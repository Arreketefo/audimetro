"""Wiring de Audímetro: fuentes + notificador + control por Telegram."""

from __future__ import annotations

import asyncio
import logging

from radar_core.botcontrol import (
    BotCommander,
    DomainHandler,
    Override,
    apply_overrides,
    int_range,
    skip_tick_if_paused,
)
from radar_core.notifiers import TelegramTransport

from audimetro.config import Settings
from audimetro.contracts import Source
from audimetro.scheduler import LAST_TICK_KEY, SENT_DATE_KEY, TickStats, tick
from audimetro.sources.instagram import InstagramSource
from audimetro.sources.tiktok import TikTokSource
from audimetro.sources.youtube import YouTubeAnalytics, YouTubeSource
from audimetro.store import Store

log = logging.getLogger(__name__)

_PLATFORM_ALIASES = {
    "yt": "youtube", "youtube": "youtube",
    "ig": "instagram", "instagram": "instagram", "reels": "instagram",
    "tt": "tiktok", "tiktok": "tiktok",
}

# Ajustables por Telegram en caliente (radar-ops §7.4). Fuera: tokens/keys,
# db_path y la cadencia del tick (protección de rate limits).
OVERRIDES: dict[str, Override] = {
    "digest_hour": Override("digest_hour", int_range(0, 23),
                            "hora UTC del informe diario"),
    "min_delta_views": Override("min_delta_views", int_range(0, 1_000_000),
                                "delta mínimo de views para salir en movimiento"),
    "top_movers": Override("top_movers", int_range(1, 50),
                           "tope de piezas en movimiento"),
}


class ConsoleSender:
    """Fallback sin Telegram: el informe acaba en el log (modo dev)."""

    async def send_text(self, text: str, markdown: bool = True) -> None:
        log.info("INFORME |\n%s", text)


class Runtime:
    def __init__(self, cfg: Settings, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.sources = self._build_sources(cfg, store)
        # Serializa TODAS las entradas al tick (scheduler, /informe, CLI): el
        # tick_lock del daemon solo cubre los ticks programados.
        self._tick_lock = asyncio.Lock()
        self.commander: BotCommander | None = None
        if cfg.telegram_bot_token and cfg.telegram_chat_id:
            self.sender: TelegramTransport | ConsoleSender = TelegramTransport(
                cfg.telegram_bot_token, cfg.telegram_chat_id
            )
            self.commander = BotCommander(
                cfg.telegram_bot_token,
                {str(cfg.telegram_chat_id)},
                store,
                "audimetro",
                OVERRIDES,
                domain_commands=self._domain_commands(),
                status_provider=self._status,
            )
        else:
            self.sender = ConsoleSender()
            log.warning("sin AUDIMETRO_TELEGRAM_BOT_TOKEN/_CHAT_ID: "
                        "el informe diario solo saldrá por el log")

    @staticmethod
    def _build_sources(cfg: Settings, store: Store) -> list[Source]:
        sources: list[Source] = []
        if cfg.youtube_api_key and cfg.youtube_handle:
            analytics = None
            oauth_bits = (cfg.yt_oauth_client_id, cfg.yt_oauth_client_secret,
                          cfg.yt_refresh_token)
            if all(oauth_bits):
                analytics = YouTubeAnalytics(
                    cfg.yt_oauth_client_id or "", cfg.yt_oauth_client_secret or "",
                    cfg.yt_refresh_token or "",
                )
            elif any(oauth_bits):
                log.warning("OAuth de YouTube a medias: faltan claves o el "
                            "refresh token (`audimetro auth youtube`) — "
                            "informe sin min vistos/subs")
            sources.append(YouTubeSource(
                store, cfg.youtube_api_key, cfg.youtube_handle,
                cfg.youtube_max_videos, analytics=analytics,
            ))
        else:
            log.warning("YouTube desactivado: falta AUDIMETRO_YOUTUBE_API_KEY "
                        "o AUDIMETRO_YOUTUBE_HANDLE")
        if cfg.ig_access_token:
            sources.append(InstagramSource(store, cfg.ig_access_token))
        else:
            log.warning("Instagram desactivado: falta AUDIMETRO_IG_ACCESS_TOKEN "
                        "(ejecuta `audimetro auth instagram`)")
        if cfg.tt_client_key and cfg.tt_client_secret and cfg.tt_refresh_token:
            sources.append(TikTokSource(
                store, cfg.tt_client_key, cfg.tt_client_secret, cfg.tt_refresh_token
            ))
        else:
            log.warning("TikTok desactivado: faltan AUDIMETRO_TT_CLIENT_KEY/"
                        "_CLIENT_SECRET/_REFRESH_TOKEN (`audimetro auth tiktok`)")
        return sources

    async def run_tick(self, force: bool = False) -> TickStats:
        async with self._tick_lock:
            cfg = apply_overrides(self.cfg, await self.store.get_overrides(), OVERRIDES)
            if not force and await skip_tick_if_paused(self.store, LAST_TICK_KEY):
                return TickStats(skipped=True)
            return await tick(cfg, self.store, self.sources, self.sender, force=force)

    def _domain_commands(self) -> dict[str, DomainHandler]:
        async def stats(args: str) -> str:
            parts = args.split()
            limit = 15
            if parts and parts[-1].isdigit():
                limit = min(int(parts.pop()), 50)
            wanted = [_PLATFORM_ALIASES[p.lower()] for p in parts
                      if p.lower() in _PLATFORM_ALIASES]
            platforms = wanted or [s.platform for s in self.sources]
            if not platforms:
                return "sin fuentes configuradas"
            lines: list[str] = []
            for platform in platforms:
                rows = await self.store.latest_for_platform(platform, limit)
                lines.append(f"{platform} (top {len(rows)} por views):")
                if not rows:
                    lines.append("  (sin capturas aún)")
                for title, _url, kind, snap in rows:
                    short_title = title if len(title) <= 40 else title[:39] + "…"
                    extras = "".join(
                        f" · {value}{icon}"
                        for value, icon in ((snap.likes, "👍"), (snap.comments, "💬"),
                                            (snap.shares, "↗️"), (snap.saves, "🔖"),
                                            (snap.watch_min, "⏱"),
                                            (snap.subs_gained, " subs"))
                        if value
                    )
                    if snap.avg_view_s:
                        extras += f" · ⌀{snap.avg_view_s // 60}:{snap.avg_view_s % 60:02d}"
                    lines.append(f"  [{kind}] {short_title} — {snap.views} views{extras}")
            return "\n".join(lines)

        async def hora(args: str) -> str:
            # Azúcar sobre /set digest_hour: mismo validador, cero deriva.
            try:
                value = OVERRIDES["digest_hour"].parse(args.strip())
            except (ValueError, TypeError):
                return "uso: /hora <0-23> (hora UTC del informe diario)"
            await self.store.set_override("digest_hour", str(value))
            return f"informe diario a las {int(str(value)):02d}:00 UTC ✓ (aplica mañana)"

        async def informe(args: str) -> str:
            stats_run = await self.run_tick(force=True)
            return (f"informe forzado: {stats_run.captured} piezas, "
                    f"{stats_run.sources_failed} fuentes caídas, "
                    f"{stats_run.messages_sent} mensajes "
                    "(sustituye al informe programado de hoy)")

        return {"stats": stats, "hora": hora, "informe": informe}

    async def _status(self) -> str:
        last_sent = await self.store.get_meta(SENT_DATE_KEY) or "nunca"
        counts = await self.store.piece_counts()
        pieces = ", ".join(f"{p}: {n}" for p, n in sorted(counts.items())) or "sin piezas"
        active = ", ".join(s.platform for s in self.sources) or "NINGUNA"
        bits = [f"fuentes: {active}", f"último informe: {last_sent}", pieces]
        for source in self.sources:
            if isinstance(source, InstagramSource):
                days = await source.token_days_left()
                if days is not None:
                    bits.append(f"token IG: ~{days} días"
                                + (" ⚠️ RENOVAR" if days <= 7 else ""))
        return " · ".join(bits)

    async def aclose(self) -> None:
        components: list[object] = [self.commander, self.sender, *self.sources]
        for component in components:
            aclose = getattr(component, "aclose", None)
            if aclose is not None:
                await aclose()
