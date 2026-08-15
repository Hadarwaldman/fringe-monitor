#!/usr/bin/env python3
"""
Data plumbing for /recommend.

Every recommendation needs the same three joins: the latest scan (what is
selling), the PlanMyFringe planner (what the user has already committed to and
what they want), and the DynamoDB config (the active date window and the
sold-out threshold). Doing that by hand each time is slow and easy to get
subtly wrong — latest.json alone is ~18 MB, so a naive read costs real time.

Subcommands
-----------
    sync                    Cache latest.json / planner.json / history.json locally
    day    <date>           The user's schedule for a day, with the gaps between items
    candidates             Rank what is on in a time window, excluding what they've booked
    show   <query>          Everything known about one show (cached, not live)

Availability here is always as-of the last scan. For the truth right now, use
`query_show.py` at the repo root — that re-checks the box office live.

Examples
--------
    python fringe_context.py sync
    python fringe_context.py day 2026-08-15
    python fringe_context.py candidates --date 2026-08-15 --from 13:00 --to 15:30
    python fringe_context.py candidates --date 2026-08-15 --from 13:00 --to 15:30 \
        --wishlist-only --min-pop 70 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

CACHE = Path(os.environ.get("FRINGE_CACHE", ".scratch/fringe-cache"))
DATA_BUCKET_PREFIX = "fringe-monitor-data-"
TABLE = os.environ.get("FRINGE_TABLE", "fringe-monitor")
OBJECTS = {
    "latest": "data/latest.json",
    "planner": "data/planner.json",
    "history": "data/history.json",
    "details": "data/details.json",  # optional; absent in some deployments
}

# No runtimes are published in the scan feed, so a gap has to be estimated.
# A typical Fringe slot is an hour; venues turn rooms over fast and the walk
# between the main clusters is short but not nothing.
ASSUMED_RUNTIME_MIN = 75
ASSUMED_TRAVEL_MIN = 20


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _s3():
    import boto3

    return boto3.client("s3")


def data_bucket() -> str:
    bucket = os.environ.get("DATA_BUCKET")
    if bucket:
        return bucket
    for entry in _s3().list_buckets()["Buckets"]:
        if entry["Name"].startswith(DATA_BUCKET_PREFIX):
            return entry["Name"]
    raise RuntimeError(f"No {DATA_BUCKET_PREFIX}* bucket; set DATA_BUCKET")


def sync(force: bool = False) -> dict[str, str]:
    """Pull the S3 payloads into the local cache. Cheap to re-run."""
    CACHE.mkdir(parents=True, exist_ok=True)
    bucket = data_bucket()
    s3 = _s3()
    out: dict[str, str] = {}
    for name, key in OBJECTS.items():
        dest = CACHE / f"{name}.json"
        if dest.exists() and not force:
            out[name] = f"cached {dest}"
            continue
        try:
            s3.download_file(bucket, key, str(dest))
            out[name] = f"{dest} ({dest.stat().st_size / 1e6:.1f} MB)"
        except Exception as exc:  # noqa: BLE001
            out[name] = f"unavailable ({type(exc).__name__})"
    return out


def load(name: str) -> dict[str, Any]:
    path = CACHE / f"{name}.json"
    if not path.exists():
        sync()
    if not path.exists():
        raise RuntimeError(f"{name}.json unavailable — run `sync` first")
    return json.loads(path.read_text())


def shows(latest: dict[str, Any]) -> list[dict[str, Any]]:
    items = latest.get("shows") or []
    return list(items.values()) if isinstance(items, dict) else items


def config() -> dict[str, Any]:
    """Live config from DynamoDB — the active window and sold-out threshold."""
    import boto3

    table = boto3.resource("dynamodb").Table(TABLE)
    return table.get_item(Key={"pk": "CONFIG", "sk": "MAIN"}).get("Item") or {}


def monitors() -> list[dict[str, Any]]:
    import boto3
    from boto3.dynamodb.conditions import Key

    table = boto3.resource("dynamodb").Table(TABLE)
    return table.query(KeyConditionExpression=Key("pk").eq("MONITOR")).get("Items", [])


# --------------------------------------------------------------------------
# Joins
# --------------------------------------------------------------------------


def wishlist_scores(planner: dict[str, Any]) -> dict[str, float]:
    """slug -> the user's own 0-10 rating on PlanMyFringe. This is the single
    best personalisation signal available; popularity is everyone else's
    opinion, this one is theirs."""
    out: dict[str, float] = {}
    for item in planner.get("wishlist") or []:
        slug = item.get("slug")
        score = item.get("score")
        if slug and score is not None:
            out[str(slug)] = float(score)
    return out


def booked_slugs(planner: dict[str, Any]) -> set[str]:
    """Every show already on the schedule — booked or merely planned. Both are
    reasons not to recommend it again."""
    return {e["slug"] for e in planner.get("schedule") or [] if e.get("slug")}


def _minutes(hhmm: str) -> int:
    hours, _, mins = (hhmm or "0:0").partition(":")
    return int(hours) * 60 + int(mins or 0)


def _clock(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def day_schedule(planner: dict[str, Any], day: str) -> list[dict[str, Any]]:
    entries = [e for e in planner.get("schedule") or [] if e.get("date") == day]
    return sorted(entries, key=lambda e: e.get("time") or "")


def gaps(
    entries: list[dict[str, Any]],
    *,
    runtime: int = ASSUMED_RUNTIME_MIN,
    travel: int = ASSUMED_TRAVEL_MIN,
    confirmed_only: bool = False,
) -> list[dict[str, Any]]:
    """Free stretches between commitments.

    `confirmed_only` treats unconfirmed entries as droppable — on PlanMyFringe
    an unconfirmed row is a plan, not a ticket, so the real gap is often much
    wider than the schedule looks. Offer the user both readings when they
    differ, rather than silently picking one.
    """
    items = [e for e in entries if not confirmed_only or e.get("confirmed")]
    out: list[dict[str, Any]] = []
    for current, following in zip(items, items[1:]):
        ends = _minutes(current.get("time") or "0:0") + runtime
        starts = _minutes(following.get("time") or "0:0")
        free = starts - ends
        if free < runtime // 2:
            continue
        out.append(
            {
                "after": current.get("title"),
                "after_ends_approx": _clock(ends),
                "before": following.get("title"),
                "before_starts": following.get("time"),
                "free_minutes": free,
                # Leave travel time at each end of the gap.
                "search_from": _clock(ends + travel),
                "search_to": _clock(starts - travel),
            }
        )
    return out


def candidates(
    latest: dict[str, Any],
    planner: dict[str, Any],
    *,
    day: str,
    start: str | None,
    end: str | None,
    exclude_booked: bool = True,
) -> list[dict[str, Any]]:
    """Every performance on `day` inside the time window, joined to the user's
    wishlist scores and annotated with popularity."""
    scores = wishlist_scores(planner)
    skip = booked_slugs(planner) if exclude_booked else set()
    rows: list[dict[str, Any]] = []

    for show in shows(latest):
        slug = show.get("slug") or ""
        if slug in skip:
            continue
        for perf in show.get("performances") or []:
            if perf.get("date") != day:
                continue
            when = perf.get("time") or ""
            if start and when < start:
                continue
            if end and when > end:
                continue
            trend = show.get("trend") or {}
            rows.append(
                {
                    "time": when,
                    "show_title": show.get("show_title"),
                    "slug": slug,
                    "venue": show.get("venue"),
                    "genre": show.get("genre"),
                    "url": show.get("url"),
                    "availability": perf.get("availability"),
                    "percent_remaining": perf.get("percent_remaining"),
                    "popularity": show.get("avg_percent_sold"),
                    "trend_per_day": trend.get("avg_daily_sold_pct"),
                    "wishlist_score": scores.get(slug),
                    "offers": [o.get("label") for o in (perf.get("offers") or [])],
                    "performance_id": perf.get("performance_id"),
                    "box_office_id": perf.get("box_office_id"),
                }
            )
    return rows


def rank(rows: list[dict[str, Any]], *, by: str = "popularity") -> list[dict[str, Any]]:
    """Sort candidates. `popularity` is the crowd's verdict; `wishlist` is the
    user's own; `blend` splits the difference so a show they rated highly isn't
    buried under strangers' sell-through."""
    if by == "wishlist":
        key = lambda r: (-(r.get("wishlist_score") or 0), -(r.get("popularity") or 0))
    elif by == "blend":
        key = lambda r: -(
            (r.get("popularity") or 0) / 10 + (r.get("wishlist_score") or 0)
        )
    else:
        key = lambda r: (
            -(r.get("popularity") or 0),
            r.get("percent_remaining") if r.get("percent_remaining") is not None else 999,
        )
    return sorted(rows, key=key)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _print_rows(rows: list[dict[str, Any]], limit: int) -> None:
    print(f"{'time':6}{'show':44}{'venue':34}{'pop':5}{'left':6}{'wish':5}{'status'}")
    for row in rows[:limit]:
        left = row.get("percent_remaining")
        print(
            f"{str(row.get('time') or ''):6}"
            f"{str(row.get('show_title') or '')[:42]:44}"
            f"{str(row.get('venue') or '')[:32]:34}"
            f"{('' if row.get('popularity') is None else round(row['popularity'])):<5}"
            f"{('-' if left is None else str(left) + '%'):6}"
            f"{('' if row.get('wishlist_score') is None else row['wishlist_score']):<5}"
            f"{row.get('availability') or ''}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="cache S3 payloads locally")
    p_sync.add_argument("--force", action="store_true")

    p_day = sub.add_parser("day", help="schedule + gaps for a date")
    p_day.add_argument("date")
    p_day.add_argument("--runtime", type=int, default=ASSUMED_RUNTIME_MIN)
    p_day.add_argument("--travel", type=int, default=ASSUMED_TRAVEL_MIN)

    p_cand = sub.add_parser("candidates", help="rank what's on in a window")
    p_cand.add_argument("--date", required=True)
    p_cand.add_argument("--from", dest="start")
    p_cand.add_argument("--to", dest="end")
    p_cand.add_argument("--genre")
    p_cand.add_argument("--venue")
    p_cand.add_argument("--min-pop", type=float)
    p_cand.add_argument("--wishlist-only", action="store_true")
    p_cand.add_argument(
        "--status",
        choices=["buyable", "sold_out", "any"],
        default="buyable",
        help="buyable = available or nearly (default); sold_out = monitor candidates",
    )
    p_cand.add_argument("--rank", choices=["popularity", "wishlist", "blend"], default="popularity")
    p_cand.add_argument("--include-booked", action="store_true")
    p_cand.add_argument("--limit", type=int, default=15)
    p_cand.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="everything cached about one show")
    p_show.add_argument("query")

    args = parser.parse_args(argv)

    if args.cmd == "sync":
        for name, status in sync(force=args.force).items():
            print(f"  {name:8} {status}")
        return 0

    if args.cmd == "day":
        planner = load("planner")
        entries = day_schedule(planner, args.date)
        if not entries:
            print(f"Nothing scheduled on {args.date}.")
            return 0
        print(f"Schedule for {args.date} (synced {planner.get('synced_at', '?')}):")
        for entry in entries:
            mark = "booked" if entry.get("confirmed") else "planned (not booked)"
            print(
                f"  {entry.get('time'):6}{str(entry.get('title'))[:44]:46}"
                f"{mark:22}{str(entry.get('venue') or '')[:40]}"
            )
        for label, only in (("all commitments", False), ("confirmed bookings only", True)):
            found = gaps(entries, runtime=args.runtime, travel=args.travel, confirmed_only=only)
            print(f"\nGaps ({label}):")
            if not found:
                print("  none")
            for gap in found:
                print(
                    f"  after {str(gap['after'])[:30]:32} ~{gap['after_ends_approx']}"
                    f" → {gap['before_starts']} {str(gap['before'])[:26]:28}"
                    f" search {gap['search_from']}-{gap['search_to']}"
                    f" ({gap['free_minutes']} min free)"
                )
        return 0

    if args.cmd == "candidates":
        latest, planner = load("latest"), load("planner")
        rows = candidates(
            latest,
            planner,
            day=args.date,
            start=args.start,
            end=args.end,
            exclude_booked=not args.include_booked,
        )
        if args.status == "buyable":
            rows = [r for r in rows if r["availability"] in {"available", "nearly_sold_out"}]
        elif args.status == "sold_out":
            rows = [r for r in rows if r["availability"] == "sold_out"]
        if args.genre:
            rows = [r for r in rows if args.genre.upper() in (r.get("genre") or "").upper()]
        if args.venue:
            rows = [r for r in rows if args.venue.lower() in (r.get("venue") or "").lower()]
        if args.min_pop is not None:
            rows = [r for r in rows if (r.get("popularity") or 0) >= args.min_pop]
        if args.wishlist_only:
            rows = [r for r in rows if r.get("wishlist_score") is not None]

        rows = rank(rows, by=args.rank)
        if args.json:
            print(json.dumps(rows[: args.limit], indent=2, default=str))
        else:
            print(
                f"{len(rows)} candidates on {args.date} "
                f"{args.start or ''}-{args.end or ''} "
                f"(scan fetched {latest.get('fetched_at', '?')})"
            )
            _print_rows(rows, args.limit)
        return 0

    if args.cmd == "show":
        latest = load("latest")
        needle = args.query.casefold()
        found = [
            s
            for s in shows(latest)
            if needle in (s.get("slug") or "").casefold()
            or needle in (s.get("show_title") or "").casefold()
        ]
        if not found:
            print(f"No cached show matching {args.query!r}", file=sys.stderr)
            return 1
        scores = wishlist_scores(load("planner"))
        for show in found[:5]:
            show = dict(show)
            show["wishlist_score"] = scores.get(show.get("slug"))
            print(json.dumps(show, indent=2, default=str))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
