"""Daemon: `python -m audimetro` (tick cada poll_interval_s; captura y envío
del informe una vez al día, ver scheduler)."""

from __future__ import annotations

import asyncio

from radar_core.runtime import run_daemon, setup_logging

from audimetro.config import Settings
from audimetro.runtime import Runtime
from audimetro.store import open_store


async def _run(cfg: Settings) -> None:
    async with open_store(cfg.db_path) as store:
        await store.init_schema()
        runtime = Runtime(cfg, store)
        background = (
            runtime.commander.background_tasks() if runtime.commander else ()
        )
        await run_daemon(
            "audimetro", runtime.run_tick, cfg.poll_interval_s,
            cleanup=runtime.aclose, background=background,
        )


def main() -> None:
    setup_logging()
    asyncio.run(_run(Settings()))


if __name__ == "__main__":
    main()
