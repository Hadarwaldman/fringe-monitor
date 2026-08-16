"""Wishlist-freshness job — runs every ~15 minutes.

Keeps availability current for ONLY the user's PlanMyFringe wishlist shows,
using cheap per-performance price lookups (no full programme fetch). This
replaces the old whole-programme 15-min watchlist, which re-scanned ~15,000
performances every cycle and dominated proxy bandwidth.

Flow: read data/planner.json (wishlist slugs, written by the PMF sync) and
data/latest.json (box-office ids per performance, from the daily scan) →
classify each wishlist show's performances → write refreshed availability back
into data/planner.json so the site's wishlist view shows fresh sold-out status,
and append the readings to data/wishlist_history.json as a retained
sell-through time series.

Shares the watchlist DynamoDB lock so it can't race other checks.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fringe_lib.availability import classify_box_office_ids
from fringe_lib.aws_util import (
    acquire_watchlist_lock,
    env,
    get_config,
    get_json_s3,
    put_json_s3,
    release_watchlist_lock,
)
from fringe_lib.client import FringeClient, make_async_client
from fringe_lib.proxy import load_proxy_into_env
from fringe_lib.wishlist_history import collect_samples, merge_samples


def _perfs_by_slug(latest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for show in latest.get("shows") or []:
        slug = show.get("slug")
        if slug:
            out[slug] = show.get("performances") or []
    return out


def _apply_availability(
    perfs: list[dict[str, Any]], avail: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Recompute per-date buckets for one show from fresh availability.

    Also returns the per-performance readings themselves. They used to be
    computed, written onto the in-memory copy of latest.json and then dropped
    on the floor — only the date buckets survived, so the freshly measured
    percentages never reached the UI and latest.json stayed a day old. They now
    ride along in planner.json, which is small (wishlist shows only) and
    already loaded by My Fringe.
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


async def run_wishlist_refresh() -> dict[str, Any]:
    config = get_config()
    nearly = int(config.get("nearly_threshold") or 20)
    data_bucket = env("DATA_BUCKET")

    planner = get_json_s3(data_bucket, "data/planner.json")
    if not planner or not planner.get("wishlist"):
        print("No planner.json / wishlist; nothing to refresh", flush=True)
        return {"ok": True, "wishlist_shows": 0}

    latest = get_json_s3(data_bucket, "data/latest.json") or {}
    perfs_by_slug = _perfs_by_slug(latest)

    wishlist = planner["wishlist"]
    # Gather box-office ids for wishlist shows that matched a scanned show.
    box_ids: list[str] = []
    for entry in wishlist:
        slug = entry.get("slug")
        for perf in perfs_by_slug.get(slug, []):
            if perf.get("box_office_id"):
                box_ids.append(perf["box_office_id"])

    if not box_ids:
        print("Wishlist has no matched performances to check", flush=True)
        return {"ok": True, "wishlist_shows": len(wishlist), "checked": 0}

    print(
        f"Wishlist refresh: {len(wishlist)} shows / {len(box_ids)} performances",
        flush=True,
    )
    load_proxy_into_env()
    async with make_async_client() as client:
        api = FringeClient(client)
        await api.authenticate()
        avail = await classify_box_office_ids(api, box_ids, nearly_threshold=nearly)

    refreshed = 0
    refreshed_slugs: set[str] = set()
    for entry in wishlist:
        perfs = perfs_by_slug.get(entry.get("slug"), [])
        if not perfs:
            continue
        entry.update(_apply_availability(perfs, avail))
        refreshed += 1
        if entry.get("slug"):
            refreshed_slugs.add(str(entry["slug"]))

    at = _now_iso()
    planner["wishlist_refreshed_at"] = at
    put_json_s3(data_bucket, "data/planner.json", planner)

    # Retain the readings so sell-through can be analysed later. Separate
    # object because it accumulates: planner.json is replaced wholesale by
    # every PlanMyFringe sync, and the frontend loads it on every visit.
    history = merge_samples(
        get_json_s3(data_bucket, "data/wishlist_history.json"),
        collect_samples(perfs_by_slug, refreshed_slugs),
        at=at,
    )
    put_json_s3(data_bucket, "data/wishlist_history.json", history)

    result = {
        "ok": True,
        "wishlist_shows": len(wishlist),
        "refreshed": refreshed,
        "performances_checked": len(box_ids),
        "history_performances": history["performance_count"],
        "history_samples": history["sample_count"],
        "history_appended": history["appended"],
    }
    print(result, flush=True)
    return result


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    if not acquire_watchlist_lock():
        print("Another check holds the lock; skipping.", flush=True)
        return {"ok": True, "skipped": "locked"}
    try:
        return asyncio.run(run_wishlist_refresh())
    finally:
        release_watchlist_lock()
