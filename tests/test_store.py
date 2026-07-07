from conftest import stat

from audimetro.store import Store


async def test_record_and_latest_snapshots(store: Store) -> None:
    await store.record([stat(views=100), stat(video_id="v2", views=50)],
                       "2026-07-05 08:00:00")
    await store.record([stat(views=150)], "2026-07-06 08:00:00")

    latest = await store.latest_snapshots()
    assert latest[("youtube", "v1")].views == 150
    assert latest[("youtube", "v1")].captured_at == "2026-07-06 08:00:00"
    assert latest[("youtube", "v2")].views == 50


async def test_known_kinds_and_history(store: Store) -> None:
    assert await store.platforms_with_history() == set()
    await store.record([stat(kind="short"), stat(platform="tiktok", video_id="t1")],
                       "2026-07-06 08:00:00")
    assert await store.known_kinds("youtube") == {"v1": "short"}
    assert await store.platforms_with_history() == {"youtube", "tiktok"}


async def test_video_metadata_updates(store: Store) -> None:
    await store.record([stat(title="Antes")], "2026-07-05 08:00:00")
    await store.record([stat(title="Después", views=1)], "2026-07-06 08:00:00")
    rows = await store.latest_for_platform("youtube")
    assert len(rows) == 1
    title, _url, kind, snap = rows[0]
    assert title == "Después"
    assert kind == "video"
    assert snap.views == 1


async def test_latest_for_platform_orders_by_views(store: Store) -> None:
    await store.record(
        [stat(video_id="a", views=10), stat(video_id="b", views=99)],
        "2026-07-06 08:00:00",
    )
    rows = await store.latest_for_platform("youtube")
    assert [r[3].views for r in rows] == [99, 10]


async def test_init_schema_migrates_pre_analytics_snapshots(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Una DB creada antes de la fase Analytics gana las columnas nuevas."""
    from audimetro.store import open_store

    db = str(tmp_path / "old.db")
    async with open_store(db) as s:
        await s._conn.executescript(
            """
            CREATE TABLE snapshots (
                platform TEXT NOT NULL, video_id TEXT NOT NULL,
                captured_at TEXT NOT NULL, views INTEGER NOT NULL,
                likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
                PRIMARY KEY (platform, video_id, captured_at)
            );
            """
        )
        await s._conn.commit()
        await s.init_schema()
        await s.record([stat(watch_min=500, avg_view_s=83, subs_gained=3)],
                       "2026-07-06 08:00:00")
        latest = await s.latest_snapshots()
        assert latest[("youtube", "v1")].watch_min == 500
        assert latest[("youtube", "v1")].subs_gained == 3


async def test_prune_keeps_recent(store: Store) -> None:
    await store.record([stat()], "2020-01-01 00:00:00")
    await store.record([stat(views=200)], "2026-07-06 08:00:00")
    pruned = await store.prune(30)
    assert pruned == 1
    latest = await store.latest_snapshots()
    assert latest[("youtube", "v1")].views == 200
