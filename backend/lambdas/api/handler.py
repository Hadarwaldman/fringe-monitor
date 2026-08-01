from __future__ import annotations

import json
import os
from typing import Any

import boto3

from fringe_lib.aws_util import (
    get_config,
    list_watchlist,
    put_config,
    upsert_watch_items,
)


def _response(status: int, body: Any, *, cors: bool = True) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if cors:
        headers.update(
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "content-type",
                "Access-Control-Allow-Methods": "GET,PUT,POST,OPTIONS",
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
