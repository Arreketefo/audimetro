from conftest import stat

from audimetro.contracts import Snapshot
from audimetro.engine import SourceResult, compute


def snap(views: int, likes: int | None = 0, comments: int | None = 0,
         watch_min: int | None = None, subs_gained: int | None = None) -> Snapshot:
    return Snapshot(views=views, likes=likes, comments=comments,
                    shares=None, saves=None, captured_at="2026-07-05 08:00:00",
                    watch_min=watch_min, subs_gained=subs_gained)


def test_first_capture_has_no_new_or_movers() -> None:
    report = compute(
        [SourceResult("youtube", [stat(views=100), stat(video_id="v2", views=5)])],
        previous={}, had_history=set(), min_delta_views=10, top_movers=5,
    )
    assert report.new == [] and report.movers == []
    summary = report.platforms[0]
    assert summary.first_capture and summary.total_views == 105
    assert summary.counts == {"video": 2}


def test_deltas_new_movers_and_unchanged() -> None:
    results = [SourceResult("youtube", [
        stat(video_id="mover", views=300, likes=20),
        stat(video_id="quiet", views=101),
        stat(video_id="nuevo", views=50),
    ])]
    previous = {
        ("youtube", "mover"): snap(100, likes=5),
        ("youtube", "quiet"): snap(100),
    }
    report = compute(results, previous, {"youtube"},
                     min_delta_views=10, top_movers=5)
    assert [d.stat.video_id for d in report.new] == ["nuevo"]
    assert [d.stat.video_id for d in report.movers] == ["mover"]
    assert report.movers[0].d_views == 200
    assert report.movers[0].d_likes == 15
    assert report.unchanged == 1
    # El total de plataforma suma también las views de las piezas nuevas.
    assert report.platforms[0].d_views == 200 + 1 + 50


def test_movers_sorted_and_capped() -> None:
    stats = [stat(video_id=f"v{i}", views=100 + i * 10) for i in range(1, 5)]
    previous = {("youtube", f"v{i}"): snap(10) for i in range(1, 5)}
    report = compute([SourceResult("youtube", stats)], previous, {"youtube"},
                     min_delta_views=10, top_movers=2)
    assert [d.stat.video_id for d in report.movers] == ["v4", "v3"]
    assert report.more_movers == 2


def test_analytics_aggregates_and_deltas() -> None:
    results = [SourceResult("youtube", [
        stat(video_id="a", views=200, watch_min=500, subs_gained=10),
        stat(video_id="b", views=100, watch_min=50, subs_gained=1),
        stat(video_id="c", views=50),          # sin fila de Analytics (>top 200)
    ])]
    previous = {
        ("youtube", "a"): snap(150, watch_min=460, subs_gained=7),
        ("youtube", "b"): snap(90, watch_min=50, subs_gained=1),
        ("youtube", "c"): snap(40),
    }
    report = compute(results, previous, {"youtube"}, min_delta_views=10, top_movers=5)
    summary = report.platforms[0]
    assert summary.total_watch_min == 550
    assert summary.d_watch_min == 40 and summary.d_subs == 3


def test_analytics_enabled_later_does_not_fake_deltas() -> None:
    """El día que se activa Analytics, el acumulado histórico no es un delta."""
    results = [SourceResult("youtube", [stat(views=200, watch_min=5000)])]
    previous = {("youtube", "v1"): snap(150)}   # ayer sin watch_min
    report = compute(results, previous, {"youtube"}, min_delta_views=10, top_movers=5)
    assert report.platforms[0].total_watch_min == 5000
    assert report.platforms[0].d_watch_min == 0
    assert report.movers[0].d_watch_min == 0


def test_no_analytics_means_none_not_zero() -> None:
    report = compute([SourceResult("youtube", [stat()])], {}, {"youtube"},
                     min_delta_views=10, top_movers=5)
    assert report.platforms[0].total_watch_min is None


def test_failed_source_is_reported_not_fatal() -> None:
    report = compute(
        [SourceResult("tiktok", error="boom"),
         SourceResult("youtube", [stat(views=100)])],
        previous={}, had_history={"youtube"}, min_delta_views=10, top_movers=5,
    )
    tiktok, youtube = report.platforms
    assert tiktok.error == "boom"
    assert youtube.error is None
    assert [d.stat.video_id for d in report.new] == ["v1"]
