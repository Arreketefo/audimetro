"""Contratos estables del dominio de audiencias."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PLATFORMS = ("youtube", "instagram", "tiktok")


@dataclass(frozen=True)
class VideoStat:
    """Foto de las métricas públicas de una pieza en el momento de la captura."""

    platform: str                # 'youtube' | 'instagram' | 'tiktok'
    video_id: str
    kind: str                    # 'video' | 'short' | 'reel'
    title: str
    url: str
    published_at: str | None     # ISO 8601 (como lo da la plataforma)
    views: int
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None    # solo IG/TikTok
    saves: int | None = None     # solo IG
    # Solo YouTube con Analytics (OAuth): llegan con ~48h de retraso.
    watch_min: int | None = None     # minutos vistos acumulados
    avg_view_s: int | None = None    # duración media de visualización (s)
    subs_gained: int | None = None   # suscriptores atribuidos al vídeo


@dataclass(frozen=True)
class Snapshot:
    """Última captura persistida de una pieza (para calcular deltas)."""

    views: int
    likes: int | None
    comments: int | None
    shares: int | None
    saves: int | None
    captured_at: str
    watch_min: int | None = None
    avg_view_s: int | None = None
    subs_gained: int | None = None


class Source(Protocol):
    """Una plataforma: devuelve la foto de hoy de todas sus piezas."""

    platform: str

    async def fetch(self) -> list[VideoStat]: ...
