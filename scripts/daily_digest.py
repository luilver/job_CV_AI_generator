#!/usr/bin/env python
"""Run the daily LinkedIn discovery pass for every eligible account.

This is the scheduled entry point. It is deliberately dumb: it decides *who* is
due, runs each account independently, and never lets one account's failure stop
the rest. The digest-hour and once-a-day rules live in store.digest_is_due() so
the manual "Run now" button can share them without obeying them.

    python scripts/daily_digest.py                 # everyone due now
    python scripts/daily_digest.py --user-id 1     # one account, regardless of hour
    python scripts/daily_digest.py --dry-run       # list who would run
    python scripts/daily_digest.py --all           # ignore the once-a-day guard

Exit codes: 0 all good, 1 at least one account failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.auth.db import get_connection, init_db  # noqa: E402
from src.discovery.matcher import run_discovery  # noqa: E402
from src.linkedin.store import digest_is_due, get_settings  # noqa: E402

log = logging.getLogger("daily_digest")


def eligible_user_ids() -> list[int]:
    """Every account with discovery switched on."""
    init_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT user_id FROM discovery_settings WHERE enabled = 1 ORDER BY user_id"
        ).fetchall()
    finally:
        conn.close()
    return [int(row["user_id"]) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily job-discovery digest.")
    parser.add_argument("--user-id", type=int, action="append", default=None,
                        help="Run this account now, ignoring the digest hour and the daily guard. Repeatable.")
    parser.add_argument("--all", action="store_true",
                        help="Run every enabled account now, ignoring the daily guard.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would run and exit without calling any AI.")
    parser.add_argument("--no-digest", action="store_true",
                        help="Score and prepare materials but do not email.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.user_id:
        targets = list(dict.fromkeys(args.user_id))
    else:
        targets = eligible_user_ids()

    if not targets:
        log.info("No accounts have discovery enabled.")
        return 0

    failures = 0
    for user_id in targets:
        force = args.user_id is not None or args.all
        if not force:
            due, reason = digest_is_due(user_id)
            if not due:
                log.info("user %s: skipped — %s", user_id, reason)
                continue

        settings = get_settings(user_id)
        if args.dry_run:
            log.info(
                "user %s: would run (threshold %s%%, llm %s/day, gen %s/day)",
                user_id, settings["min_match_score"], settings["daily_llm_budget"],
                settings["daily_gen_budget"],
            )
            continue

        log.info("user %s: running discovery", user_id)
        try:
            report = run_discovery(user_id, send_digest=not args.no_digest)
        except Exception:
            failures += 1
            log.exception("user %s: the run crashed", user_id)
            continue

        log.info(
            "user %s: found=%s read=%s reviewed=%s matched=%s kits=%s%s",
            user_id, report.get("found"), report.get("hydrated"), report.get("scored"),
            report.get("matches"), report.get("kits"),
            " (digest sent)" if report.get("digest_sent") else "",
        )
        if report.get("reason"):
            log.info("user %s: %s", user_id, report["reason"])
        for message in report.get("errors", []):
            log.warning("user %s: %s", user_id, message)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())