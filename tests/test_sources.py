"""Mapeo payload→contrato de cada plataforma y gestión de tokens en meta."""

from conftest import stat  # noqa: F401  (fixture side-effects del conftest)

from audimetro.sources import instagram, tiktok
from audimetro.sources.instagram import parse_insights, reel_stat
from audimetro.sources.tiktok import tiktok_stat
from audimetro.sources.youtube import parse_analytics_report, parse_duration_s, video_stat
from audimetro.store import Store


def test_youtube_duration_parse() -> None:
    assert parse_duration_s("PT1M30S") == 90
    assert parse_duration_s("PT45S") == 45
    assert parse_duration_s("PT1H2M3S") == 3723
    assert parse_duration_s("P1DT1S") == 86401
    assert parse_duration_s("") == 0
    assert parse_duration_s("garbage") == 0


def test_youtube_video_stat_mapping() -> None:
    item = {
        "id": "abc123",
        "snippet": {"title": "Mi vídeo", "publishedAt": "2026-07-01T10:00:00Z"},
        "statistics": {"viewCount": "1234", "likeCount": "56", "commentCount": "7"},
    }
    s = video_stat(item, "video")
    assert s.video_id == "abc123" and s.views == 1234
    assert s.likes == 56 and s.comments == 7
    assert s.url == "https://www.youtube.com/watch?v=abc123"
    assert video_stat(item, "short").url == "https://www.youtube.com/shorts/abc123"


def test_youtube_hidden_likes_are_none_not_zero() -> None:
    item = {"id": "x", "snippet": {"title": "t"}, "statistics": {"viewCount": "10"}}
    s = video_stat(item, "video")
    assert s.likes is None and s.comments is None and s.views == 10


def test_analytics_report_parsing_uses_headers_not_positions() -> None:
    payload = {
        "columnHeaders": [
            {"name": "video"}, {"name": "subscribersGained"},
            {"name": "estimatedMinutesWatched"}, {"name": "averageViewDuration"},
        ],
        "rows": [["abc123", 3, 500, 83], ["def456", 0, 50, 41]],
    }
    parsed = parse_analytics_report(payload)
    assert parsed["abc123"] == (500, 83, 3)
    assert parsed["def456"] == (50, 41, 0)


def test_analytics_report_degrades_on_unexpected_shape() -> None:
    assert parse_analytics_report({}) == {}
    assert parse_analytics_report({"columnHeaders": [{"name": "video"}]}) == {}


def test_instagram_reel_mapping() -> None:
    media = {
        "id": "178001",
        "caption": "Primera línea\nsegunda línea",
        "media_product_type": "REELS",
        "permalink": "https://www.instagram.com/reel/xyz/",
        "timestamp": "2026-07-01T10:00:00+0000",
        "like_count": 40,
        "comments_count": 3,
    }
    s = reel_stat(media, {"views": 812, "shares": 2, "saved": 5})
    assert s.kind == "reel" and s.title == "Primera línea"
    assert s.views == 812 and s.likes == 40 and s.comments == 3
    assert s.shares == 2 and s.saves == 5


def test_instagram_reel_degrades_without_insights() -> None:
    media = {"id": "1", "media_product_type": "REELS",
             "permalink": "https://ig/x", "like_count": 4}
    s = reel_stat(media, {})
    assert s.views == 0 and s.likes == 4 and s.title == "(sin título)"


def test_tiktok_mapping() -> None:
    video = {
        "id": "7300000001",
        "title": "Truco de Y",
        "create_time": 1782000000,
        "share_url": "https://www.tiktok.com/@user/video/7300000001",
        "view_count": 21900, "like_count": 850, "comment_count": 12,
        "share_count": 30,
    }
    s = tiktok_stat(video)
    assert s.platform == "tiktok" and s.views == 21900 and s.shares == 30
    assert s.published_at is not None and s.published_at.startswith("2026-")


def test_tiktok_empty_title() -> None:
    s = tiktok_stat({"id": "1", "share_url": "https://t/x"})
    assert s.title == "(sin título)" and s.views == 0


def test_parse_insights_distinguishes_missing_from_zero() -> None:
    assert parse_insights(None) is None
    assert parse_insights({"data": []}) is None
    assert parse_insights(
        {"data": [{"name": "views", "values": [{"value": 0}]}]}
    ) == {"views": 0}


async def test_instagram_new_seed_wins_over_meta(store: Store) -> None:
    from radar_core.store import utcnow_str

    async def no_refresh(token: str) -> str | None:
        return None  # sin red en tests

    source = instagram.InstagramSource(store, "seed-v1")
    source._try_refresh = no_refresh  # type: ignore[method-assign]
    await store.set_meta(instagram.SEED_KEY, "seed-v1")
    await store.set_meta(instagram.TOKEN_KEY, "refreshed-old")
    await store.set_meta(instagram.REFRESHED_KEY, utcnow_str())
    # Misma semilla: la copia refrescada de meta manda.
    assert await source._current_token() == "refreshed-old"

    # Semilla NUEVA en .env (re-autorización): destrona a la copia de meta.
    source2 = instagram.InstagramSource(store, "seed-v2")
    source2._try_refresh = no_refresh  # type: ignore[method-assign]
    assert await source2._current_token() == "seed-v2"
    assert await store.get_meta(instagram.SEED_KEY) == "seed-v2"
    await source.aclose()
    await source2.aclose()


async def test_tiktok_new_seed_resets_tokens(store: Store) -> None:
    import time

    source = tiktok.TikTokSource(store, "key", "secret", "seed-v1")
    await store.set_meta(tiktok.SEED_KEY, "seed-v1")
    await store.set_meta(tiktok.ACCESS_KEY, "access-vivo")
    await store.set_meta(tiktok.ACCESS_EXPIRES_KEY, str(time.time() + 86400))
    # Misma semilla y access vigente: no toca nada.
    assert await source._access_token() == "access-vivo"

    # Semilla nueva: invalida access/refresh de meta y fuerza refresh.
    async def fake_refresh() -> str:
        return "access-nuevo"  # sin red en tests

    source2 = tiktok.TikTokSource(store, "key", "secret", "seed-v2")
    source2._refresh = fake_refresh  # type: ignore[method-assign]
    assert await source2._access_token() == "access-nuevo"
    assert await store.get_meta(tiktok.SEED_KEY) == "seed-v2"
    assert await store.get_meta(tiktok.REFRESH_KEY) == "seed-v2"
    assert await store.get_meta(tiktok.ACCESS_KEY) == ""
    await source.aclose()
    await source2.aclose()
