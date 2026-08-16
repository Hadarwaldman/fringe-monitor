from __future__ import annotations

import json
import os
import re
from typing import Any

import boto3

from fringe_lib.aws_util import (
    delete_monitor,
    env,
    get_config,
    get_json_s3,
    get_monitor,
    list_monitors,
    list_watchlist,
    patch_monitor,
    put_config,
    put_json_s3,
    put_monitor,
    replace_watchlist_source,
    upsert_watch_items,
)
from fringe_lib.proxy import load_proxy_into_env
from fringe_lib.monitors import new_monitor
from fringe_lib.planmyfringe import (
    get_planmyfringe_credentials,
    mask_user_id,
    store_credentials,
    sync_planner,
    verify_credentials,
)


def _response(status: int, body: Any, *, cors: bool = True) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if cors:
        headers.update(
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "content-type",
                "Access-Control-Allow-Methods": "GET,PUT,POST,PATCH,DELETE,OPTIONS",
            }
        )
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body),
    }


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    if not raw:
        return {}
    return json.loads(raw)


def _unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


def _live_show_availability(slug: str) -> dict[str, Any]:
    """Live availability for one show, on demand (search / show open).

    Reads the show's box-office ids from the last scan (data/latest.json) and
    does direct price lookups — no full programme fetch. Cheap enough to run
    per search.
    """
    import asyncio

    from fringe_lib.availability import classify_box_office_ids
    from fringe_lib.client import FringeClient, make_async_client

    latest = get_json_s3(env("DATA_BUCKET"), "data/latest.json") or {}
    show = next(
        (s for s in latest.get("shows") or [] if s.get("slug") == slug), None
    )
    if not show:
        return {"slug": slug, "found": False, "performances": []}

    perfs = show.get("performances") or []
    box_ids = [p["box_office_id"] for p in perfs if p.get("box_office_id")]
    config = get_config()
    nearly = int(config.get("nearly_threshold") or 20)

    async def go() -> dict[str, dict[str, Any]]:
        load_proxy_into_env()
        async with make_async_client() as client:
            api = FringeClient(client)
            await api.authenticate()
            return await classify_box_office_ids(api, box_ids, nearly_threshold=nearly)

    avail = asyncio.run(go()) if box_ids else {}
    out_perfs = []
    for perf in perfs:
        fresh = avail.get(perf.get("box_office_id") or "")
        out_perfs.append(
            {
                "performance_id": perf.get("performance_id"),
                "date": perf.get("date"),
                "time": perf.get("time"),
                "box_office_id": perf.get("box_office_id"),
                "availability": fresh["availability"] if fresh else perf.get("availability"),
                "percent_remaining": (
                    fresh["percent_remaining"] if fresh else perf.get("percent_remaining")
                ),
            }
        )
    return {
        "slug": slug,
        "show_title": show.get("show_title"),
        "found": True,
        "checked_at": _now_iso_api(),
        "performances": out_perfs,
    }


def _now_iso_api() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()
    path = (
        event.get("rawPath")
        or event.get("path")
        or "/"
    )

    if method == "OPTIONS":
        return _response(204, {})

    try:
        if path.endswith("/config") and method == "GET":
            return _response(200, get_config())

        if path.endswith("/config") and method == "PUT":
            body = _parse_body(event)
            allowed = {
                "start_date",
                "end_date",
                "nearly_threshold",
                "notify_email",
                "auto_watch_sold_out",
            }
            patch = {k: body[k] for k in allowed if k in body}
            if "start_date" in patch and "end_date" in patch:
                if patch["end_date"] < patch["start_date"]:
                    return _response(400, {"error": "end_date must be on or after start_date"})
            return _response(200, put_config(patch))

        if path.endswith("/watchlist") and method == "GET":
            return _response(200, {"items": list_watchlist()})

        if path.endswith("/watchlist") and method in {"PUT", "POST"}:
            body = _parse_body(event)
            items = body.get("items") or []
            for item in items:
                item["source"] = item.get("source") or "manual"
            count = upsert_watch_items(items)
            return _response(200, {"upserted": count, "items": list_watchlist()})

        avail_match = re.search(r"/shows/([^/]+)/availability$", path)
        if avail_match and method == "GET":
            slug = _unquote(avail_match.group(1))
            return _response(200, _live_show_availability(slug))

        if path.endswith("/monitors") and method == "GET":
            return _response(200, {"items": list_monitors()})

        if path.endswith("/monitors") and method == "POST":
            body = _parse_body(event)
            required = ["slug", "show_title", "start_date", "end_date"]
            missing = [k for k in required if not body.get(k)]
            if missing:
                return _response(400, {"error": f"missing fields: {', '.join(missing)}"})
            if body["end_date"] < body["start_date"]:
                return _response(400, {"error": "end_date must be on or after start_date"})
            monitor = new_monitor(
                slug=str(body["slug"]),
                show_title=str(body["show_title"]),
                start_date=str(body["start_date"]),
                end_date=str(body["end_date"]),
                url=str(body.get("url") or ""),
                performances=body.get("performances") or [],
            )
            put_monitor(monitor)
            return _response(200, {"monitor": monitor})

        if path.endswith("/monitors/check") and method == "POST":
            # Prefer the lightweight monitor-check function; fall back to the
            # watchlist function if it's not configured.
            fn = os.environ.get("MONITOR_CHECK_FUNCTION_NAME") or os.environ.get(
                "WATCHLIST_FUNCTION_NAME"
            )
            if not fn:
                return _response(500, {"error": "monitor-check function not configured"})
            boto3.client("lambda").invoke(FunctionName=fn, InvocationType="Event")
            return _response(
                202,
                {
                    "ok": True,
                    "started": True,
                    "message": "Check started — refresh in ~1 minute for results.",
                },
            )

        monitor_match = re.search(r"/monitors/([A-Za-z0-9-]+)$", path)
        if monitor_match and method in {"PUT", "PATCH"}:
            monitor_id = monitor_match.group(1)
            if get_monitor(monitor_id) is None:
                return _response(404, {"error": "monitor not found"})
            body = _parse_body(event)
            allowed = {"start_date", "end_date", "active"}
            patch = {k: body[k] for k in allowed if k in body}
            if "active" in patch:
                patch["active"] = bool(patch["active"])
            patch_monitor(monitor_id, patch)
            return _response(200, {"monitor": get_monitor(monitor_id)})

        if monitor_match and method == "DELETE":
            monitor_id = monitor_match.group(1)
            delete_monitor(monitor_id)
            return _response(200, {"deleted": monitor_id})

        if path.endswith("/settings/planmyfringe") and method == "GET":
            creds = get_planmyfringe_credentials()
            return _response(
                200,
                {
                    "configured": creds is not None,
                    "user_id": mask_user_id(creds["user_id"]) if creds else None,
                },
            )

        if path.endswith("/settings/planmyfringe") and method == "PUT":
            body = _parse_body(event)
            user_id = str(body.get("user_id") or "").strip()
            password = str(body.get("password") or "")
            if not user_id or not password:
                return _response(400, {"error": "user_id and password are required"})
            creds = {"user_id": user_id, "password": password}
            load_proxy_into_env()
            try:
                verify_credentials(creds)
            except RuntimeError as exc:
                return _response(401, {"error": str(exc)})
            store_credentials(creds)
            return _response(
                200,
                {"ok": True, "configured": True, "user_id": mask_user_id(user_id)},
            )

        if path.endswith("/planner/sync") and method == "POST":
            load_proxy_into_env()
            creds = get_planmyfringe_credentials()
            if creds is None:
                return _response(
                    409,
                    {
                        "error": "PlanMyFringe credentials not configured — create "
                        "the SSM SecureString named by PLANMYFRINGE_CREDS_PARAM"
                    },
                )
            latest = get_json_s3(env("DATA_BUCKET"), "data/latest.json") or {}
            result = sync_planner(credentials=creds, latest=latest)
            put_json_s3(env("DATA_BUCKET"), "data/planner.json", result["planner"])
            imported = replace_watchlist_source("planmyfringe", result["watch_items"])
            return _response(
                200,
                {
                    "ok": True,
                    "summary": {**result["summary"], "watchlist_imported": imported},
                    "planner": result["planner"],
                },
            )

        if path.endswith("/health") and method == "GET":
            return _response(200, {"ok": True, "service": "fringe-monitor"})

        if path.endswith("/scan") and method == "POST":
            fn = os.environ.get("FULL_SCAN_FUNCTION_NAME")
            if not fn:
                return _response(500, {"error": "FULL_SCAN_FUNCTION_NAME not configured"})
            boto3.client("lambda").invoke(
                FunctionName=fn,
                InvocationType="Event",
            )
            return _response(
                202,
                {
                    "ok": True,
                    "started": True,
                    "message": "Full scan started. Data refreshes when the scan finishes (~a few minutes).",
                },
            )

        return _response(404, {"error": "not found", "path": path})
    except Exception as exc:  # noqa: BLE001
        return _response(500, {"error": str(exc)})
