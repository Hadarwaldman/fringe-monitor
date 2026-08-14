#!/usr/bin/env python3
"""
Local CLI for Edinburgh Fringe availability scanning.

Cloud deployment lives under terraform/ + backend/lambdas/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.fringe_lib.client import FringeClient
from backend.fringe_lib.edfest_offers import (
    EDFEST_SHOW_URL,
    fetch_and_attach_edfest_offers,
    fetch_edfest_title_slugs,
    normalize_title,
)
from backend.fringe_lib.models import PerformanceRow
from backend.fringe_lib.scan import (
    DEFAULT_END,
    DEFAULT_START,
    EDINBURGH,
    build_latest_payload,
    collect_show_details,
    collect_window_rows,
    enrich_with_prices,
    fetch_all_programme,
    summarize_shows,
)
from backend.fringe_lib.trend import (
    attach_trends,
    build_day_snapshot,
    merge_history,
    scan_date_from_payload,
)


def write_performances_csv(path: Path, rows: list[PerformanceRow]) -> None:
    fieldnames = [
        "availability",
        "show_title",
        "genre",
        "venue",
        "date",
        "time",
        "performance_title",
        "ticket_status",
        "sold_out",
        "percent_remaining",
        "availability_level",
        "offers",
        "url",
        "performance_id",
        "box_office_id",
        "datetime_utc",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    urgency_rank = {"sold_out": 0, "nearly_sold_out": 1, "available": 2}
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(
            rows,
            key=lambda r: (
                urgency_rank.get(r.availability, 9),
                r.percent_remaining if r.percent_remaining is not None else 999,
                r.date_local,
                r.time_local,
                r.show_title,
            ),
        ):
            writer.writerow(row.to_csv_dict())


def write_show_summary_csv(path: Path, rows: list[PerformanceRow]) -> None:
    shows = summarize_shows(rows)
    fieldnames = [
        "show_title",
        "genre",
        "venue",
        "performances_in_window",
        "sold_out_dates",
        "nearly_sold_out_dates",
        "available_dates",
        "offer_dates",
        "any_sold_out",
        "any_offers",
        "url",
        "slug",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for show in shows:
            offer_bits = []
            for day, offers in (show.get("offer_dates") or {}).items():
                labels = ", ".join(
                    (o.get("label") or o.get("code") or "").strip()
                    for o in offers
                    if (o.get("label") or o.get("code") or "").strip()
                )
                if labels:
                    offer_bits.append(f"{day}: {labels}")
            writer.writerow(
                {
                    "show_title": show["show_title"],
                    "genre": show["genre"],
                    "venue": show["venue"],
                    "performances_in_window": show["performances_in_window"],
                    "sold_out_dates": "; ".join(show["sold_out_dates"]),
                    "nearly_sold_out_dates": "; ".join(show["nearly_sold_out_dates"]),
                    "available_dates": "; ".join(show["available_dates"]),
                    "offer_dates": "; ".join(offer_bits),
                    "any_sold_out": show["any_sold_out"],
                    "any_offers": show.get("any_offers", False),
                    "url": show["url"],
                    "slug": show["slug"],
                }
            )


def save_raw_snapshot(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(tz=EDINBURGH).isoformat(),
        "show_count": len(events),
        "performance_count": sum(len(e.get("performances") or []) for e in events),
        "events": events,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    print(
        f"Wrote raw snapshot ({payload['show_count']} shows, "
        f"{payload['performance_count']} performances) → {path}",
        flush=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Edinburgh Fringe availability (full programme → filtered CSV)."
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output", "-o", default="output/fringe_availability.csv")
    parser.add_argument("--summary-output", default="output/fringe_show_summary.csv")
    parser.add_argument("--raw-output", default="output/fringe_raw_programme.json")
    parser.add_argument("--latest-json", default="output/latest.json")
    parser.add_argument("--details-json", default="output/details.json")
    parser.add_argument("--history-json", default="output/history.json")
    parser.add_argument("--nearly-threshold", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--page-size", type=int, default=500)
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        print("error: --end must be on or after --start", file=sys.stderr)
        return 2

    print(
        f"Fetching full Fringe programme, then filtering "
        f"{start.isoformat()} → {end.isoformat()} (Edinburgh); "
        f"nearly-sold-out ≤ {args.nearly_threshold}% or level=low",
        flush=True,
    )

    limits = httpx.Limits(max_connections=args.concurrency + 5, max_keepalive_connections=20)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as client:
        api = FringeClient(client)
        await api.authenticate()
        events = await fetch_all_programme(api, args.page_size)
        save_raw_snapshot(Path(args.raw_output), events)

        candidates = collect_window_rows(events, start, end)
        print(
            f"Classifying {len(candidates)} ticketed performances in filter window…",
            flush=True,
        )
        classified = await enrich_with_prices(
            api,
            candidates,
            concurrency=args.concurrency,
            nearly_threshold=args.nearly_threshold,
        )
        offers_meta = await fetch_and_attach_edfest_offers(
            client,
            classified,
            start=start,
            end=end,
        )
        try:
            edfest_slugs = await fetch_edfest_title_slugs(client)
        except Exception as exc:  # noqa: BLE001 — links are nice-to-have
            print(f"warn: EdFest catalogue fetch failed: {exc}", flush=True)
            edfest_slugs = {}

    out = Path(args.output)
    summary = Path(args.summary_output)
    write_performances_csv(out, classified)
    write_show_summary_csv(summary, classified)
    latest = build_latest_payload(
        classified,
        start=start,
        end=end,
        nearly_threshold=args.nearly_threshold,
        offers_meta=offers_meta,
    )
    history_path = Path(args.history_json)
    previous_history: dict[str, Any] = {"days": []}
    if history_path.exists():
        try:
            previous_history = json.loads(history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_history = {"days": []}
    snapshot = build_day_snapshot(latest["shows"], scan_date_from_payload(latest))
    history = merge_history(previous_history, snapshot)
    attach_trends(latest["shows"], history)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history), encoding="utf-8")

    latest_path = Path(args.latest_json)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(latest), encoding="utf-8")

    # Show detail pages: descriptions, venue addresses, EdFest ticket links.
    slugs_in_window = {s["slug"] for s in latest["shows"] if s.get("slug")}
    details = collect_show_details(events, slugs=slugs_in_window)
    for det in details.values():
        edfest_slug = edfest_slugs.get(normalize_title(det.get("title") or ""))
        if edfest_slug:
            det["edfest_url"] = EDFEST_SHOW_URL.format(slug=edfest_slug)
    details_path = Path(args.details_json)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    details_path.write_text(
        json.dumps({"fetched_at": latest["fetched_at"], "shows": details}),
        encoding="utf-8",
    )

    sold = latest["counts"]["sold_out"]
    nearly = latest["counts"]["nearly_sold_out"]
    available = latest["counts"]["available"]
    print(
        f"Done. {sold} sold out, {nearly} nearly sold out, {available} available "
        f"({latest['counts']['shows_with_sold_out']} shows with ≥1 sold-out date)\n"
        f"  performances → {out.resolve()}\n"
        f"  show summary → {summary.resolve()}\n"
        f"  latest json  → {latest_path.resolve()}\n"
        f"  history json → {history_path.resolve()}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
