"""Live, on-demand availability lookups for specific shows.

The daily/hourly full scan is the bulk path: it walks the whole programme and
writes `data/latest.json`. This module is the opposite — a cheap, targeted
"what is the truth *right now* for this one show?" query, built on the same
primitive the lightweight monitor check uses: a direct
`performancePrices(box_office_id)` call per performance, with no programme
fetch at all.

Two halves, deliberately separated so the expensive half is optional:

* **Resolution** (`match_shows` / `window_performances`) is pure and offline —
  it picks a show out of a cached scan payload. Callers that already know the
  box-office IDs (the frontend, a monitor, the API route) skip it entirely.
* **Lookup** (`check_box_office_ids`) is the only part that touches the
  network, so it is the only part that needs the residential proxy.

In AWS every caller must go through the residential proxy — Cloudflare 403s
datacenter IPs — so callers run `cart.load_proxy_into_env()` before building
the client, exactly as the scan and monitor Lambdas do.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Iterable

from .client import PRICES_QUERY
from .models import PerformanceRow

DEFAULT_CONCURRENCY = 10

# A single show rarely has more than a couple of dozen performances in a
# window; the cap stops a pathological query from spraying the proxy.
MAX_PERFORMANCES = 60


def normalize_query(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so "Cathy!" and
    "cathy" match the same show."""
    lowered = (text or "").casefold()
    lowered = re.sub(r"[^\w\s-]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def match_shows(
    latest: dict[str, Any],
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Resolve a free-text query to shows in a cached scan payload.

    Ranked: exact slug, then exact title, then slug substring, then title
    substring. Returns the raw show dicts from `latest.json` (which carry
    `performances` with the box-office IDs the live lookup needs).
    """
    shows = latest.get("shows") or []
    if isinstance(shows, dict):
        shows = list(shows.values())

    needle = normalize_query(query)
    if not needle:
        return []

    exact_slug: list[dict[str, Any]] = []
    exact_title: list[dict[str, Any]] = []
    partial_slug: list[dict[str, Any]] = []
    partial_title: list[dict[str, Any]] = []

    for show in shows:
        slug = str(show.get("slug") or "")
        title_norm = normalize_query(str(show.get("show_title") or ""))
        slug_norm = normalize_query(slug.replace("-", " "))

        if slug.casefold() == query.casefold().strip():
            exact_slug.append(show)
        elif title_norm == needle:
            exact_title.append(show)
        elif needle.replace(" ", "-") in slug.casefold():
            partial_slug.append(show)
        elif needle in title_norm:
            partial_title.append(show)

    ordered = exact_slug + exact_title + partial_slug + partial_title
    return ordered[:limit]


def window_performances(
    show: dict[str, Any],
    *,
    start: str | None = None,
    end: str | None = None,
    dates: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Performances of one cached show inside a date filter, sorted by when
    they happen. `dates` (exact days) wins over the `start`/`end` range."""
    wanted = set(dates or [])
    out: list[dict[str, Any]] = []
    for perf in show.get("performances") or []:
        day = str(perf.get("date") or "")
        if wanted:
            if day not in wanted:
                continue
        else:
            if start and day < start:
                continue
            if end and day > end:
                continue
        out.append(perf)
    out.sort(key=lambda p: (str(p.get("date") or ""), str(p.get("time") or "")))
    return out


def _row_from_spec(spec: dict[str, Any], meta: dict[str, Any]) -> PerformanceRow:
    """Build an unclassified row from a performance spec + show-level metadata.

    A spec is anything carrying `performance_id` and `box_office_id` — a
    performance out of `latest.json`, a monitor's seeded list, or a JSON body
    posted by the frontend."""
    return PerformanceRow(
        show_title=str(spec.get("show_title") or meta.get("show_title") or ""),
        slug=str(spec.get("slug") or meta.get("slug") or ""),
        genre=str(spec.get("genre") or meta.get("genre") or ""),
        venue=str(spec.get("venue") or meta.get("venue") or ""),
        performance_id=int(spec["performance_id"]),
        performance_title=str(spec.get("performance_title") or ""),
        date_local=str(spec.get("date") or ""),
        time_local=str(spec.get("time") or ""),
        datetime_utc=str(spec.get("datetime_utc") or ""),
        ticket_status="",
        sold_out_flag=False,
        box_office_id=spec.get("box_office_id"),
        url=str(spec.get("url") or meta.get("url") or ""),
        offers=spec.get("offers") or None,
    )


async def check_box_office_ids(
    api,
    specs: list[dict[str, Any]],
    *,
    nearly_threshold: int = 20,
    concurrency: int = DEFAULT_CONCURRENCY,
    meta: dict[str, Any] | None = None,
) -> list[PerformanceRow]:
    """Classify each performance from a live `performancePrices` lookup.

    No programme fetch — one GraphQL call per performance, bounded by
    `concurrency` so a burst never trips the shared proxy's rate limit.

    A performance with no box-office ID, or whose lookup fails, is left as
    `available` with `percent_remaining` None. That is deliberately the same
    fail-open behaviour the monitor check has always had (never alert on a
    network blip); callers that need to tell "genuinely available" from
    "could not check" should treat a null `percent_remaining` as unknown.
    """
    from .scan import classify_availability

    sem = asyncio.Semaphore(max(1, concurrency))
    meta = meta or {}

    async def one(spec: dict[str, Any]) -> PerformanceRow:
        row = _row_from_spec(spec, meta)
        if not row.box_office_id:
            row.availability = "available"
            return row
        async with sem:
            try:
                data = await api.graphql(
                    PRICES_QUERY, {"performanceId": row.box_office_id}
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  warn: price lookup failed for {row.box_office_id}: {exc}",
                    flush=True,
                )
                row.availability = "available"
                return row
        result = (data["performancePrices"].get("result") or {})
        row.percent_remaining = result.get("performancePercentageRemaining")
        row.availability_level = result.get("performanceAvailabilityLevel")
        row.availability = classify_availability(
            sold_out=False,
            ticket_status="",
            percent_remaining=row.percent_remaining,
            availability_level=row.availability_level,
            nearly_threshold=nearly_threshold,
        )
        return row

    rows = await asyncio.gather(*(one(s) for s in specs[:MAX_PERFORMANCES]))
    return sorted(rows, key=lambda r: (r.date_local, r.time_local))


def summarize(rows: list[PerformanceRow]) -> dict[str, Any]:
    """Counts + the first buyable performance, for a one-line verdict."""
    counts: dict[str, int] = {"sold_out": 0, "nearly_sold_out": 0, "available": 0}
    for row in rows:
        counts[row.availability] = counts.get(row.availability, 0) + 1
    buyable = [r for r in rows if r.availability != "sold_out"]
    return {
        "checked": len(rows),
        "counts": counts,
        "any_buyable": bool(buyable),
        "first_buyable": buyable[0].to_public_dict() if buyable else None,
    }
