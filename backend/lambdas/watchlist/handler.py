from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

from fringe_lib.aws_util import (
    acquire_watchlist_lock,
    env,
    get_alert_state,
    get_config,
    list_monitors,
    list_watchlist,
    put_alert_state,
    release_watchlist_lock,
    send_reopen_email,
    upsert_watch_items,
)
from fringe_lib.proxy import load_proxy_into_env
from fringe_lib.client import FringeClient, make_async_client
from fringe_lib.monitors import run_monitor_checks
from fringe_lib.scan import (
    collect_window_rows,
    enrich_with_prices,
    fetch_all_programme,
)


async def run_watchlist_check() -> dict[str, Any]:
    config = get_config()
    start = date.fromisoformat(config["start_date"])
    end = date.fromisoformat(config["end_date"])
    nearly = int(config["nearly_threshold"])
    notify_email = config["notify_email"]
    from_email = env("FROM_EMAIL", notify_email)

    watched = list_watchlist()
    monitors = [m for m in list_monitors() if m.get("active", True)]
    if not watched and not monitors:
        print("Watchlist and monitors empty; nothing to check", flush=True)
        return {"ok": True, "checked": 0, "openings": 0, "monitors_checked": 0}

    slug_set = {w["slug"] for w in watched if w.get("slug")}
    watched_ids = {int(w["performance_id"]) for w in watched if w.get("performance_id") is not None}
    previous_by_id = {
        int(w["performance_id"]): w.get("availability") or ""
        for w in watched
        if w.get("performance_id") is not None
    }

    print(
        f"Watchlist check: {len(watched)} items / {len(slug_set)} shows; "
        f"{len(monitors)} active monitor(s); window {start} → {end}",
        flush=True,
    )

    monitor_result: dict[str, Any] = {"monitors_checked": 0}
    load_proxy_into_env()
    async with make_async_client() as client:
        api = FringeClient(client)
        await api.authenticate()
        # Listing-only programme fetch (shared by watchlist + monitors), then
        # price-enrich just the slugs we care about.
        events = await fetch_all_programme(api, page_size=500)
        rows = collect_window_rows(events, start, end, slugs=slug_set)
        rows = [r for r in rows if r.performance_id in watched_ids]
        print(f"Re-checking {len(rows)} watched performances…", flush=True)
        classified = await enrich_with_prices(
            api,
            rows,
            concurrency=20,
            nearly_threshold=nearly,
        )
        if monitors:
            monitor_result = await run_monitor_checks(
                api, events, config, monitors
            )

    openings: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for row in classified:
        prev = previous_by_id.get(row.performance_id, "")
        updates.append(
            {
                "performance_id": row.performance_id,
                "slug": row.slug,
                "show_title": row.show_title,
                "box_office_id": row.box_office_id,
                "date": row.date_local,
                "time": row.time_local,
                "availability": row.availability,
                "url": row.url,
                "source": "auto",
            }
        )
        reopened = prev in {"sold_out", "nearly_sold_out"} and row.availability == "available"
        if not reopened:
            put_alert_state(
                row.performance_id,
                availability=row.availability,
                notified=False,
            )
            continue

        state = get_alert_state(row.performance_id) or {}
        already = (
            state.get("notified")
            and state.get("availability") == "available"
            and prev != "available"
        )
        # Notify once per transition into available.
        if state.get("notified") and state.get("availability") == row.availability:
            continue

        openings.append(
            {
                "performance_id": row.performance_id,
                "show_title": row.show_title,
                "date": row.date_local,
                "time": row.time_local,
                "previous": prev,
                "availability": row.availability,
                "url": row.url,
            }
        )
        put_alert_state(
            row.performance_id,
            availability=row.availability,
            notified=True,
        )
        _ = already  # kept for clarity / future tuning

    # Preserve manual source flags where possible.
    source_by_id = {
        int(w["performance_id"]): w.get("source") or "auto"
        for w in watched
        if w.get("performance_id") is not None
    }
    for item in updates:
        item["source"] = source_by_id.get(int(item["performance_id"]), "auto")
    upsert_watch_items(updates)

    if openings:
        send_reopen_email(
            to_address=notify_email,
            from_address=from_email,
            openings=openings,
        )

    result = {
        "ok": True,
        "checked": len(classified),
        "openings": len(openings),
        "emailed": bool(openings),
        **monitor_result,
    }
    print(json.dumps(result), flush=True)
    return result


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    # Serialize runs via a DynamoDB lock — the 15-min schedule and manual
    # /monitors/check must not overlap, or they race on MONITOR state.
    if not acquire_watchlist_lock():
        print("Another watchlist run holds the lock; skipping this invocation.", flush=True)
        return {"ok": True, "skipped": "locked"}
    try:
        return asyncio.run(run_watchlist_check())
    finally:
        release_watchlist_lock()
