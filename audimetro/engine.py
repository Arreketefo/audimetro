"""Cálculo del informe diario: deltas 24h, novedades y movers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from audimetro.contracts import Snapshot, VideoStat


@dataclass(frozen=True)
class SourceResult:
    """Salida (o fallo) de una plataforma en la captura de hoy."""

    platform: str
    stats: list[VideoStat] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class Delta:
    stat: VideoStat
    is_new: bool
    d_views: int
    d_likes: int
    d_comments: int
    d_shares: int
    d_saves: int
    d_watch_min: int = 0            # solo con Analytics; 0 sin histórico previo
    d_subs: int = 0


@dataclass(frozen=True)
class PlatformSummary:
    platform: str
    error: str | None               # None = captura OK
    first_capture: bool
    total_views: int
    d_views: int
    counts: dict[str, int]          # kind -> nº de piezas
    total_watch_min: int | None = None   # None = sin datos de Analytics
    d_watch_min: int = 0
    d_subs: int = 0


@dataclass(frozen=True)
class DailyReport:
    platforms: list[PlatformSummary]
    new: list[Delta]
    movers: list[Delta]
    more_movers: int                # movers por encima del tope (no mostrados)
    unchanged: int                  # piezas por debajo del umbral


def _delta(stat: VideoStat, prev: Snapshot | None) -> Delta:
    def diff(now: int | None, before: int | None) -> int:
        return (now or 0) - (before or 0)

    def diff_both(now: int | None, before: int | None) -> int:
        """Delta solo con AMBOS lados presentes: al activar Analytics más
        tarde, el acumulado histórico no puede salir como delta gigante."""
        return now - before if now is not None and before is not None else 0

    return Delta(
        stat=stat,
        is_new=prev is None,
        d_views=stat.views - (prev.views if prev else 0),
        d_likes=diff(stat.likes, prev.likes if prev else None),
        d_comments=diff(stat.comments, prev.comments if prev else None),
        d_shares=diff(stat.shares, prev.shares if prev else None),
        d_saves=diff(stat.saves, prev.saves if prev else None),
        d_watch_min=diff_both(stat.watch_min, prev.watch_min if prev else None),
        d_subs=diff_both(stat.subs_gained, prev.subs_gained if prev else None),
    )


def compute(
    results: list[SourceResult],
    previous: Mapping[tuple[str, str], Snapshot],
    had_history: set[str],
    min_delta_views: int,
    top_movers: int,
) -> DailyReport:
    platforms: list[PlatformSummary] = []
    new: list[Delta] = []
    changed: list[Delta] = []
    unchanged = 0

    for result in results:
        if result.error is not None:
            platforms.append(PlatformSummary(
                platform=result.platform, error=result.error,
                first_capture=False, total_views=0, d_views=0, counts={},
            ))
            continue
        first_capture = result.platform not in had_history
        counts: dict[str, int] = {}
        total_views = 0
        platform_d_views = 0
        total_watch: int | None = None
        watch_delta = 0
        subs_delta = 0
        for stat in result.stats:
            counts[stat.kind] = counts.get(stat.kind, 0) + 1
            total_views += stat.views
            if stat.watch_min is not None:
                total_watch = (total_watch or 0) + stat.watch_min
            delta = _delta(stat, previous.get((stat.platform, stat.video_id)))
            platform_d_views += delta.d_views
            watch_delta += delta.d_watch_min
            subs_delta += delta.d_subs
            if first_capture:
                continue  # el día 1 todo sería "nuevo": solo cuenta el total
            if delta.is_new:
                new.append(delta)
            elif delta.d_views >= min_delta_views:
                changed.append(delta)
            else:
                unchanged += 1
        platforms.append(PlatformSummary(
            platform=result.platform, error=None,
            first_capture=first_capture, total_views=total_views,
            d_views=0 if first_capture else platform_d_views, counts=counts,
            total_watch_min=total_watch,
            d_watch_min=0 if first_capture else watch_delta,
            d_subs=0 if first_capture else subs_delta,
        ))

    new.sort(key=lambda d: d.stat.views, reverse=True)
    changed.sort(key=lambda d: d.d_views, reverse=True)
    movers = changed[:top_movers]
    return DailyReport(
        platforms=platforms,
        new=new,
        movers=movers,
        more_movers=len(changed) - len(movers),
        unchanged=unchanged,
    )
