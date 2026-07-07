from datetime import UTC, datetime

from conftest import cfg, stat

from audimetro.contracts import VideoStat
from audimetro.scheduler import LAST_TICK_KEY, SENT_DATE_KEY, tick
from audimetro.store import Store


class FakeSource:
    def __init__(self, platform: str, stats: list[VideoStat] | None = None,
                 boom: bool = False) -> None:
        self.platform = platform
        self._stats = stats or []
        self._boom = boom
        self.calls = 0

    async def fetch(self) -> list[VideoStat]:
        self.calls += 1
        if self._boom:
            raise RuntimeError("api caída")
        return self._stats


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, text: str, markdown: bool = True) -> None:
        self.messages.append(text)


async def test_skips_before_digest_hour(store: Store) -> None:
    hour_later = (datetime.now(UTC).hour + 1) % 24
    source = FakeSource("youtube", [stat()])
    sender = FakeSender()
    result = await tick(cfg(digest_hour=hour_later), store, [source], sender)
    if hour_later != 0:  # a las 23 UTC el "+1" cruza la medianoche: no aplica
        assert result.skipped and source.calls == 0 and sender.messages == []
        assert await store.get_meta(LAST_TICK_KEY) is not None


async def test_sends_once_per_day(store: Store) -> None:
    source = FakeSource("youtube", [stat()])
    sender = FakeSender()
    first = await tick(cfg(digest_hour=0), store, [source], sender)
    assert not first.skipped and first.messages_sent == 1
    assert await store.get_meta(SENT_DATE_KEY) == datetime.now(UTC).strftime("%Y-%m-%d")

    second = await tick(cfg(digest_hour=0), store, [source], sender)
    assert second.skipped and source.calls == 1 and len(sender.messages) == 1


async def test_force_ignores_guards(store: Store) -> None:
    source = FakeSource("youtube", [stat()])
    sender = FakeSender()
    await tick(cfg(digest_hour=0), store, [source], sender)
    result = await tick(cfg(digest_hour=0), store, [source], sender, force=True)
    assert not result.skipped and source.calls == 2 and len(sender.messages) == 2


async def test_failed_source_does_not_block_report(store: Store) -> None:
    ok = FakeSource("youtube", [stat()])
    broken = FakeSource("tiktok", boom=True)
    sender = FakeSender()
    result = await tick(cfg(digest_hour=0), store, [ok, broken], sender)
    assert result.sources_failed == 1 and result.captured == 1
    [message] = sender.messages
    assert "⚠️ sin datos hoy" in message and "YouTube" in message
    # La captura de la fuente sana se persistió aunque la otra fallara.
    assert ("youtube", "v1") in await store.latest_snapshots()


async def test_out_of_range_digest_hour_is_clamped(store: Store) -> None:
    """Una hora fuera de 0-23 en el .env no puede romper la guardia horaria:
    -5 se recorta a 0 (dispara siempre); 24 se recorta a 23 (dispara a las
    23:00 en vez de nunca — el lado alto no es testeable sin congelar el reloj)."""
    source = FakeSource("youtube", [stat()])
    sender = FakeSender()
    result = await tick(cfg(digest_hour=-5), store, [source], sender)
    assert not result.skipped and sender.messages


async def test_markdown_failure_falls_back_to_plain_text(store: Store) -> None:
    class PickySender(FakeSender):
        async def send_text(self, text: str, markdown: bool = True) -> None:
            if markdown:
                raise RuntimeError("400 can't parse entities")
            await super().send_text(text, markdown)

    sender = PickySender()
    result = await tick(cfg(digest_hour=0), store,
                        [FakeSource("youtube", [stat()])], sender, force=True)
    assert result.messages_sent == 1 and len(sender.messages) == 1


async def test_second_day_reports_deltas(store: Store) -> None:
    sender = FakeSender()
    await tick(cfg(digest_hour=0), store, [FakeSource("youtube", [stat(views=100)])],
               sender, force=True)
    await tick(cfg(digest_hour=0, min_delta_views=10), store,
               [FakeSource("youtube", [stat(views=250)])], sender, force=True)
    assert "(+150)" in sender.messages[1]
    assert "🔥 Movimiento 24h" in sender.messages[1]
