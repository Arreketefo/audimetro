"""Render del informe diario a mensajes de Telegram (Markdown legacy).

Qué dice el mensaje es dominio de Audímetro; el transporte es de radar-core.
Regla heredada: jamás emitir `/palabra` (Telegram lo pinta como comando).
"""

from __future__ import annotations

from datetime import datetime

from radar_core.notifiers import escape_markdown

from audimetro.engine import DailyReport, Delta, PlatformSummary

# Límite duro de Telegram: 4096; margen para el markup.
_MAX_MESSAGE = 3500
_TITLE_MAX = 48

_EMOJI = {"youtube": "▶️", "instagram": "📸", "tiktok": "🎵"}
_NAME = {"youtube": "YouTube", "instagram": "Reels IG", "tiktok": "TikTok"}
_TAG = {"youtube": "YT", "instagram": "IG", "tiktok": "TT"}
_KIND_PLURAL = {"video": "vídeos", "short": "shorts", "reel": "reels"}

_DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun",
           "jul", "ago", "sep", "oct", "nov", "dic")


def _n(value: int) -> str:
    """12430 -> '12 430' (miles con espacio, estilo neutro)."""
    return f"{value:,}".replace(",", " ")


def _signed(value: int) -> str:
    return f"+{_n(value)}" if value >= 0 else f"-{_n(-value)}"


def _title(delta: Delta) -> str:
    """Título truncado y saneado como texto de enlace Markdown: corchetes y
    paréntesis romperían la sintaxis del enlace (fuera); el resto se escapa."""
    text = " ".join(delta.stat.title.split()) or "(sin título)"
    if len(text) > _TITLE_MAX:
        text = text[: _TITLE_MAX - 1] + "…"
    for char in "[]()":
        text = text.replace(char, " ")
    return f"[«{escape_markdown(text.strip())}»]({delta.stat.url})"


def _kind_counts(summary: PlatformSummary) -> str:
    return ", ".join(
        f"{count} {_KIND_PLURAL.get(kind, kind)}"
        for kind, count in sorted(summary.counts.items())
    ) or "0 piezas"


def _analytics_suffix(s: PlatformSummary) -> str:
    if s.total_watch_min is None:
        return ""
    suffix = f" · {_n(s.total_watch_min)}⏱"
    if not s.first_capture:
        suffix += f" ({_signed(s.d_watch_min)})"
        if s.d_subs:
            suffix += f" · {_signed(s.d_subs)} subs"
    return suffix


def _platform_line(s: PlatformSummary) -> str:
    head = f"{_EMOJI.get(s.platform, '•')} {_NAME.get(s.platform, s.platform)}"
    if s.error is not None:
        reason = escape_markdown(s.error[:120] or "error")
        return f"{head}  ⚠️ sin datos hoy ({reason})"
    if s.first_capture:
        return (f"{head}  {_n(s.total_views)} views · {_kind_counts(s)}"
                f"{_analytics_suffix(s)} (primera captura: mañana habrá deltas)")
    return (f"{head}  {_n(s.total_views)} views ({_signed(s.d_views)}) · "
            f"{_kind_counts(s)}{_analytics_suffix(s)}")


def _engagement(delta: Delta, signed: bool) -> str:
    if signed:
        deltas = ((delta.d_likes, "👍"), (delta.d_comments, "💬"),
                  (delta.d_shares, "↗️"), (delta.d_saves, "🔖"),
                  (delta.d_watch_min, "⏱"), (delta.d_subs, " subs"))
        return " · ".join(f"{_signed(v)}{icon}" for v, icon in deltas if v)
    totals = ((delta.stat.likes or 0, "👍"), (delta.stat.comments or 0, "💬"),
              (delta.stat.shares or 0, "↗️"), (delta.stat.saves or 0, "🔖"))
    return " · ".join(f"{_n(v)}{icon}" for v, icon in totals if v)


def render_report(report: DailyReport, now: datetime) -> list[str]:
    header = (f"📊 Audímetro — {_DAYS[now.weekday()]} "
              f"{now.day} {_MONTHS[now.month - 1]}")
    lines: list[str] = [header]
    lines += [_platform_line(s) for s in report.platforms]
    if any(s.total_watch_min is not None for s in report.platforms):
        lines.append("(⏱ min vistos y subs: Analytics, ~48 h de retraso)")

    if report.new:
        lines.append("")
        lines.append("🆕 Nuevos")
        for delta in report.new:
            extra = _engagement(delta, signed=False)
            lines.append(
                f"  {_TAG.get(delta.stat.platform, '?')} {_title(delta)} — "
                f"{_n(delta.stat.views)} views" + (f" · {extra}" if extra else "")
            )
    if report.movers:
        lines.append("")
        lines.append("🔥 Movimiento 24h")
        for delta in report.movers:
            extra = _engagement(delta, signed=True)
            lines.append(
                f"  {_TAG.get(delta.stat.platform, '?')} {_title(delta)} "
                f"{_signed(delta.d_views)} views" + (f" · {extra}" if extra else "")
            )
        if report.more_movers:
            lines.append(f"  … y {report.more_movers} más en movimiento")
    if report.unchanged:
        lines.append("")
        lines.append(f"(sin cambios: {report.unchanged} piezas)")
    return _split(lines)


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
