#!/usr/bin/env python3
"""
Scan Edinburgh Fringe (edfringe.com) shows for ticket availability.

1. Fetches the full programme with every performance date (raw snapshot).
2. Classifies each performance in a filter window (default 13–20 Aug 2026).
3. Writes a CSV with sold_out / nearly_sold_out / available days.

Uses the same public GraphQL API as https://www.edfringe.com/tickets/whats-on
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

API_BASE = "https://edfringe-tikketr-web-api.equhost.com"
SHOW_URL = "https://www.edfringe.com/tickets/whats-on/{slug}"
EDINBURGH = ZoneInfo("Europe/London")

# Anonymous credentials embedded in the public edfringe tickets web app.
ANON_USER = "anonymous"
ANON_PASS = "2add50c2-ac54-4c1e-b5bc-f8d9ca66a067"

# Ticket statuses where Fringe box-office allocation / sell-out applies.
# NO_ALLOCATION_CONTACT_VENUE = Fringe has no tickets left (contact venue);
# the prices API reports 0% remaining for these — treat as sold out.
CHECKABLE_STATUSES = {
    "TICKETS_AVAILABLE",
    "PREVIEW_SHOW",
    "TWO_FOR_ONE",
    "FREE_TICKETED",
    "EVENT_SPECIFIC",
    "NO_ALLOCATION_CONTACT_VENUE",
}

# Statuses that mean sold out at the Fringe box office without needing prices.
SOLD_OUT_STATUSES = {
    "NO_ALLOCATION_CONTACT_VENUE",
}

EVENTS_QUERY = """
query EventsSearch($criteria: SearchCriteriaInput!) {
  events(input: $criteria) {
    total
    page
    per
    results {
      id
      title
      slug
      genre
      startingDate
      endingDate
      datesDisplay
      venues { title }
      performances {
        id
        title
        dateTime
        soldOut
        ticketStatus
        status
        ticketsAvailable
        boxOfficeId
        cancelled
        badges { label colour }
      }
    }
  }
}
"""

PRICES_QUERY = """
query PerformancePrices($performanceId: String!) {
  performancePrices(performanceRef: $performanceId) {
    success
    error
    result {
      performanceId
      performancePercentageRemaining
      performanceAvailabilityLevel
      allocationDetails
    }
  }
}
"""


@dataclass
class PerformanceRow:
    show_title: str
    slug: str
    genre: str
    venue: str
    performance_id: int
    performance_title: str
    date_local: str
    time_local: str
    datetime_utc: str
    ticket_status: str
    sold_out_flag: bool
    box_office_id: str | None
    percent_remaining: int | None = None
    availability_level: str | None = None
    availability: str = ""
    url: str = ""

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "show_title": self.show_title,
            "genre": self.genre,
            "venue": self.venue,
            "date": self.date_local,
            "time": self.time_local,
            "performance_title": self.performance_title,
            "ticket_status": self.ticket_status,
            "sold_out": self.availability == "sold_out",
            "percent_remaining": self.percent_remaining
            if self.percent_remaining is not None
            else "",
            "availability_level": self.availability_level or "",
            "url": self.url,
            "performance_id": self.performance_id,
            "box_office_id": self.box_office_id or "",
            "datetime_utc": self.datetime_utc,
        }


class FringeClient:
    def __init__(self, client: httpx.AsyncClient, *, max_retries: int = 4) -> None:
        self._client = client
        self._token: str | None = None
        self._max_retries = max_retries
        self._auth_lock = asyncio.Lock()

    async def authenticate(self) -> None:
        async with self._auth_lock:
            resp = await self._client.post(
                f"{API_BASE}/token",
                json={"username": ANON_USER, "password": ANON_PASS},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            if not token:
                raise RuntimeError(f"Token endpoint returned no token: {data!r}")
            self._token = token

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._token:
            await self.authenticate()
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(
                    f"{API_BASE}/graphql",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                )
                if resp.status_code == 401:
                    await self.authenticate()
                    resp = await self._client.post(
                        f"{API_BASE}/graphql",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self._token}",
                            "Accept": "application/json",
                        },
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get("errors"):
                    raise RuntimeError(f"GraphQL error: {data['errors']}")
                return data["data"]
            except (httpx.TransportError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                await asyncio.sleep(0.4 * (2**attempt))
        assert last_exc is not None
        raise last_exc


def parse_iso(dt: str) -> datetime:
    return datetime.fromisoformat(dt.replace("Z", "+00:00"))


def local_day(dt_utc: datetime) -> date:
    return dt_utc.astimezone(EDINBURGH).date()


def daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def classify_availability(
    *,
    sold_out: bool,
    ticket_status: str,
    percent_remaining: int | None,
    availability_level: str | None,
    nearly_threshold: int,
) -> str:
    """Return sold_out | nearly_sold_out | available."""
    level = (availability_level or "").strip().lower()
    if (
        sold_out
        or ticket_status in SOLD_OUT_STATUSES
        or (percent_remaining is not None and percent_remaining <= 0)
    ):
        return "sold_out"
    if level == "low" or (
        percent_remaining is not None and percent_remaining <= nearly_threshold
    ):
        return "nearly_sold_out"
    return "available"


async def fetch_all_programme(
    api: FringeClient,
    page_size: int,
) -> list[dict[str, Any]]:
    """
    Fetch every show with its full performance calendar.

    Do not restrict the listing query to the user's date filter — that window is
    applied later when classifying / writing CSV.
    """
    criteria_base = {
        "per": page_size,
        "excludeCancelled": True,
        "deDuplicate": True,
        # Default sort is RANDOM and paginates unstably (dupes / missing shows).
        "sortBy": "TITLE",
    }

    results: list[dict[str, Any]] = []
    page = 0
    reported_total: int | None = None
    while True:
        data = await api.graphql(
            EVENTS_QUERY, {"criteria": {**criteria_base, "page": page}}
        )
        block = data["events"]
        if reported_total is None:
            reported_total = block["total"]
            print(f"Programme lists {reported_total} shows…", flush=True)
        batch = block["results"]
        results.extend(batch)
        print(
            f"  page {page}: +{len(batch)} (collected {len(results)}/{reported_total})",
            flush=True,
        )
        if not batch or len(batch) < page_size:
            break
        page += 1
        if page > 100:
            raise RuntimeError("Pagination exceeded safety cap for programme fetch")

    # Dedupe by event id (stable TITLE sort should already be unique).
    by_id: dict[int, dict[str, Any]] = {}
    for event in results:
        by_id[event["id"]] = event
    print(f"Unique shows in raw snapshot: {len(by_id)}", flush=True)
    return list(by_id.values())


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


def collect_window_rows(
    events: list[dict[str, Any]],
    start: date,
    end: date,
) -> list[PerformanceRow]:
    """Keep ticketed performances whose Edinburgh local date is in [start, end]."""
    allowed = set(daterange(start, end))
    rows_by_perf: dict[int, PerformanceRow] = {}

    for event in events:
        venues = event.get("venues") or []
        venue = ", ".join(v.get("title") or "" for v in venues if v.get("title"))
        slug = event.get("slug") or ""
        url = SHOW_URL.format(slug=slug) if slug else ""

        for perf in event.get("performances") or []:
            if perf.get("cancelled"):
                continue
            dt = parse_iso(perf["dateTime"])
            day = local_day(dt)
            if day not in allowed:
                continue

            status = perf.get("ticketStatus") or ""
            sold_out = bool(perf.get("soldOut"))
            if not sold_out and status not in CHECKABLE_STATUSES:
                continue

            local = dt.astimezone(EDINBURGH)
            rows_by_perf[perf["id"]] = PerformanceRow(
                show_title=event.get("title") or "",
                slug=slug,
                genre=event.get("genre") or "",
                venue=venue,
                performance_id=perf["id"],
                performance_title=(perf.get("title") or "").strip(),
                date_local=local.strftime("%Y-%m-%d"),
                time_local=local.strftime("%H:%M"),
                datetime_utc=perf["dateTime"],
                ticket_status=status,
                sold_out_flag=sold_out,
                box_office_id=perf.get("boxOfficeId"),
                url=url,
            )
    return list(rows_by_perf.values())


async def enrich_with_prices(
    api: FringeClient,
    rows: list[PerformanceRow],
    *,
    concurrency: int,
    nearly_threshold: int,
) -> list[PerformanceRow]:
    """Classify every row; always keep it (sold_out / nearly / available)."""
    sem = asyncio.Semaphore(concurrency)
    done = 0
    total = len(rows)
    lock = asyncio.Lock()

    async def one(row: PerformanceRow) -> None:
        nonlocal done
        # Listing / ticket-status already implies sold out at Fringe BO.
        if row.sold_out_flag or row.ticket_status in SOLD_OUT_STATUSES:
            if row.ticket_status in SOLD_OUT_STATUSES and row.percent_remaining is None:
                row.percent_remaining = 0
            row.availability = "sold_out"
            async with lock:
                done += 1
                if done % 200 == 0 or done == total:
                    print(f"  checked availability {done}/{total}", flush=True)
            return

        if not row.box_office_id:
            row.availability = "available"
            async with lock:
                done += 1
                if done % 200 == 0 or done == total:
                    print(f"  checked availability {done}/{total}", flush=True)
            return

        async with sem:
            try:
                data = await api.graphql(
                    PRICES_QUERY, {"performanceId": row.box_office_id}
                )
            except Exception as exc:  # noqa: BLE001 - continue scan on single failure
                print(f"  warn: prices failed for {row.box_office_id}: {exc}", flush=True)
                row.availability = "available"
                async with lock:
                    done += 1
                    if done % 200 == 0 or done == total:
                        print(f"  checked availability {done}/{total}", flush=True)
                return

            result = (data["performancePrices"].get("result") or {})
            row.percent_remaining = result.get("performancePercentageRemaining")
            row.availability_level = result.get("performanceAvailabilityLevel")
            row.availability = classify_availability(
                sold_out=row.sold_out_flag,
                ticket_status=row.ticket_status,
                percent_remaining=row.percent_remaining,
                availability_level=row.availability_level,
                nearly_threshold=nearly_threshold,
            )

        async with lock:
            done += 1
            if done % 200 == 0 or done == total:
                print(f"  checked availability {done}/{total}", flush=True)

    await asyncio.gather(*(one(r) for r in rows))
    return rows


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
    """One row per show: which dates are sold out / nearly / available."""
    by_show: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.slug, row.show_title)
        bucket = by_show.setdefault(
            key,
            {
                "show_title": row.show_title,
                "slug": row.slug,
                "genre": row.genre,
                "venue": row.venue,
                "url": row.url,
                "sold_out_dates": set(),
                "nearly_sold_out_dates": set(),
                "available_dates": set(),
                "performances": 0,
            },
        )
        bucket["performances"] += 1
        if row.availability == "sold_out":
            bucket["sold_out_dates"].add(row.date_local)
        elif row.availability == "nearly_sold_out":
            bucket["nearly_sold_out_dates"].add(row.date_local)
        else:
            bucket["available_dates"].add(row.date_local)

    fieldnames = [
        "show_title",
        "genre",
        "venue",
        "performances_in_window",
        "sold_out_dates",
        "nearly_sold_out_dates",
        "available_dates",
        "any_sold_out",
        "url",
        "slug",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for bucket in sorted(by_show.values(), key=lambda b: b["show_title"].lower()):
            sold = sorted(bucket["sold_out_dates"])
            nearly = sorted(bucket["nearly_sold_out_dates"])
            avail = sorted(bucket["available_dates"])
            writer.writerow(
                {
                    "show_title": bucket["show_title"],
                    "genre": bucket["genre"],
                    "venue": bucket["venue"],
                    "performances_in_window": bucket["performances"],
                    "sold_out_dates": "; ".join(sold),
                    "nearly_sold_out_dates": "; ".join(nearly),
                    "available_dates": "; ".join(avail),
                    "any_sold_out": bool(sold),
                    "url": bucket["url"],
                    "slug": bucket["slug"],
                }
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Edinburgh Fringe availability (full programme → filtered CSV)."
    )
    parser.add_argument(
        "--start",
        default="2026-08-13",
        help="Filter start date inclusive (YYYY-MM-DD, Edinburgh local). Default: 2026-08-13",
    )
    parser.add_argument(
        "--end",
        default="2026-08-20",
        help="Filter end date inclusive (YYYY-MM-DD, Edinburgh local). Default: 2026-08-20",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="output/fringe_availability.csv",
        help="Per-performance CSV output path",
    )
    parser.add_argument(
        "--summary-output",
        default="output/fringe_show_summary.csv",
        help="Per-show date summary CSV path",
    )
    parser.add_argument(
        "--raw-output",
        default="output/fringe_raw_programme.json",
        help="Full programme JSON snapshot (all performance dates)",
    )
    parser.add_argument(
        "--nearly-threshold",
        type=int,
        default=20,
        help="Treat percent_remaining <= this as nearly sold out (also uses API level 'low'). Default: 20",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=25,
        help="Parallel availability requests. Default: 25",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="Events page size. Default: 500",
    )
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

    out = Path(args.output)
    summary = Path(args.summary_output)
    write_performances_csv(out, classified)
    write_show_summary_csv(summary, classified)

    sold = sum(1 for r in classified if r.availability == "sold_out")
    nearly = sum(1 for r in classified if r.availability == "nearly_sold_out")
    available = sum(1 for r in classified if r.availability == "available")
    shows_with_sold = {
        r.slug for r in classified if r.availability == "sold_out"
    }
    print(
        f"Done. {sold} sold out, {nearly} nearly sold out, {available} available "
        f"({len(shows_with_sold)} shows with ≥1 sold-out date)\n"
        f"  performances → {out.resolve()}\n"
        f"  show summary → {summary.resolve()}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
