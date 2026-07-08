from datetime import UTC, datetime

from conftest import stat

from audimetro.contracts import Snapshot
from audimetro.engine import DailyReport, Delta, PlatformSummary
from audimetro.render import render_report, render_stats

NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)  # lunes


def delta(video_id: str = "v1", d_views: int = 200, is_new: bool = False,
          title: str = "Mi título", platform: str = "youtube", kind: str = "short",
          views: int = 1234, d_saves: int = 0) -> Delta:
    return Delta(stat=stat(platform=platform, video_id=video_id, views=views,
                           title=title, kind=kind),
                 is_new=is_new, d_views=d_views, d_likes=3, d_comments=0,
                 d_shares=0, d_saves=d_saves)


def summary(**kw: object) -> PlatformSummary:
    base: dict[str, object] = {
        "platform": "youtube", "error": None, "first_capture": False,
        "total_views": 12430, "d_views": 320, "counts": {"video": 3, "short": 2},
    }
    base.update(kw)
    return PlatformSummary(**base)  # type: ignore[arg-type]


def test_header_and_platform_line() -> None:
    report = DailyReport([summary()], [], [], 0, 0)
    [message] = render_report(report, NOW)
    assert message.startswith("📊 *Audímetro* · lun 6 jul")
    assert "▶️ *YouTube*  ·  *12 430* views ▲ +320" in message
    assert "2 shorts · 3 vídeos" in message


def test_kind_counts_singular_and_plural() -> None:
    report = DailyReport(
        [summary(counts={"short": 1, "video": 2})], [], [], 0, 0
    )
    [message] = render_report(report, NOW)
    assert "1 short · 2 vídeos" in message  # singular, no "1 shorts"


def test_flat_platform_has_no_trend_arrow() -> None:
    [message] = render_report(DailyReport([summary(d_views=0)], [], [], 0, 0), NOW)
    assert "*12 430* views\n" in message and "▲" not in message


def test_error_and_first_capture_lines() -> None:
    report = DailyReport(
        [summary(platform="tiktok", error="circuit open"),
         summary(platform="instagram", first_capture=True, counts={"reel": 4},
                 total_views=100, d_views=0)],
        [], [], 0, 0,
    )
    [message] = render_report(report, NOW)
    assert "🎵 *TikTok*  ·  ⚠️ sin datos hoy" in message
    assert "circuit open" in message
    assert "📸 *Instagram*  ·  *100* views" in message
    assert "4 reels · primera captura" in message


def test_new_and_movers_sections() -> None:
    report = DailyReport(
        [summary()],
        new=[delta(video_id="n1", is_new=True, title="Nuevo short")],
        movers=[delta(video_id="m1", d_views=1010, views=5000, title="Truco de Y")],
        more_movers=3, unchanged=22,
    )
    [message] = render_report(report, NOW)
    assert "🆕 *Nuevos*" in message
    assert "1. YT [«Nuevo short»](https://example.com/n1)" in message
    assert "short · *1 234* views" in message           # totales de la pieza nueva
    assert "🔥 *Suben (24 h)*" in message
    assert "1. YT [«Truco de Y»](https://example.com/m1)" in message
    assert "short · *5 000* views ▲ +1 010 · 👍 +3" in message  # total + delta 24h
    assert "… y 3 piezas más subiendo" in message
    assert "😴 22 piezas sin cambios en 24 h" in message


def test_markdown_metacharacters_neutralized_in_titles() -> None:
    report = DailyReport(
        [summary()],
        new=[delta(is_new=True, title="Trucos [top] _raros_ (2026)")],
        movers=[], more_movers=0, unchanged=0,
    )
    [message] = render_report(report, NOW)
    # Corchetes/paréntesis fuera (romperían el enlace); _ se escapa, no se pierde.
    assert "[«Trucos  top  \\_raros\\_  2026»](https://example.com/v1)" in message


def test_analytics_in_platform_line_and_footnote() -> None:
    report = DailyReport(
        [summary(total_watch_min=550, d_watch_min=40, d_subs=3)], [], [], 0, 0
    )
    [message] = render_report(report, NOW)
    assert "550 min vistos ▲ +40 · +3 subs" in message
    assert "_min vistos y subs: YouTube Analytics, ~48 h de retraso_" in message


def test_no_analytics_no_footnote() -> None:
    [message] = render_report(DailyReport([summary()], [], [], 0, 0), NOW)
    assert "Analytics" not in message and "min vistos" not in message


def test_ig_saves_shown_in_movers() -> None:
    report = DailyReport(
        [summary()],
        new=[],
        movers=[delta(platform="instagram", kind="reel", d_views=60, d_saves=4,
                      title="Reel W")],
        more_movers=0, unchanged=0,
    )
    [message] = render_report(report, NOW)
    assert "🔖 +4" in message


def test_singular_unchanged_and_more_movers() -> None:
    report = DailyReport(
        [summary()],
        new=[], movers=[delta()], more_movers=1, unchanged=1,
    )
    [message] = render_report(report, NOW)
    assert "… y 1 pieza más subiendo" in message
    assert "😴 1 pieza sin cambios en 24 h" in message


def test_error_text_is_markdown_escaped() -> None:
    report = DailyReport(
        [summary(platform="tiktok", error="refresh_token caducado")], [], [], 0, 0
    )
    [message] = render_report(report, NOW)
    assert "refresh\\_token caducado" in message


def test_long_reports_split_into_multiple_messages() -> None:
    movers = [delta(video_id=f"v{i}", title=f"Título bastante largo nº {i}" * 3)
              for i in range(200)]
    report = DailyReport([summary()], [], movers, 0, 0)
    messages = render_report(report, NOW)
    assert len(messages) > 1
    assert all(len(m) <= 3500 for m in messages)


# --- /stats -----------------------------------------------------------------

def snap(views: int, likes: int | None = None, saves: int | None = None,
         watch_min: int | None = None) -> Snapshot:
    return Snapshot(views=views, likes=likes, comments=None, shares=None,
                    saves=saves, captured_at="2026-07-06 08:00:00",
                    watch_min=watch_min)


def test_stats_header_totals_and_clickable_items() -> None:
    rows = [("Doritos", "https://example.com/a", "short", snap(478, likes=5)),
            ("Levers", "https://example.com/b", "short", snap(418, likes=2))]
    [message] = render_stats([("youtube", rows)], limit=15)
    assert message.startswith("▶️ *YouTube*  ·  2 piezas · *896* views")
    assert "1. [«Doritos»](https://example.com/a)" in message
    assert "short · *478* views · 👍 5" in message
    assert "2. [«Levers»](https://example.com/b)" in message


def test_stats_marks_truncation_instead_of_fake_total() -> None:
    rows = [(f"t{i}", f"https://example.com/{i}", "reel", snap(10)) for i in range(5)]
    [message] = render_stats([("instagram", rows)], limit=5)
    assert "📸 *Instagram*  ·  top 5 por views" in message
    assert "piezas ·" not in message  # sin total engañoso al estar cortado


def test_stats_empty_platform() -> None:
    [message] = render_stats([("tiktok", [])], limit=15)
    assert message == "🎵 *TikTok*  ·  (sin capturas aún)"
