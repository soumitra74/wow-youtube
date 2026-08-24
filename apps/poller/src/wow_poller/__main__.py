from __future__ import annotations

import argparse
import sys

from wow_poller.pipeline import run_poll


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll YouTube channels and summarize new videos.")
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Re-attempt videos previously marked as error.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max new videos to attempt this run (0 = unlimited). Defaults to POLL_LIMIT.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Skip RSS items older than this many days (0 = no age filter). Defaults to POLL_MAX_AGE_DAYS.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Process the full RSS backlog: no per-run limit and no age filter.",
    )
    args = parser.parse_args(argv)
    if args.seed:
        return run_poll(retry_errors=args.retry_errors, limit=0, max_age_days=0)
    return run_poll(
        retry_errors=args.retry_errors,
        limit=args.limit,
        max_age_days=args.max_age_days,
    )


if __name__ == "__main__":
    sys.exit(main())
