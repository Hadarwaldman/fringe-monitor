#!/usr/bin/env python3
"""Local CLI to validate the PlanMyFringe scrape without AWS.

Reads credentials from output/planmyfringe-creds.json (or the file named by
PLANMYFRINGE_CREDS_FILE, or the PLANMYFRINGE_CREDS env var — JSON
{"user_id": "...", "password": "..."}), logs in, scrapes the schedule and
wishlist, and prints the parsed result as JSON. Optionally matches against a
local scan output to preview the watchlist import.

Usage:
    python sync_planmyfringe.py                     # scrape + parse only
    python sync_planmyfringe.py --latest output/latest.json   # + matching
    python sync_planmyfringe.py --raw               # dump fetched page info
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "backend")

from fringe_lib.planmyfringe import (  # noqa: E402
    fetch_planner_data,
    get_planmyfringe_credentials,
    sync_planner,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest",
        help="Path to a local latest.json scan output to test show matching",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print which pages were fetched alongside the parsed entries",
    )
    args = parser.parse_args()

    creds = get_planmyfringe_credentials()
    if creds is None:
        print(
            "No credentials found. Create output/planmyfringe-creds.json with\n"
            '{"user_id": "...", "password": "..."} (the file is gitignored).',
            file=sys.stderr,
        )
        return 1

    if args.latest:
        with open(args.latest, encoding="utf-8") as fh:
            latest = json.load(fh)
        result = sync_planner(credentials=creds, latest=latest)
        print(json.dumps(result, indent=2))
    else:
        data = fetch_planner_data(creds)
        if not args.raw:
            data.pop("debug", None)
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
