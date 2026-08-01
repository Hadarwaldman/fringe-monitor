from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import httpx

from fringe_lib.aws_util import (
    env,
    get_alert_state,
    get_config,
    list_watchlist,
    put_alert_state,
    send_reopen_email,
    upsert_watch_items,
)
from fringe_lib.client import FringeClient
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
    if not watched:
        print("Watchlist empty; nothing to check", flush=True)
        return {"ok": True, "checked": 0, "openings": 0}

    slug_set = {w["slug"] for w in watched if w.get("slug")}
    watched_ids = {int(w["performance_id"]) for w in watched if w.get("performance_id") is not None}
    previous_by_id = {
        int(w["performance_id"]): w.get("availability") or ""
        for w in watched
        if w.get("performance_id") is not None
    }

    print(
        f"Watchlist check: {len(watched)} items / {len(slug_set)} shows; "
        f"window {start} → {end}",
        flush=True,
    )

    limits = httpx.Limits(max_connections=30, max_keepalive_connections=20)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as client:
        api = FringeClient(client)
        await api.authenticate()
        # Listing-only programme fetch, then price-enrich just watched slugs.
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
    }
    print(json.dumps(result), flush=True)
    return result


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    return asyncio.run(run_watchlist_check())
