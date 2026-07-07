"""Fuente YouTube: Data API v3 (stats públicas del canal, solo API key) +
YouTube Analytics API opcional (OAuth: min vistos, duración media y subs por
vídeo, con ~48h de retraso respecto a los contadores públicos).

La key viaja en el header X-Goog-Api-Key (regla del core: secretos jamás en
URLs); el refresh de OAuth va en el body de un POST. Clasificación
short/vídeo: heurística de duración + confirmación con un HEAD a /shorts/{id}
(200 = short, redirect a /watch = vídeo); el resultado se cachea en la tabla
videos vía known_kinds — una pieza no cambia de tipo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from radar_core.http import CircuitBreaker, get_json
from radar_core.ratelimit import TokenBucket

from audimetro.contracts import VideoStat
from audimetro.store import Store

log = logging.getLogger(__name__)

_API = "https://www.googleapis.com/youtube/v3"
_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ANALYTICS_METRICS = "estimatedMinutesWatched,averageViewDuration,subscribersGained"
# Tope de la API con dimensions=video; ordenado por min vistos, la cola sin
# datos son piezas residuales.
_ANALYTICS_MAX_ROWS = 200
# Los shorts admiten hoy hasta 3 min: por debajo de esto hay que confirmar.
_SHORT_CANDIDATE_MAX_S = 183
# Por debajo de esto, si la confirmación es inconclusa, se asume short.
_SHORT_FALLBACK_MAX_S = 62

_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$"
)


def parse_duration_s(raw: str) -> int:
    """'PT1M30S' -> 90. Formato ISO-8601 restringido de la Data API."""
    match = _DURATION_RE.match(raw or "")
    if match is None:
        return 0
    parts = {k: int(v or 0) for k, v in match.groupdict().items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def parse_analytics_report(payload: dict[str, Any]) -> dict[str, tuple[int, int, int]]:
    """Informe de Analytics → {video_id: (watch_min, avg_view_s, subs_gained)}.
    Los índices salen de columnHeaders: el orden de rows no está garantizado."""
    headers = [str(h.get("name")) for h in payload.get("columnHeaders", [])]
    try:
        idx_video = headers.index("video")
        idx_watch = headers.index("estimatedMinutesWatched")
        idx_avg = headers.index("averageViewDuration")
        idx_subs = headers.index("subscribersGained")
    except ValueError:
        log.warning("informe de Analytics sin las columnas esperadas: %s", headers)
        return {}
    return {
        str(row[idx_video]): (
            int(row[idx_watch] or 0), int(row[idx_avg] or 0), int(row[idx_subs] or 0)
        )
        for row in payload.get("rows") or []
    }


class YouTubeAnalytics:
    """Métricas OAuth por vídeo. El refresh token de Google no rota ni caduca
    (app OAuth en producción), así que vive solo en el .env — sin meta."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._client = httpx.AsyncClient(timeout=20.0)
        self._bucket = TokenBucket(2.0, 4.0)
        self._breaker = CircuitBreaker()

    async def per_video(self) -> dict[str, tuple[int, int, int]]:
        token = await self._access_token()
        payload = await get_json(
            self._client, _ANALYTICS_API,
            {
                "ids": "channel==MINE",
                "startDate": "2005-02-14",   # acumulado de toda la vida del canal
                "endDate": datetime.now(UTC).date().isoformat(),
                "metrics": _ANALYTICS_METRICS,
                "dimensions": "video",
                "sort": "-estimatedMinutesWatched",
                "maxResults": _ANALYTICS_MAX_ROWS,
            },
            self._bucket, self._breaker,
            headers={"Authorization": f"Bearer {token}"},
        )
        return parse_analytics_report(payload)

    async def _access_token(self) -> str:
        """Access token fresco por captura (1h de vida, 1 captura/día)."""
        try:
            resp = await self._client.post(_OAUTH_TOKEN_URL, data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            })
            payload = resp.json()
        except httpx.TransportError as exc:
            raise RuntimeError(f"OAuth de Google inalcanzable: {exc}") from None
        token = str(payload.get("access_token") or "")
        if not token:
            # Solo códigos de error: nunca el payload entero en un log.
            raise RuntimeError(
                "refresh OAuth de YouTube rechazado: "
                f"{payload.get('error', 'sin detalle')} — "
                f"{payload.get('error_description', '')} (si el token fue "
                "revocado, re-autoriza con `audimetro auth youtube`)"
            )
        return token

    async def aclose(self) -> None:
        await self._client.aclose()


def video_stat(item: dict[str, Any], kind: str) -> VideoStat:
    """Mapea un item de videos.list (snippet+statistics) al contrato."""
    video_id = str(item["id"])
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}

    def count(name: str) -> int | None:
        raw = statistics.get(name)
        return None if raw is None else int(raw)

    path = f"shorts/{video_id}" if kind == "short" else f"watch?v={video_id}"
    return VideoStat(
        platform="youtube",
        video_id=video_id,
        kind=kind,
        title=str(snippet.get("title") or "(sin título)"),
        url=f"https://www.youtube.com/{path}",
        published_at=snippet.get("publishedAt"),
        views=count("viewCount") or 0,
        likes=count("likeCount"),
        comments=count("commentCount"),
    )


class YouTubeSource:
    platform = "youtube"

    def __init__(self, store: Store, api_key: str, handle: str,
                 max_videos: int = 300,
                 analytics: YouTubeAnalytics | None = None) -> None:
        self._store = store
        self._headers = {"X-Goog-Api-Key": api_key}
        self._handle = handle if handle.startswith("@") else f"@{handle}"
        self._max_videos = max_videos
        self._analytics = analytics
        self._uploads: str | None = None   # no cambia nunca para un handle
        self._client = httpx.AsyncClient(timeout=20.0)
        # El probe de shorts NO debe seguir redirects: la redirección ES la señal.
        self._probe = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        self._bucket = TokenBucket(4.0, 8.0)
        self._breaker = CircuitBreaker()

    async def fetch(self) -> list[VideoStat]:
        known_kinds = await self._store.known_kinds(self.platform)
        if self._uploads is None:
            self._uploads = await self._uploads_playlist()
        ids = await self._video_ids(self._uploads)
        stats: list[VideoStat] = []
        for batch_start in range(0, len(ids), 50):
            batch = ids[batch_start:batch_start + 50]
            payload = await self._get("videos", {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
                "maxResults": 50,
            })
            for item in payload.get("items", []):
                video_id = str(item["id"])
                kind = known_kinds.get(video_id)
                if kind is None:
                    duration = parse_duration_s(
                        str((item.get("contentDetails") or {}).get("duration", ""))
                    )
                    kind = await self._classify(video_id, duration)
                stats.append(video_stat(item, kind))
        return await self._merge_analytics(stats)

    async def _merge_analytics(self, stats: list[VideoStat]) -> list[VideoStat]:
        """Fallo aislado: sin Analytics el informe sale igual, solo sin ⏱/subs
        (el engine trata los None como 'sin datos', nunca como ceros)."""
        if self._analytics is None:
            return stats
        try:
            extra = await self._analytics.per_video()
        except Exception as exc:
            log.warning("Analytics sin datos hoy (%s): informe sin min/subs", exc)
            return stats
        return [
            replace(s, watch_min=row[0], avg_view_s=row[1], subs_gained=row[2])
            if (row := extra.get(s.video_id)) is not None else s
            for s in stats
        ]

    async def _uploads_playlist(self) -> str:
        payload = await self._get("channels", {
            "part": "contentDetails", "forHandle": self._handle,
        })
        items = payload.get("items") or []
        if not items:
            raise RuntimeError(f"canal no encontrado para el handle {self._handle}")
        return str(items[0]["contentDetails"]["relatedPlaylists"]["uploads"])

    async def _video_ids(self, playlist_id: str) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < self._max_videos:
            params: dict[str, Any] = {
                "part": "contentDetails", "playlistId": playlist_id, "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            payload = await self._get("playlistItems", params)
            ids += [str(i["contentDetails"]["videoId"]) for i in payload.get("items", [])]
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return ids[: self._max_videos]

    async def _classify(self, video_id: str, duration_s: int) -> str:
        if duration_s > _SHORT_CANDIDATE_MAX_S:
            return "video"
        # Mismo bucket que la API: el día 1 puede haber decenas de probes y
        # una ráfaga sin freno invita a youtube.com a devolver 429.
        await self._bucket.acquire()
        try:
            resp = await self._probe.head(f"https://www.youtube.com/shorts/{video_id}")
        except httpx.TransportError as exc:
            log.warning("probe de shorts falló para %s (%s); heurística", video_id, exc)
            return "short" if duration_s <= _SHORT_FALLBACK_MAX_S else "video"
        if resp.status_code == 200:
            return "short"
        if resp.is_redirect and "/watch" in resp.headers.get("location", ""):
            return "video"
        # Redirect a consent u otra respuesta rara: inconcluso.
        return "short" if duration_s <= _SHORT_FALLBACK_MAX_S else "video"

    async def _get(self, resource: str, params: dict[str, Any]) -> Any:
        return await get_json(
            self._client, f"{_API}/{resource}", params,
            self._bucket, self._breaker, headers=self._headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._probe.aclose()
        if self._analytics is not None:
            await self._analytics.aclose()
