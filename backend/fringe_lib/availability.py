"""Cheap, targeted availability lookups.

The daily full scan classifies the whole programme; these helpers classify a
*specific* set of performances (by box-office id) without any programme fetch —
used by the show monitors, the wishlist-freshness job, and the live search
endpoint. Bandwidth is a couple hundred bytes per performance.

AWS-free: safe to import from the local CLI venv.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .client import PRICES_QUERY
from .scan import classify_availability


async def classify_box_office_ids(
    api,
    box_office_ids: list[str],
    *,
    nearly_threshold: int = 20,
    concurrency: int = 10,
) -> dict[str, dict[str, Any]]:
    """Return {box_office_id: {availability, percent_remaining, availability_level}}
    via direct performancePrices lookups. Missing/failed ids default to
    'available' so a transient error never reads as sold out."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, dict[str, Any]] = {}

    async def one(box_id: str) -> None:
        if not box_id:
            return
        async with sem:
            try:
                data = await api.graphql(PRICES_QUERY, {"performanceId": box_id})
                result = (data["performancePrices"].get("result") or {})
                pct = result.get("performancePercentageRemaining")
                level = result.get("performanceAvailabilityLevel")
                availability = classify_availability(
                    sold_out=False,
                    ticket_status="",
                    percent_remaining=pct,
                    availability_level=level,
                    nearly_threshold=nearly_threshold,
                )
                out[box_id] = {
                    "availability": availability,
                    "percent_remaining": pct,
                    "availability_level": level,
                }
            except Exception as exc:  # noqa: BLE001
                print(f"  warn: availability lookup failed for {box_id}: {exc}", flush=True)
                out[box_id] = {
                    "availability": "available",
                    "percent_remaining": None,
                    "availability_level": None,
                    "error": str(exc),
                }

    await asyncio.gather(*(one(b) for b in dict.fromkeys(box_office_ids)))
    return out


def apply_availability(
    perfs: list[dict[str, Any]], avail: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Fold fresh readings into a show's performances; return buckets + readings.

    Updates `perfs` in place and reports both the per-date buckets the UI
    filters on and the readings themselves. The readings used to be computed
    and dropped — only the buckets were persisted — so freshly measured
    percentages never reached the frontend.

    Performances missing from `avail` were not re-checked: they keep their
    existing value and are deliberately absent from `performances`, so nothing
    downstream mistakes a stale number for a fresh one.
    """
    sold, nearly, available = set(), set(), set()
    checked: list[dict[str, Any]] = []
    for perf in perfs:
        box_id = perf.get("box_office_id") or ""
        fresh = avail.get(box_id)
        status = (
            fresh["availability"]
            if fresh
            else (perf.get("availability") or "available")
        )
        if fresh:
            perf["availability"] = fresh["availability"]
            perf["percent_remaining"] = fresh["percent_remaining"]
            checked.append(
                {
                    "box_office_id": box_id,
                    "date": perf.get("date"),
                    "time": perf.get("time"),
                    "availability": fresh["availability"],
                    "percent_remaining": fresh["percent_remaining"],
                }
            )
        day = perf.get("date")
        if not day:
            continue
        if status == "sold_out":
            sold.add(day)
        elif status == "nearly_sold_out":
            nearly.add(day)
        else:
            available.add(day)
    checked.sort(key=lambda p: (str(p.get("date") or ""), str(p.get("time") or "")))
    return {
        "sold_out_dates": sorted(sold),
        "nearly_sold_out_dates": sorted(nearly),
        "available_dates": sorted(available),
        "performances": checked,
    }
