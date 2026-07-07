"""Tick de Audímetro: captura diaria + envío del informe.

El daemon tickea cada poll_interval_s, pero la captura/envío ocurre UNA vez al
día (primer tick con hour >= digest_hour). El resto de ticks solo refrescan la
marca de frescura del healthcheck. El fallo de una plataforma no tumba a las
demás: el informe sale igual con una línea de aviso.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from radar_core.store import utcnow_str

from audimetro.config import Settings
from audimetro.contracts import Source
from audimetro.engine import SourceResult, compute
from audimetro.render import render_report
from audimetro.store import Store

log = logging.getLogger(__name__)

LAST_TICK_KEY = "last_tick_at"
SENT_DATE_KEY = "digest_sent_date"

_QUERY_STRING_RE = re.compile(r"\?[^\s'\")]*")


def _sanitize(message: str) -> str:
    """Los errores de httpx llevan la URL completa; una query string puede
    arrastrar un token — jamás al informe ni al log de alto nivel."""
    return _QUERY_STRING_RE.sub("?…", message)


class TextSender(Protocol):
    """Lo mínimo que el tick necesita de un notificador (Telegram o consola)."""

    async def send_text(self, text: str, markdown: bool = True) -> None: ...


@dataclass
class TickStats:
    captured: int = 0            # piezas capturadas
    sources_failed: int = 0
    messages_sent: int = 0
    skipped: bool = False        # tick sin captura (fuera de hora / ya enviado)


async def tick(
    cfg: Settings,
    store: Store,
    sources: list[Source],
    sender: TextSender,
    force: bool = False,
) -> TickStats:
    stats = TickStats()
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    # Clamp heredado del core: fuera de 0-23, `now.hour < hora` sería siempre
    # cierto y el informe moriría en silencio con healthcheck verde.
    digest_hour = min(max(cfg.digest_hour, 0), 23)
    if not force:
        already_sent = await store.get_meta(SENT_DATE_KEY) == today
        if now.hour < digest_hour or already_sent:
            stats.skipped = True
            await store.set_meta(LAST_TICK_KEY, utcnow_str())
            return stats

    previous = await store.latest_snapshots()
    had_history = await store.platforms_with_history()
    captured_at = utcnow_str()

    async def capture(source: Source) -> SourceResult:
        try:
            fetched = await source.fetch()
            # aiosqlite serializa todas las operaciones en su worker: grabar
            # desde tasks concurrentes es seguro.
            await store.record(fetched, captured_at)
            stats.captured += len(fetched)
            return SourceResult(platform=source.platform, stats=fetched)
        except Exception as exc:
            stats.sources_failed += 1
            log.exception("fuente %s falló", source.platform)
            return SourceResult(platform=source.platform, error=_sanitize(str(exc)))

    # Plataformas independientes (hosts, buckets y breakers propios): en
    # paralelo el tick dura lo que la más lenta, no la suma de las tres.
    results = list(await asyncio.gather(*(capture(s) for s in sources)))

    report = compute(results, previous, had_history,
                     cfg.min_delta_views, cfg.top_movers)
    messages = render_report(report, now)

    # At-most-once (disciplina del core): el día queda marcado ANTES de enviar
    # — un fallo parcial de Telegram no provoca duplicados en el retick.
    await store.set_meta(SENT_DATE_KEY, today)
    for message in messages:
        try:
            try:
                await sender.send_text(message)
            except Exception:
                # Red de seguridad: si algún resto de markup rompe el parse de
                # Telegram, mejor el informe en texto plano que ninguno.
                await sender.send_text(message, markdown=False)
            stats.messages_sent += 1
        except Exception:
            log.exception("envío del informe falló (mensaje %d/%d)",
                          stats.messages_sent + 1, len(messages))
            break

    pruned = await store.prune(cfg.snapshot_retention_days)
    await store.set_meta(LAST_TICK_KEY, utcnow_str())
    log.info("captura diaria: %d piezas, %d fuentes caídas, %d mensajes, %d snapshots purgados",
             stats.captured, stats.sources_failed, stats.messages_sent, pruned)
    return stats
