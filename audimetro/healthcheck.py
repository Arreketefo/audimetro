from __future__ import annotations

import sys

from radar_core.runtime import healthcheck_main

from audimetro.config import Settings
from audimetro.scheduler import LAST_TICK_KEY


def main() -> int:
    cfg = Settings()
    return healthcheck_main(cfg.db_path, LAST_TICK_KEY, cfg.poll_interval_s)


if __name__ == "__main__":
    sys.exit(main())
