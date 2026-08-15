from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .client import EVENTS_QUERY, PRICES_QUERY, SHOW_URL, FringeClient
from .models import PerformanceRow

EDINBURGH = ZoneInfo("Europe/London")

CHECKABLE_STATUSES = {
    "TICKETS_AVAILABLE",
    "PREVIEW_SHOW",
    "TWO_FOR_ONE",
    "FREE_TICKETED",
    "EVENT_SPECIFIC",
    "NO_ALLOCATION_CONTACT_VENUE",
}

SOLD_OUT_STATUSES = {
    "NO_ALLOCATION_CONTACT_VENUE",
}

DEFAULT_START = "2026-08-12"
DEFAULT_END = "2026-08-20"


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
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Fetch every show with its full performance calendar."""
    criteria_base = {
        "per": page_size,
        "excludeCancelled": True,
        "deDuplicate": True,
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

    by_id: dict[int, dict[str, Any]] = {}
    for event in results:
        by_id[event["id"]] = event
    print(f"Unique shows in raw snapshot: {len(by_id)}", flush=True)
    return list(by_id.values())


def collect_show_details(
    events: list[dict[str, Any]],
    *,
    slugs: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Show-level detail map keyed by slug (description, venue location, …).

    Written to data/details.json for the show detail page; kept out of
    latest.json so the dashboard payload stays small. When `slugs` is given,
    only those shows are included (e.g. shows with performances in the window).
    """
    details: dict[str, dict[str, Any]] = {}
    for event in events:
        slug = event.get("slug") or ""
        if not slug or (slugs is not None and slug not in slugs):
            continue
        venues = []
        for v in event.get("venues") or []:
            name = (v.get("title") or "").strip()
            if not name:
                continue
            venues.append(
                {
                    "name": name,
                    "slug": v.get("slug") or "",
                    "address": (v.get("address1") or "").strip(),
                    "post_code": (v.get("postCode") or "").strip(),
                    "description": (v.get("description") or "").strip(),
                }
            )
        images = [i.get("url") for i in (event.get("images") or []) if i.get("url")]
        details[slug] = {
            "title": event.get("title") or "",
            "description": (event.get("description") or "").strip(),
            "age_restriction": event.get("ageRestriction") or "",
            "duration": event.get("duration") or "",
            "image_url": images[0] if images else "",
            "venues": venues,
        }
    return details


def collect_window_rows(
    events: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    slugs: set[str] | None = None,
) -> list[PerformanceRow]:
    """Keep ticketed performances whose Edinburgh local date is in [start, end]."""
    allowed = set(daterange(start, end))
    rows_by_perf: dict[int, PerformanceRow] = {}

    for event in events:
        slug = event.get("slug") or ""
        if slugs is not None and slug not in slugs:
            continue

        venues = event.get("venues") or []
        venue = ", ".join(v.get("title") or "" for v in venues if v.get("title"))
        url = SHOW_URL.format(slug=slug) if slug else ""

        for perf in event.get("performances") or []:
            if perf.get("cancelled"):
                continue
            # Some feed rows carry a placeholder dateTime (e.g. 0001-01-01),
            # which overflows tz conversion. Skip anything unparseable rather
            # than aborting the whole scan.
            raw_dt = perf.get("dateTime")
            if not raw_dt:
                continue
            try:
                dt = parse_iso(raw_dt)
                day = local_day(dt)
            except (ValueError, OverflowError, OSError):
                continue
            if day not in allowed:
                continue

            status = perf.get("ticketStatus") or ""
            sold_out = bool(perf.get("soldOut"))
            if not sold_out and status not in CHECKABLE_STATUSES:
                continue

            local = dt.astimezone(EDINBURGH)
            price_types = event.get("priceType") or []
            if isinstance(price_types, str):
                price_types = [price_types]
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
                price_types=[str(p) for p in price_types if p],
            )
    return list(rows_by_perf.values())


async def enrich_with_prices(
    api: FringeClient,
    rows: list[PerformanceRow],
    *,
    concurrency: int = 25,
    nearly_threshold: int = 20,
    deadline: float | None = None,
) -> list[PerformanceRow]:
    """Classify every row; always keep it (sold_out / nearly / available).

    `deadline` is an absolute time.time() epoch. Once passed, remaining rows
    are not price-checked: they get the listing-only fallback label and
    `unchecked=True`, so the run always finishes inside its Lambda budget
    instead of being hard-killed (which leaks the watchlist lock and writes
    nothing to S3). Rows whose lookup fails are marked `unchecked` too —
    alert logic must not treat either as a real reopen.
    """
    sem = asyncio.Semaphore(concurrency)
    done = 0
    failed = 0
    skipped = 0
    total = len(rows)
    lock = asyncio.Lock()

    async def tick() -> None:
        nonlocal done
        async with lock:
            done += 1
            if done % 200 == 0 or done == total:
                print(f"  checked availability {done}/{total}", flush=True)

    async def one(row: PerformanceRow) -> None:
        nonlocal failed, skipped
        if row.sold_out_flag or row.ticket_status in SOLD_OUT_STATUSES:
            if row.ticket_status in SOLD_OUT_STATUSES and row.percent_remaining is None:
                row.percent_remaining = 0
            row.availability = "sold_out"
            await tick()
            return

        if not row.box_office_id:
            row.availability = "available"
            await tick()
            return

        async with sem:
            if deadline is not None and time.time() >= deadline:
                row.availability = "available"
                row.unchecked = True
                skipped += 1
                await tick()
                return
            try:
                data = await api.graphql(
                    PRICES_QUERY, {"performanceId": row.box_office_id}
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  warn: prices failed for {row.box_office_id}: {exc}", flush=True)
                row.availability = "available"
                row.unchecked = True
                failed += 1
                await tick()
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

        await tick()

    await asyncio.gather(*(one(r) for r in rows))
    if failed or skipped:
        print(
            f"  availability check degraded: {failed} lookups failed, "
            f"{skipped} skipped past deadline (of {total})",
            flush=True,
        )
    return rows


def summarize_shows(rows: list[PerformanceRow]) -> list[dict[str, Any]]:
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
                "offer_dates": {},
                "performances": [],
            },
        )
        public = row.to_public_dict()
        bucket["performances"].append(public)
        if row.availability == "sold_out":
            bucket["sold_out_dates"].add(row.date_local)
        elif row.availability == "nearly_sold_out":
            bucket["nearly_sold_out_dates"].add(row.date_local)
        else:
            bucket["available_dates"].add(row.date_local)

        for offer in public.get("offers") or []:
            day_offers = bucket["offer_dates"].setdefault(row.date_local, [])
            label = (offer.get("label") or offer.get("code") or "").strip()
            code = (offer.get("code") or "").strip()
            if not label and not code:
                continue
            if any(
                o.get("code") == code or o.get("label") == label for o in day_offers
            ):
                continue
            day_offers.append(
                {
                    "code": code,
                    "label": label or code,
                    "slug": offer.get("slug") or "",
                }
            )

    shows: list[dict[str, Any]] = []
    for bucket in sorted(by_show.values(), key=lambda b: b["show_title"].lower()):
        sold = sorted(bucket["sold_out_dates"])
        nearly = sorted(bucket["nearly_sold_out_dates"])
        avail = sorted(bucket["available_dates"])
        offer_dates = {
            day: offers
            for day, offers in sorted(bucket["offer_dates"].items())
            if offers
        }
        shows.append(
            {
                "show_title": bucket["show_title"],
                "slug": bucket["slug"],
                "genre": bucket["genre"],
                "venue": bucket["venue"],
                "url": bucket["url"],
                "performances_in_window": len(bucket["performances"]),
                "sold_out_dates": sold,
                "nearly_sold_out_dates": nearly,
                "available_dates": avail,
                "offer_dates": offer_dates,
                "any_sold_out": bool(sold),
                "any_nearly_sold_out": bool(nearly),
                "any_offers": bool(offer_dates),
                "performances": sorted(
                    bucket["performances"],
                    key=lambda p: (p["date"], p["time"]),
                ),
            }
        )
    return shows


def build_latest_payload(
    rows: list[PerformanceRow],
    *,
    start: date,
    end: date,
    nearly_threshold: int,
    offers_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shows = summarize_shows(rows)
    sold = sum(1 for r in rows if r.availability == "sold_out")
    nearly = sum(1 for r in rows if r.availability == "nearly_sold_out")
    available = sum(1 for r in rows if r.availability == "available")
    with_offers = sum(1 for r in rows if r.offers)
    return {
        "fetched_at": datetime.now(tz=EDINBURGH).isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "nearly_threshold": nearly_threshold,
        "show_count": len(shows),
        "performance_count": len(rows),
        "counts": {
            "sold_out": sold,
            "nearly_sold_out": nearly,
            "available": available,
            "shows_with_sold_out": sum(1 for s in shows if s["any_sold_out"]),
            "performances_with_offers": with_offers,
            "shows_with_offers": sum(1 for s in shows if s.get("any_offers")),
        },
        "offers": offers_meta or {},
        "shows": shows,
    }


def watch_candidates_from_shows(shows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build watchlist entries from sold-out / nearly-sold-out performances."""
    out: list[dict[str, Any]] = []
    for show in shows:
        for perf in show.get("performances") or []:
            if perf.get("availability") in {"sold_out", "nearly_sold_out"}:
                out.append(
                    {
                        "slug": show["slug"],
                        "show_title": show["show_title"],
                        "performance_id": perf["performance_id"],
                        "box_office_id": perf.get("box_office_id"),
                        "date": perf["date"],
                        "time": perf["time"],
                        "availability": perf["availability"],
                        "url": show.get("url") or "",
                        "source": "auto",
                    }
                )
    return out
