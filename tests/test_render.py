from datetime import UTC, datetime

from conftest import stat

from audimetro.engine import DailyReport, Delta, PlatformSummary
from audimetro.render import render_report

NOW = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)  # lunes


def delta(video_id: str = "v1", d_views: int = 200, is_new: bool = False,
          title: str = "Mi título", platform: str = "youtube",
          d_saves: int = 0) -> Delta:
    return Delta(stat=stat(platform=platform, video_id=video_id, views=1234,
                           title=title),
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
    assert message.startswith("📊 Audímetro — lun 6 jul")
    assert "▶️ YouTube  12 430 views (+320) · 2 shorts, 3 vídeos" in message


def test_error_and_first_capture_lines() -> None:
    report = DailyReport(
        [summary(platform="tiktok", error="circuit open"),
         summary(platform="instagram", first_capture=True, counts={"reel": 4},
                 total_views=100, d_views=0)],
        [], [], 0, 0,
    )
    [message] = render_report(report, NOW)
    assert "🎵 TikTok  ⚠️ sin datos hoy (circuit open)" in message
    assert "📸 Reels IG  100 views · 4 reels (primera captura" in message


def test_new_and_movers_sections() -> None:
    report = DailyReport(
        [summary()],
        new=[delta(video_id="n1", is_new=True, title="Nuevo short")],
        movers=[delta(video_id="m1", d_views=1010, title="Truco de Y")],
        more_movers=3, unchanged=22,
    )
    [message] = render_report(report, NOW)
    assert "🆕 Nuevos" in message
    assert "[«Nuevo short»](https://example.com/n1) — 1 234 views" in message
    assert "🔥 Movimiento 24h" in message
    assert "[«Truco de Y»](https://example.com/m1) +1 010 views · +3👍" in message
    assert "… y 3 más en movimiento" in message
    assert "(sin cambios: 22 piezas)" in message


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
    assert "550⏱ (+40) · +3 subs" in message
    assert "(⏱ min vistos y subs: Analytics, ~48 h de retraso)" in message


def test_no_analytics_no_footnote() -> None:
    [message] = render_report(DailyReport([summary()], [], [], 0, 0), NOW)
    assert "Analytics" not in message and "⏱" not in message


def test_ig_saves_shown_in_movers() -> None:
    report = DailyReport(
        [summary()],
        new=[],
        movers=[delta(platform="instagram", d_views=60, d_saves=4,
                      title="Reel W")],
        more_movers=0, unchanged=0,
    )
    [message] = render_report(report, NOW)
    assert "+4🔖" in message


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
