"""Lightweight show-monitor check — runs every few minutes.

Unlike the 15-min watchlist job, this does NOT fetch the whole programme. For
each active monitor it queries performancePrices for the monitor's stored
box-office IDs only (seeded at creation or on first run), so a frequent cadence
stays cheap. Availability alerts are handled by the shared run_monitor_checks.

Shares the watchlist DynamoDB lock so it can't race with the 15-min job.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fringe_lib.aws_util import (
    acquire_watchlist_lock,
    get_config,
    list_monitors,
    release_watchlist_lock,
)
from fringe_lib.proxy import load_proxy_into_env
from fringe_lib.client import FringeClient, make_async_client
from fringe_lib.monitors import run_monitor_checks


async def run_monitor_only_check() -> dict[str, Any]:
    config = get_config()
    monitors = [m for m in list_monitors() if m.get("active", True)]
    if not monitors:
        print("No active monitors; nothing to check", flush=True)
        return {"ok": True, "monitors_checked": 0}

    print(f"Monitor check: {len(monitors)} active monitor(s)", flush=True)
    load_proxy_into_env()
    async with make_async_client() as client:
        api = FringeClient(client)
        await api.authenticate()
        # events=None → run_monitor_checks fetches the programme only if some
        # monitor has no seeded box-office IDs (first run), then self-seeds.
        result = await run_monitor_checks(api, None, config, monitors)

    print(json.dumps({"ok": True, **result}), flush=True)
    return {"ok": True, **result}


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    if not acquire_watchlist_lock():
        print("Watchlist/monitor run already in progress; skipping.", flush=True)
        return {"ok": True, "skipped": "locked"}
    try:
        return asyncio.run(run_monitor_only_check())
    finally:
        release_watchlist_lock()
