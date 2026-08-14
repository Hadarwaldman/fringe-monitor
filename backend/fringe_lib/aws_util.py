from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

DEFAULT_START = os.environ.get("DEFAULT_START_DATE", "2026-08-12")
DEFAULT_END = os.environ.get("DEFAULT_END_DATE", "2026-08-20")
DEFAULT_EMAIL = os.environ.get("NOTIFY_EMAIL", "hadarwaldman@gmail.com")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def s3_client():
    return boto3.client("s3")


def dynamodb_table(name: str | None = None):
    table_name = name or env("TABLE_NAME")
    return boto3.resource("dynamodb").Table(table_name)


def ses_client():
    return boto3.client("sesv2")


def put_json_s3(bucket: str, key: str, payload: dict[str, Any]) -> None:
    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, default=_json_default).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-cache",
    )


def get_json_s3(bucket: str, key: str) -> dict[str, Any] | None:
    try:
        obj = s3_client().get_object(Bucket=bucket, Key=key)
    except s3_client().exceptions.NoSuchKey:
        return None
    except Exception as exc:  # noqa: BLE001
        if "NoSuchKey" in type(exc).__name__ or "NoSuchKey" in str(exc):
            return None
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def get_config(table=None) -> dict[str, Any]:
    table = table or dynamodb_table()
    resp = table.get_item(Key={"pk": "CONFIG", "sk": "MAIN"})
    item = resp.get("Item") or {}
    return {
        "start_date": item.get("start_date") or DEFAULT_START,
        "end_date": item.get("end_date") or DEFAULT_END,
        "nearly_threshold": int(item.get("nearly_threshold") or 20),
        "notify_email": item.get("notify_email") or DEFAULT_EMAIL,
        "auto_watch_sold_out": bool(item.get("auto_watch_sold_out", True)),
    }


def put_config(config: dict[str, Any], table=None) -> dict[str, Any]:
    table = table or dynamodb_table()
    merged = get_config(table)
    merged.update({k: v for k, v in config.items() if v is not None})
    table.put_item(
        Item={
            "pk": "CONFIG",
            "sk": "MAIN",
            "start_date": merged["start_date"],
            "end_date": merged["end_date"],
            "nearly_threshold": int(merged["nearly_threshold"]),
            "notify_email": merged["notify_email"],
            "auto_watch_sold_out": bool(merged.get("auto_watch_sold_out", True)),
        }
    )
    # Mirror for the static frontend (CloudFront /data/config.json).
    try:
        put_json_s3(env("DATA_BUCKET"), "data/config.json", merged)
    except Exception as exc:  # noqa: BLE001
        print(f"warn: could not mirror config to S3: {exc}", flush=True)
    return merged


def list_watchlist(table=None) -> list[dict[str, Any]]:
    table = table or dynamodb_table()
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq("WATCHLIST"),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items") or [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    # Normalize decimals
    return json.loads(json.dumps(items, default=_json_default))


def upsert_watch_items(items: list[dict[str, Any]], table=None) -> int:
    table = table or dynamodb_table()
    count = 0
    with table.batch_writer() as batch:
        for item in items:
            perf_id = item.get("performance_id")
            if perf_id is None:
                continue
            batch.put_item(
                Item={
                    "pk": "WATCHLIST",
                    "sk": str(perf_id),
                    "performance_id": int(perf_id),
                    "slug": item.get("slug") or "",
                    "show_title": item.get("show_title") or "",
                    "box_office_id": item.get("box_office_id") or "",
                    "date": item.get("date") or "",
                    "time": item.get("time") or "",
                    "availability": item.get("availability") or "",
                    "url": item.get("url") or "",
                    "source": item.get("source") or "manual",
                }
            )
            count += 1
    return count


def replace_auto_watchlist(items: list[dict[str, Any]], table=None) -> int:
    """Replace auto-sourced watch items; keep manual ones."""
    table = table or dynamodb_table()
    existing = list_watchlist(table)
    manual = [i for i in existing if i.get("source") == "manual"]
    auto_sks = {i["sk"] for i in existing if i.get("source") != "manual"}

    with table.batch_writer() as batch:
        for sk in auto_sks:
            batch.delete_item(Key={"pk": "WATCHLIST", "sk": sk})

    # Don't overwrite manual entries for the same performance.
    manual_ids = {str(i.get("performance_id")) for i in manual}
    auto_only = [i for i in items if str(i.get("performance_id")) not in manual_ids]
    return upsert_watch_items(auto_only + manual, table)


def list_monitors(table=None) -> list[dict[str, Any]]:
    table = table or dynamodb_table()
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq("MONITOR"),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items") or [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return json.loads(json.dumps(items, default=_json_default))


def get_monitor(monitor_id: str, table=None) -> dict[str, Any] | None:
    table = table or dynamodb_table()
    resp = table.get_item(Key={"pk": "MONITOR", "sk": str(monitor_id)})
    item = resp.get("Item")
    if not item:
        return None
    return json.loads(json.dumps(item, default=_json_default))


def put_monitor(monitor: dict[str, Any], table=None) -> None:
    table = table or dynamodb_table()
    table.put_item(
        Item={
            "pk": "MONITOR",
            "sk": str(monitor["monitor_id"]),
            **monitor,
        }
    )


def patch_monitor(monitor_id: str, fields: dict[str, Any], table=None) -> None:
    if not fields:
        return
    table = table or dynamodb_table()
    names = {f"#f{i}": key for i, key in enumerate(fields)}
    values = {f":v{i}": value for i, value in enumerate(fields.values())}
    expr = ", ".join(f"#f{i} = :v{i}" for i in range(len(fields)))
    table.update_item(
        Key={"pk": "MONITOR", "sk": str(monitor_id)},
        UpdateExpression=f"SET {expr}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def delete_monitor(monitor_id: str, table=None) -> None:
    table = table or dynamodb_table()
    table.delete_item(Key={"pk": "MONITOR", "sk": str(monitor_id)})


def send_monitor_email(
    *,
    to_address: str,
    from_address: str,
    monitor: dict[str, Any],
    openings: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    hold: dict[str, Any] | None,
) -> None:
    show = monitor.get("show_title") or monitor.get("slug") or "show"
    lines = [
        f"Fringe Monitor: tickets are available for {show}!",
        f"Monitored range: {monitor.get('start_date')} → {monitor.get('end_date')}",
        "",
        "Newly available:",
    ]
    for item in openings:
        rem = item.get("percent_remaining")
        rem_txt = f", ~{rem}% remaining" if rem is not None else ""
        lines.append(
            f"- {item.get('date')} {item.get('time')} ({item.get('availability')}{rem_txt})"
        )

    if hold is not None:
        lines.append("")
        if hold.get("success"):
            lines.extend(
                [
                    f"✅ {hold.get('quantity')} ticket(s) for {hold.get('date')} "
                    f"{hold.get('time')} are HELD in your edfringe basket "
                    f"({hold.get('price_band') or 'full price'}, £{hold.get('unit_price')}).",
                    f"Basket total: £{hold.get('basket_total')} — expires in "
                    f"{hold.get('expires_in') or '~30 minutes'}.",
                    "Log in at https://www.edfringe.com and open your basket to "
                    "complete the purchase before it expires.",
                ]
            )
        else:
            lines.append(
                f"⚠️ Could not hold tickets automatically: {hold.get('error')}. "
                "Book manually via the link below."
            )

    if monitor.get("url"):
        lines.extend(["", f"Book here: {monitor['url']}"])

    if statuses:
        lines.extend(["", "All monitored performances right now:"])
        for s in statuses:
            rem = s.get("percent_remaining")
            rem_txt = f" (~{rem}% left)" if rem is not None else ""
            lines.append(
                f"- {s.get('date')} {s.get('time')}: {s.get('availability')}{rem_txt}"
            )

    lines.extend(["", "— fringe-monitor"])
    held = hold.get("success") if hold else False
    subject = (
        f"[fringe-monitor] {'Tickets HELD' if held else 'Tickets available'}: {show}"
    )
    ses_client().send_email(
        FromEmailAddress=from_address,
        Destination={"ToAddresses": [to_address]},
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": "\n".join(lines), "Charset": "UTF-8"}},
            }
        },
    )


def get_alert_state(performance_id: int | str, table=None) -> dict[str, Any] | None:
    table = table or dynamodb_table()
    resp = table.get_item(Key={"pk": "ALERT", "sk": str(performance_id)})
    item = resp.get("Item")
    if not item:
        return None
    return json.loads(json.dumps(item, default=_json_default))


def put_alert_state(
    performance_id: int | str,
    *,
    availability: str,
    notified: bool,
    table=None,
) -> None:
    table = table or dynamodb_table()
    table.put_item(
        Item={
            "pk": "ALERT",
            "sk": str(performance_id),
            "performance_id": int(performance_id),
            "availability": availability,
            "notified": notified,
        }
    )


def send_reopen_email(
    *,
    to_address: str,
    from_address: str,
    openings: list[dict[str, Any]],
) -> None:
    if not openings:
        return
    lines = [
        "Fringe Monitor: tickets appear to have opened up for:",
        "",
    ]
    for item in openings:
        lines.append(
            f"- {item.get('show_title')} on {item.get('date')} {item.get('time')} "
            f"({item.get('previous')} → {item.get('availability')})"
        )
        if item.get("url"):
            lines.append(f"  {item['url']}")
    lines.extend(["", "— fringe-monitor"])
    body = "\n".join(lines)
    ses_client().send_email(
        FromEmailAddress=from_address,
        Destination={"ToAddresses": [to_address]},
        Content={
            "Simple": {
                "Subject": {
                    "Data": f"[fringe-monitor] {len(openings)} show(s) opened up",
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Text": {"Data": body, "Charset": "UTF-8"},
                },
            }
        },
    )
