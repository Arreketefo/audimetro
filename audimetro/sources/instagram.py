"""Fuente Instagram: reels vía "Instagram API with Instagram Login".

Token largo (60 días): la semilla llega por .env (AUDIMETRO_IG_ACCESS_TOKEN,
salida de `audimetro auth instagram`); el vigente vive en meta y se
auto-refresca en cada captura (>=24h de edad). Si la semilla del .env cambia
(re-autorización), gana sobre la copia de meta — sin eso, un token muerto en
la DB sería irrecuperable sin cirugía manual. El refresh usa el token como
query param porque la API lo exige: el error HTTP se sanea para que el token
jamás acabe en un log (httpx incluye la URL completa en sus excepciones).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from radar_core.http import CircuitBreaker, get_json
from radar_core.ratelimit import TokenBucket
from radar_core.store import TIMESTAMP_FMT, BaseStore, utcnow_str

from audimetro.contracts import VideoStat

log = logging.getLogger(__name__)

_GRAPH = "https://graph.instagram.com/v23.0"
_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

TOKEN_KEY = "ig_access_token"
SEED_KEY = "ig_token_seed"
REFRESHED_KEY = "ig_token_refreshed_at"
TOKEN_TTL_DAYS = 60

_MEDIA_FIELDS = ("id,caption,media_type,media_product_type,permalink,"
                 "timestamp,like_count,comments_count")
_INSIGHT_METRICS = "views,shares,saved"
# Expansión de campo: los insights llegan con el propio listado de media
# (una llamada por página en vez de una por reel).
_MEDIA_FIELDS_EXPANDED = f"{_MEDIA_FIELDS},insights.metric({_INSIGHT_METRICS})"


def parse_insights(payload: dict[str, Any] | None) -> dict[str, int] | None:
    """{'data': [{'name': 'views', 'values': [{'value': 812}]}, ...]} → dict.
    None si no hay datos (distinto de {'views': 0}, que es un cero real)."""
    if not payload or not payload.get("data"):
        return None
    return {
        str(m["name"]): int((m.get("values") or [{}])[0].get("value") or 0)
        for m in payload["data"]
    }


def reel_stat(media: dict[str, Any], insights: dict[str, int]) -> VideoStat:
    caption = str(media.get("caption") or "").strip()
    title = caption.splitlines()[0] if caption else "(sin título)"
    return VideoStat(
        platform="instagram",
        video_id=str(media["id"]),
        kind="reel",
        title=title,
        url=str(media.get("permalink") or ""),
        published_at=media.get("timestamp"),
        views=insights.get("views", 0),
        likes=None if media.get("like_count") is None else int(media["like_count"]),
        comments=(None if media.get("comments_count") is None
                  else int(media["comments_count"])),
        shares=insights.get("shares"),
        saves=insights.get("saved"),
    )


class InstagramSource:
    platform = "instagram"

    def __init__(self, store: BaseStore, seed_token: str) -> None:
        self._store = store
        self._seed = seed_token
        self._expansion_ok = True
        self._client = httpx.AsyncClient(timeout=20.0)
        self._bucket = TokenBucket(2.0, 4.0)
        self._breaker = CircuitBreaker()

    async def fetch(self) -> list[VideoStat]:
        token = await self._current_token()
        stats: list[VideoStat] = []
        async for media in self._media_pages(token):
            if media.get("media_product_type") != "REELS":
                continue
            insights = parse_insights(media.get("insights"))
            if insights is None:
                insights = await self._insights(str(media["id"]), token)
            if insights is None:
                # Sin views de hoy, mejor NO grabar la pieza: un views=0
                # falso pintaría un desplome hoy y un pico fantasma mañana.
                log.warning("reel %s sin insights hoy: omitido", media.get("id"))
                continue
            stats.append(reel_stat(media, insights))
        return stats

    async def _media_pages(self, token: str) -> AsyncIterator[dict[str, Any]]:
        url: str | None = f"{_GRAPH}/me/media"
        fields = _MEDIA_FIELDS_EXPANDED if self._expansion_ok else _MEDIA_FIELDS
        params: dict[str, Any] = {"fields": fields, "limit": 50}
        while url:
            try:
                payload = await get_json(
                    self._client, url, params, self._bucket, self._breaker,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and self._expansion_ok:
                    # Cuenta/media donde la expansión de insights no cuela:
                    # se reempieza sin ella (caerá al plan B por pieza). Un
                    # segundo 400 ya no es la expansión y se propaga.
                    self._expansion_ok = False
                    url = f"{_GRAPH}/me/media"
                    params = {"fields": _MEDIA_FIELDS, "limit": 50}
                    continue
                raise
            for media in payload.get("data", []):
                yield media
            # paging.next es una URL completa (trae ya fields/limit/after).
            url = (payload.get("paging") or {}).get("next")
            params = {}

    async def _insights(self, media_id: str, token: str) -> dict[str, int] | None:
        """Plan B por pieza cuando la expansión no vino: None = sin datos."""
        for metrics in (_INSIGHT_METRICS, "views"):
            try:
                payload = await get_json(
                    self._client, f"{_GRAPH}/{media_id}/insights",
                    {"metric": metrics}, self._bucket, self._breaker,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and metrics != "views":
                    continue  # algún metric no soportado: reintenta solo views
                log.warning("insights de %s fallaron (HTTP %d)",
                            media_id, exc.response.status_code)
                return None
            return parse_insights(payload) or {}
        return None

    async def _current_token(self) -> str:
        stored_seed = await self._store.get_meta(SEED_KEY)
        if stored_seed != self._seed:
            # Semilla nueva en el .env (re-autorización): manda sobre meta.
            await self._store.set_meta(SEED_KEY, self._seed)
            await self._store.set_meta(TOKEN_KEY, self._seed)
            await self._store.set_meta(REFRESHED_KEY, "")
            log.info("token IG re-sembrado desde el .env")
        token = await self._store.get_meta(TOKEN_KEY) or self._seed
        if await self._refresh_due():
            refreshed = await self._try_refresh(token)
            if refreshed is not None:
                token = refreshed
        return token

    async def _refresh_due(self) -> bool:
        raw = await self._store.get_meta(REFRESHED_KEY)
        if not raw:
            return True
        last = datetime.strptime(raw, TIMESTAMP_FMT).replace(tzinfo=UTC)
        return (datetime.now(UTC) - last).total_seconds() >= 86400

    async def _try_refresh(self, token: str) -> str | None:
        try:
            resp = await self._client.get(_REFRESH_URL, params={
                "grant_type": "ig_refresh_token", "access_token": token,
            })
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            # Solo el status: el mensaje de httpx lleva la URL con el token.
            # Un token de <24h no es refrescable: aviso, no error.
            log.warning("refresh del token IG rechazado (HTTP %d)",
                        exc.response.status_code)
            return None
        except httpx.TransportError as exc:
            log.warning("refresh del token IG inalcanzable (%s)", exc)
            return None
        new_token = str(payload.get("access_token") or "")
        if not new_token:
            log.warning("refresh del token IG sin access_token en la respuesta")
            return None
        await self._store.set_meta(TOKEN_KEY, new_token)
        await self._store.set_meta(REFRESHED_KEY, utcnow_str())
        log.info("token IG refrescado (60 días más)")
        return new_token

    async def token_days_left(self) -> int | None:
        """Días de vida que le quedan al token, si consta un refresh."""
        raw = await self._store.get_meta(REFRESHED_KEY)
        if not raw:
            return None
        refreshed = datetime.strptime(raw, TIMESTAMP_FMT).replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - refreshed).total_seconds() / 86400
        return max(0, int(TOKEN_TTL_DAYS - age_days))

    async def aclose(self) -> None:
        await self._client.aclose()
