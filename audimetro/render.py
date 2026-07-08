"""Render de los mensajes de Telegram de Audímetro (Markdown legacy).

Dos vistas comparten el MISMO lenguaje visual para que sean fáciles de leer:

- `render_report`  → informe diario del scheduler (totales + novedades + subidas)
- `render_stats`   → detalle bajo demanda de `/stats`

Cada pieza se pinta en dos líneas: el título clicable arriba y sus métricas
(tipo · views · engagement) debajo, sangradas. Así se ve de un vistazo qué es y
qué le pasa a cada vídeo, en vez de un churro de emojis pegados a números.

Qué dice el mensaje es dominio de Audímetro; el transporte es de radar-core.
Regla heredada: jamás emitir `/palabra` (Telegram lo pinta como comando).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from radar_core.notifiers import escape_markdown

from audimetro.contracts import Snapshot, VideoStat
from audimetro.engine import DailyReport, Delta, PlatformSummary

# (título, url, kind, snapshot) — una fila de /stats, como la da el store.
StatsRow = tuple[str, str, str, Snapshot]

# Límite duro de Telegram: 4096; margen para el markup.
_MAX_MESSAGE = 3500
_TITLE_MAX = 56
_INDENT = "     "  # sangría de la línea de métricas de cada pieza

_EMOJI = {"youtube": "▶️", "instagram": "📸", "tiktok": "🎵"}
_NAME = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
_TAG = {"youtube": "YT", "instagram": "IG", "tiktok": "TT"}
_KIND = {"video": "vídeo", "short": "short", "reel": "reel"}
_KIND_PLURAL = {"video": "vídeos", "short": "shorts", "reel": "reels"}

_DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun",
           "jul", "ago", "sep", "oct", "nov", "dic")


def _n(value: int) -> str:
    """12430 -> '12 430' (miles con espacio, estilo neutro)."""
    return f"{value:,}".replace(",", " ")


def _signed(value: int) -> str:
    return f"+{_n(value)}" if value >= 0 else f"-{_n(-value)}"


def _trend(delta: int) -> str:
    """Flecha de tendencia 24 h: ' ▲ +26' / ' ▼ -3' / '' si no cambia."""
    if delta > 0:
        return f" ▲ +{_n(delta)}"
    if delta < 0:
        return f" ▼ -{_n(-delta)}"
    return ""


def _pieces(n: int) -> str:
    return "1 pieza" if n == 1 else f"{_n(n)} piezas"


def _title_link(text: str, url: str) -> str:
    """Título truncado y saneado como texto de enlace Markdown: corchetes y
    paréntesis romperían la sintaxis del enlace (fuera); el resto se escapa."""
    text = " ".join(text.split()) or "(sin título)"
    if len(text) > _TITLE_MAX:
        text = text[: _TITLE_MAX - 1] + "…"
    for char in "[]()":
        text = text.replace(char, " ")
    return f"[«{escape_markdown(text.strip())}»]({url})"


def _title(delta: Delta) -> str:
    return _title_link(delta.stat.title, delta.stat.url)


def _tokens(pairs: tuple[tuple[int, str], ...],
            fmt: Callable[[int], str]) -> list[str]:
    """['👍 5', '💬 1'] — omite los ceros para no ensuciar la línea."""
    return [f"{icon} {fmt(v)}" for v, icon in pairs if v]


def _totals(m: VideoStat | Snapshot) -> list[str]:
    """Engagement absoluto de una pieza (para /stats y piezas nuevas)."""
    tokens = _tokens(
        ((m.likes or 0, "👍"), (m.comments or 0, "💬"),
         (m.shares or 0, "↗️"), (m.saves or 0, "🔖")),
        _n,
    )
    if m.watch_min:
        tokens.append(f"{_n(m.watch_min)} min vistos")
    if m.avg_view_s:
        tokens.append(f"⌀ {m.avg_view_s // 60}:{m.avg_view_s % 60:02d}")
    if m.subs_gained:
        tokens.append(f"{_signed(m.subs_gained)} subs")
    return tokens


def _deltas(d: Delta) -> list[str]:
    """Engagement ganado en 24 h (para piezas en movimiento)."""
    tokens = _tokens(
        ((d.d_likes, "👍"), (d.d_comments, "💬"),
         (d.d_shares, "↗️"), (d.d_saves, "🔖")),
        _signed,
    )
    if d.d_watch_min:
        tokens.append(f"{_signed(d.d_watch_min)} min vistos")
    if d.d_subs:
        tokens.append(f"{_signed(d.d_subs)} subs")
    return tokens


def _item(rank: int, tag: str, link: str, kind: str,
          views: int, trend: str, metrics: list[str]) -> list[str]:
    """Pieza en dos líneas: rango + título clicable arriba, métricas debajo."""
    head = f"{rank}. {tag}{link}"
    body = [_KIND.get(kind, kind), f"*{_n(views)}* views{trend}", *metrics]
    return [head, _INDENT + " · ".join(body)]


def _kind_counts(s: PlatformSummary) -> str:
    return " · ".join(
        f"{count} {(_KIND if count == 1 else _KIND_PLURAL).get(kind, kind)}"
        for kind, count in sorted(s.counts.items())
    ) or "0 piezas"


def _platform_block(s: PlatformSummary) -> list[str]:
    head = f"{_EMOJI.get(s.platform, '•')} *{_NAME.get(s.platform, s.platform)}*"
    if s.error is not None:
        reason = escape_markdown(s.error[:120] or "error")
        return [f"{head}  ·  ⚠️ sin datos hoy", f"{_INDENT}{reason}"]

    detail = [_kind_counts(s)]
    if s.total_watch_min is not None:
        detail.append(f"{_n(s.total_watch_min)} min vistos{_trend(s.d_watch_min)}")
        if s.d_subs:
            detail.append(f"{_signed(s.d_subs)} subs")
    if s.first_capture:
        detail.append("primera captura (mañana verás la evolución)")
        return [f"{head}  ·  *{_n(s.total_views)}* views",
                _INDENT + " · ".join(detail)]
    return [f"{head}  ·  *{_n(s.total_views)}* views{_trend(s.d_views)}",
            _INDENT + " · ".join(detail)]


def render_report(report: DailyReport, now: datetime) -> list[str]:
    header = (f"📊 *Audímetro* · {_DAYS[now.weekday()]} "
              f"{now.day} {_MONTHS[now.month - 1]}")
    lines: list[str] = [header, ""]
    for summary in report.platforms:
        lines += _platform_block(summary)
    if any(s.total_watch_min is not None for s in report.platforms):
        lines.append("_min vistos y subs: YouTube Analytics, ~48 h de retraso_")

    if report.new:
        lines += ["", "🆕 *Nuevos*"]
        for rank, delta in enumerate(report.new, 1):
            lines += _item(rank, f"{_TAG.get(delta.stat.platform, '?')} ",
                           _title(delta), delta.stat.kind, delta.stat.views,
                           "", _totals(delta.stat))
    if report.movers:
        lines += ["", "🔥 *Suben (24 h)*"]
        for rank, delta in enumerate(report.movers, 1):
            lines += _item(rank, f"{_TAG.get(delta.stat.platform, '?')} ",
                           _title(delta), delta.stat.kind, delta.stat.views,
                           _trend(delta.d_views), _deltas(delta))
        if report.more_movers:
            lines.append(f"{_INDENT}… y {_pieces(report.more_movers)} más subiendo")
    if report.unchanged:
        lines += ["", f"😴 {_pieces(report.unchanged)} sin cambios en 24 h"]
    return _split(lines)


def render_stats(
    sections: Sequence[tuple[str, Sequence[StatsRow]]], limit: int
) -> list[str]:
    """Detalle de /stats: por cada plataforma, sus piezas ordenadas por views.

    `sections` es [(plataforma, filas)] con las filas tal cual las devuelve
    `store.latest_for_platform`.
    """
    lines: list[str] = []
    for platform, rows in sections:
        if lines:
            lines.append("")
        lines += _stats_block(platform, rows, limit)
    return _split(lines) or ["sin datos"]


def _stats_block(platform: str, rows: Sequence[StatsRow], limit: int) -> list[str]:
    head = f"{_EMOJI.get(platform, '•')} *{_NAME.get(platform, platform)}*"
    if not rows:
        return [f"{head}  ·  (sin capturas aún)"]
    if len(rows) >= limit:
        # Cortadas por el tope: no sumamos views para no dar un total engañoso.
        head += f"  ·  top {len(rows)} por views"
    else:
        total = sum(snap.views for *_, snap in rows)
        head += f"  ·  {_pieces(len(rows))} · *{_n(total)}* views"
    lines = [head, ""]
    for rank, (title, url, kind, snap) in enumerate(rows, 1):
        lines += _item(rank, "", _title_link(title, url), kind,
                       snap.views, "", _totals(snap))
    return lines


def _split(lines: list[str]) -> list[str]:
    """Trocea en mensajes < _MAX_MESSAGE respetando líneas enteras."""
    messages: list[str] = []
    chunk: list[str] = []
    size = 0
    for line in lines:
        if chunk and size + len(line) + 1 > _MAX_MESSAGE:
            messages.append("\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        messages.append("\n".join(chunk))
    return messages
