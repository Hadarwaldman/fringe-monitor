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
