"""Wiring: overrides en caliente y comandos de dominio."""

from conftest import cfg, stat

from audimetro.runtime import OVERRIDES, Runtime
from audimetro.store import Store


def test_override_whitelist_excludes_secrets_and_cadence() -> None:
    assert set(OVERRIDES) == {"digest_hour", "min_delta_views", "top_movers"}


async def test_no_credentials_means_no_sources_and_console(store: Store) -> None:
    runtime = Runtime(cfg(), store)
    assert runtime.sources == []
    assert runtime.commander is None
    await runtime.aclose()


async def test_hora_command_sets_override(store: Store) -> None:
    runtime = Runtime(cfg(), store)
    commands = runtime._domain_commands()
    reply = await commands["hora"]("7")
    assert "07:00 UTC" in reply
    assert (await store.get_overrides())["digest_hour"] == "7"
    assert "uso:" in await commands["hora"]("25")
    assert "uso:" in await commands["hora"]("pronto")
    await runtime.aclose()


async def test_stats_command_lists_pieces(store: Store) -> None:
    await store.record(
        [stat(video_id="a", views=99, likes=5, title="Corto famoso", kind="short"),
         stat(platform="tiktok", video_id="t1", views=10, title="TT")],
        "2026-07-06 08:00:00",
    )
    runtime = Runtime(cfg(), store)
    commands = runtime._domain_commands()
    reply = await commands["stats"]("yt 5")
    assert "youtube" in reply and "Corto famoso — 99 views · 5👍" in reply
    assert "tiktok" not in reply
    reply_all = await commands["stats"]("")
    # Sin fuentes configuradas, /stats sin filtro no tiene qué listar.
    assert reply_all == "sin fuentes configuradas"
    await runtime.aclose()


async def test_status_summary(store: Store) -> None:
    await store.record([stat()], "2026-07-06 08:00:00")
    runtime = Runtime(cfg(), store)
    status = await runtime._status()
    assert "último informe: nunca" in status
    assert "youtube: 1" in status
    await runtime.aclose()
