from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from audimetro.config import Settings
from audimetro.contracts import VideoStat
from audimetro.store import Store, open_store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    async with open_store(str(tmp_path / "test.db")) as s:
        await s.init_schema()
        yield s


def cfg(**kw: object) -> Settings:
    """Settings de test: apantalla credenciales del entorno/.env local."""
    base: dict[str, object] = {
        "db_path": ":memory:",
        "telegram_bot_token": None, "telegram_chat_id": None,
        "youtube_api_key": None, "ig_access_token": None,
        "tt_client_key": None,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def stat(
    platform: str = "youtube",
    video_id: str = "v1",
    kind: str = "video",
    views: int = 100,
    likes: int | None = 10,
    comments: int | None = 2,
    shares: int | None = None,
    saves: int | None = None,
    title: str = "Un vídeo",
    watch_min: int | None = None,
    avg_view_s: int | None = None,
    subs_gained: int | None = None,
) -> VideoStat:
    return VideoStat(
        platform=platform, video_id=video_id, kind=kind, title=title,
        url=f"https://example.com/{video_id}", published_at="2026-07-01T00:00:00Z",
        views=views, likes=likes, comments=comments, shares=shares, saves=saves,
        watch_min=watch_min, avg_view_s=avg_view_s, subs_gained=subs_gained,
    )
