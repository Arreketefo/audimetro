"""Fuente TikTok: Display API v2 (video.list del propio usuario).

Tokens: access de 24h + refresh de 365 días. La semilla del refresh llega por
.env (AUDIMETRO_TT_REFRESH_TOKEN, salida de `audimetro auth tiktok`); los
vigentes viven en meta (TikTok puede rotar el refresh token en cada uso). Si
la semilla del .env cambia (re-autorización), gana sobre la copia de meta. El
endpoint de token exige form-encoding, así que va con httpx directo y errores
saneados (nunca el body con secretos en un log).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from radar_core.http import CircuitBreaker, post_json
from radar_core.ratelimit import TokenBucket
from radar_core.store import BaseStore

from audimetro.contracts import VideoStat

log = logging.getLogger(__name__)

_API = "https://open.tiktokapis.com/v2"
_VIDEO_FIELDS = "id,title,create_time,share_url,view_count,like_count,comment_count,share_count"

ACCESS_KEY = "tt_access_token"
ACCESS_EXPIRES_KEY = "tt_access_expires_at"    # epoch (str)
REFRESH_KEY = "tt_refresh_token"
SEED_KEY = "tt_refresh_seed"
_EXPIRY_MARGIN_S = 300
_MAX_PAGES = 100                               # 20 vídeos/página: tope holgado


def tiktok_stat(video: dict[str, Any]) -> VideoStat:
    create_time = video.get("create_time")
    published = (
        datetime.fromtimestamp(int(create_time), tz=UTC).isoformat()
        if create_time else None
    )
    return VideoStat(
        platform="tiktok",
        video_id=str(video["id"]),
        kind="video",
        title=str(video.get("title") or "").strip() or "(sin título)",
        url=str(video.get("share_url") or ""),
        published_at=published,
        views=int(video.get("view_count") or 0),
        likes=None if video.get("like_count") is None else int(video["like_count"]),
        comments=(None if video.get("comment_count") is None
                  else int(video["comment_count"])),
        shares=None if video.get("share_count") is None else int(video["share_count"]),
    )


class TikTokSource:
    platform = "tiktok"

    def __init__(
        self, store: BaseStore, client_key: str, client_secret: str,
        seed_refresh_token: str,
    ) -> None:
        self._store = store
        self._client_key = client_key
        self._client_secret = client_secret
        self._seed_refresh = seed_refresh_token
        self._client = httpx.AsyncClient(timeout=20.0)
        self._bucket = TokenBucket(2.0, 4.0)
        self._breaker = CircuitBreaker()

    async def fetch(self) -> list[VideoStat]:
        token = await self._access_token()
        stats: list[VideoStat] = []
        cursor: int | None = None
        for _page in range(_MAX_PAGES):
            body: dict[str, Any] = {"max_count": 20}
            if cursor is not None:
                body["cursor"] = cursor
            payload = await post_json(
                self._client, f"{_API}/video/list/?fields={_VIDEO_FIELDS}",
                body, self._bucket, self._breaker,
                headers={"Authorization": f"Bearer {token}"},
            )
            error = payload.get("error") or {}
            if error.get("code") not in (None, "ok"):
                raise RuntimeError(
                    f"video.list: {error.get('code')} — {error.get('message', '')}"
                )
            data = payload.get("data") or {}
            stats += [tiktok_stat(v) for v in data.get("videos", [])]
            next_cursor = data.get("cursor")
            # Guardas anti-bucle: sin cursor nuevo o cursor que no avanza, se
            # corta con lo capturado en vez de colgar el tick para siempre.
            if (not data.get("has_more") or next_cursor is None
                    or int(next_cursor) == cursor):
                return stats
            cursor = int(next_cursor)
        log.warning("video.list superó %d páginas: captura truncada", _MAX_PAGES)
        return stats

    async def _access_token(self) -> str:
        stored_seed = await self._store.get_meta(SEED_KEY)
        if stored_seed != self._seed_refresh:
            # Semilla nueva en el .env (re-autorización): manda sobre meta.
            await self._store.set_meta(SEED_KEY, self._seed_refresh)
            await self._store.set_meta(REFRESH_KEY, self._seed_refresh)
            await self._store.set_meta(ACCESS_KEY, "")
            await self._store.set_meta(ACCESS_EXPIRES_KEY, "")
            log.info("refresh token TikTok re-sembrado desde el .env")
        access = await self._store.get_meta(ACCESS_KEY)
        expires = await self._store.get_meta(ACCESS_EXPIRES_KEY)
        if access and expires and time.time() < float(expires) - _EXPIRY_MARGIN_S:
            return access
        return await self._refresh()

    async def _refresh(self) -> str:
        refresh_token = await self._store.get_meta(REFRESH_KEY) or self._seed_refresh
        try:
            resp = await self._client.post(f"{_API}/oauth/token/", data={
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            })
            payload = resp.json()
        except httpx.TransportError as exc:
            raise RuntimeError(f"token TikTok inalcanzable: {exc}") from None
        access = str(payload.get("access_token") or "")
        if not access:
            # Nunca el payload entero: puede llevar tokens. error/description
            # de este endpoint son códigos cortos sin secretos.
            raise RuntimeError(
                "refresh del token TikTok rechazado: "
                f"{payload.get('error', 'sin detalle')} — "
                f"{payload.get('error_description', '')} (si el refresh token "
                "caducó, re-autoriza con `audimetro auth tiktok`)"
            )
        expires_at = time.time() + int(payload.get("expires_in") or 86400)
        await self._store.set_meta(ACCESS_KEY, access)
        await self._store.set_meta(ACCESS_EXPIRES_KEY, str(expires_at))
        new_refresh = str(payload.get("refresh_token") or "")
        if new_refresh:
            await self._store.set_meta(REFRESH_KEY, new_refresh)
        log.info("token TikTok refrescado")
        return access

    async def aclose(self) -> None:
        await self._client.aclose()
