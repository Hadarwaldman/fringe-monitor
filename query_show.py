#!/usr/bin/env python3
"""
Live availability lookup for specific shows.

Where `scan_fringe.py` walks the whole programme (~4 min), this asks the box
office about one show right now (~2 s). It resolves the show from the most
recent scan — cached locally or in S3 — then re-checks each performance with a
direct `performancePrices` call, so the numbers are live even though the
resolution is cached.

Three transports, because who can reach edfringe depends on where you are:

  --via direct   No proxy. Only works from a residential IP; a datacenter IP
                 gets a Cloudflare 403.
  --via proxy    Route through the residential proxy in SSM (the default) —
                 this is what the Lambdas do.
  --via remote   Don't touch edfringe at all; POST to the deployed API and let
                 the Lambda do the lookup. The fallback for hosts that can
                 reach neither edfringe nor the proxy directly.

Examples
--------
    ./query_show.py cathy --date 2026-08-15
    ./query_show.py "one man musical" --via direct
    ./query_show.py roleplay --start 2026-08-15 --end 2026-08-17 --json
    ./query_show.py deepfake --via remote          # from a locked-down box
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

from backend.fringe_lib.client import FringeClient, make_async_client
from backend.fringe_lib.live import (
    check_box_office_ids,
    match_shows,
    summarize,
    window_performances,
)

DATA_BUCKET_PREFIX = "fringe-monitor-data-"
LATEST_KEY = "data/latest.json"
LOCAL_LATEST = "output/latest.json"

STATUS_MARK = {
    "sold_out": "SOLD OUT",
    "nearly_sold_out": "nearly",
    "available": "available",
}


# --------------------------------------------------------------------------
# Loading the cached scan (for resolution only — availability is always live)
# --------------------------------------------------------------------------


def _find_data_bucket(s3) -> str:
    bucket = os.environ.get("DATA_BUCKET")
    if bucket:
        return bucket
    for entry in s3.list_buckets()["Buckets"]:
        if entry["Name"].startswith(DATA_BUCKET_PREFIX):
            return entry["Name"]
    raise RuntimeError(
        f"No bucket named {DATA_BUCKET_PREFIX}* found; set DATA_BUCKET or use --latest"
    )


def load_latest(source: str) -> tuple[dict[str, Any], str]:
    """Return (payload, where-it-came-from). `source` is auto | local | s3 | a path."""
    if source not in {"auto", "local", "s3"}:
        path = Path(source)
        return json.loads(path.read_text()), str(path)

    local = Path(LOCAL_LATEST)
    if source in {"auto", "local"} and local.exists():
        return json.loads(local.read_text()), str(local)
    if source == "local":
        raise RuntimeError(f"{LOCAL_LATEST} not found — run scan_fringe.py first")

    import boto3

    s3 = boto3.client("s3")
    bucket = _find_data_bucket(s3)
    body = s3.get_object(Bucket=bucket, Key=LATEST_KEY)["Body"].read()
    return json.loads(body), f"s3://{bucket}/{LATEST_KEY}"


async def resolve_from_programme(
    query: str, *, start: str, end: str
) -> list[dict[str, Any]]:
    """Fallback for a show that postdates the last scan: fetch the programme
    and build show records in the same shape `latest.json` uses."""
    from backend.fringe_lib.scan import collect_window_rows, fetch_all_programme

    from datetime import date as _date

    async with make_async_client() as client:
        api = FringeClient(client)
        await api.authenticate()
        events = await fetch_all_programme(api, page_size=500)

    rows = collect_window_rows(events, _date.fromisoformat(start), _date.fromisoformat(end))
    by_slug: dict[str, dict[str, Any]] = {}
    for row in rows:
        show = by_slug.setdefault(
            row.slug,
            {
                "show_title": row.show_title,
                "slug": row.slug,
                "genre": row.genre,
                "venue": row.venue,
                "url": row.url,
                "performances": [],
            },
        )
        show["performances"].append(
            {
                "performance_id": row.performance_id,
                "box_office_id": row.box_office_id,
                "date": row.date_local,
                "time": row.time_local,
            }
        )
    return match_shows({"shows": list(by_slug.values())}, query)


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


def setup_transport(via: str) -> str:
    """Prepare egress for a local lookup. Returns the transport actually used."""
    if via == "direct":
        os.environ.pop("FRINGE_PROXY_URL", None)
        return "direct"

    if os.environ.get("FRINGE_PROXY_URL"):
        return "proxy (FRINGE_PROXY_URL)"

    from backend.fringe_lib.cart import load_proxy_into_env

    if load_proxy_into_env():
        return "proxy (SSM)"

    print(
        "warn: no residential proxy available (SSM parameter unreadable); "
        "falling back to a direct connection, which 403s from a datacenter IP.",
        file=sys.stderr,
    )
    return "direct (proxy unavailable)"


def _api_url(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env_url = os.environ.get("FRINGE_API_URL")
    if env_url:
        return env_url.rstrip("/")
    # frontend/config.js is written at deploy time: window.FRINGE_API_URL = "…"
    config = Path("frontend/config.js")
    if config.exists():
        import re

        match = re.search(r"https://[^\"']+", config.read_text())
        if match:
            return match.group(0).rstrip("/")
    raise RuntimeError(
        "No API URL — pass --api-url, set FRINGE_API_URL, or deploy to generate "
        "frontend/config.js"
    )


def query_remote(
    api_url: str, show: dict[str, Any], specs: list[dict[str, Any]], nearly: int
) -> dict[str, Any]:
    """Let the deployed Lambda do the lookup (it already has the proxy)."""
    payload = json.dumps(
        {
            "slug": show.get("slug") or "",
            "show_title": show.get("show_title") or "",
            "url": show.get("url") or "",
            "venue": show.get("venue") or "",
            "genre": show.get("genre") or "",
            "performances": specs,
            "nearly_threshold": nearly,
        }
    ).encode()
    request = urllib.request.Request(
        f"{api_url}/live",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def render(show: dict[str, Any], result: dict[str, Any], *, source: str, via: str) -> None:
    title = show.get("show_title") or show.get("slug") or "?"
    print(f"\n{title}")
    meta = " · ".join(x for x in [show.get("venue"), show.get("genre")] if x)
    if meta:
        print(f"  {meta}")
    if show.get("url"):
        print(f"  {show['url']}")
    print(f"  resolved from {source} · checked live via {via} at {result.get('checked_at', '?')}")

    print(f"\n  {'date':12}{'time':7}{'status':12}{'left':7}level")
    for perf in result.get("performances") or []:
        remaining = perf.get("percent_remaining")
        left = "?" if remaining is None else f"{remaining}%"
        level = perf.get("availability_level") or ("no price data" if remaining is None else "")
        status = STATUS_MARK.get(perf.get("availability", ""), perf.get("availability", ""))
        print(f"  {perf.get('date',''):12}{perf.get('time',''):7}{status:12}{left:7}{level}")

    counts = result.get("counts") or {}
    print(
        f"\n  {result.get('checked', 0)} checked — "
        f"{counts.get('available', 0)} available, "
        f"{counts.get('nearly_sold_out', 0)} nearly, "
        f"{counts.get('sold_out', 0)} sold out"
    )
    first = result.get("first_buyable")
    if first:
        print(f"  earliest buyable: {first['date']} {first['time']}")
    else:
        print("  nothing buyable in range")
    print(
        "  note: '?' means the price lookup returned nothing — treat as unknown, "
        "not as available."
    )


# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live availability for specific Fringe shows."
    )
    parser.add_argument("query", help="show slug or part of the title")
    parser.add_argument("--date", help="single date (YYYY-MM-DD)")
    parser.add_argument("--start", help="range start (YYYY-MM-DD)")
    parser.add_argument("--end", help="range end (YYYY-MM-DD)")
    parser.add_argument(
        "--via",
        choices=["proxy", "direct", "remote"],
        default="proxy",
        help="how to reach edfringe (default: proxy — the residential proxy in SSM)",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help="where to resolve the show from: auto | local | s3 | path to a latest.json",
    )
    parser.add_argument(
        "--programme",
        action="store_true",
        help="resolve via a full programme fetch (slow) when the show predates "
        "the last scan",
    )
    parser.add_argument("--api-url", help="API base URL for --via remote")
    parser.add_argument("--nearly-threshold", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--all", action="store_true", help="check every match, not just the best")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    start = args.date or args.start
    end = args.date or args.end

    if args.programme:
        if not (start and end):
            print("--programme needs --date, or --start and --end", file=sys.stderr)
            return 2
        matches = await resolve_from_programme(args.query, start=start, end=end)
        source = "live programme fetch"
    else:
        latest, source = load_latest(args.source)
        matches = match_shows(latest, args.query)

    if not matches:
        print(f"No show matching {args.query!r} in {source}.", file=sys.stderr)
        print("Try --programme if the show is newer than the last scan.", file=sys.stderr)
        return 1

    if len(matches) > 1 and not args.all:
        print(f"{len(matches)} shows match {args.query!r}; checking the best one:", file=sys.stderr)
        for show in matches[1:6]:
            print(f"  also matched: {show.get('show_title')} ({show.get('slug')})", file=sys.stderr)
        print("  (pass --all to check them all)", file=sys.stderr)
        matches = matches[:1]

    via = "remote API" if args.via == "remote" else setup_transport(args.via)
    payloads: list[dict[str, Any]] = []

    for show in matches:
        specs = window_performances(show, start=start, end=end)
        if not specs:
            print(
                f"{show.get('show_title')}: no performances in that date range.",
                file=sys.stderr,
            )
            continue

        if args.via == "remote":
            result = query_remote(_api_url(args.api_url), show, specs, args.nearly_threshold)
        else:
            async with make_async_client() as client:
                api = FringeClient(client)
                await api.authenticate()
                rows = await check_box_office_ids(
                    api,
                    specs,
                    nearly_threshold=args.nearly_threshold,
                    concurrency=args.concurrency,
                    meta={
                        "show_title": show.get("show_title") or "",
                        "slug": show.get("slug") or "",
                        "venue": show.get("venue") or "",
                        "genre": show.get("genre") or "",
                        "url": show.get("url") or "",
                    },
                )
            from backend.fringe_lib.monitors import now_iso

            result = {
                "ok": True,
                "checked_at": now_iso(),
                **summarize(rows),
                "performances": [r.to_public_dict() for r in rows],
            }

        if args.json:
            payloads.append({"show": show.get("slug"), **result})
        else:
            render(show, result, source=source, via=via)

    if args.json:
        print(json.dumps(payloads, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
