#!/usr/bin/env python
"""Long-running scheduler for the daily digest.

Sleeps, then asks daily_digest who is due. The once-a-day stamp in the database
is what actually prevents duplicates, so restarting or scaling this process can
never mail the same digest twice — this loop only has to be alive.

    python scripts/daily_digest_worker.py
    python scripts/daily_digest_worker.py --interval 900 --run-once
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.daily_digest import main as run_due  # noqa: E402

log = logging.getLogger("daily_digest_worker")

DEFAULT_INTERVAL_SECONDS = 15 * 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the digest scheduler loop.")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help="Seconds between checks (default 900).")
    parser.add_argument("--run-once", action="store_true", help="Run a single pass and exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    interval = max(30, int(args.interval))

    log.info("digest worker starting; checking every %ss", interval)
    while True:
        try:
            run_due([])
        except Exception:
            # A bad pass must not kill the scheduler; the next one retries.
            log.exception("a digest pass failed")
        if args.run_once:
            return 0
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("digest worker stopping")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())