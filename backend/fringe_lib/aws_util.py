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


def replace_watchlist_source(
    source: str, items: list[dict[str, Any]], table=None
) -> int:
    """Replace watch items from one source (e.g. "planmyfringe"); keep all
    others. Items whose performance is already watched from another source
    are left untouched rather than overwritten."""
    table = table or dynamodb_table()
    existing = list_watchlist(table)
    stale_sks = {i["sk"] for i in existing if i.get("source") == source}
    with table.batch_writer() as batch:
        for sk in stale_sks:
            batch.delete_item(Key={"pk": "WATCHLIST", "sk": sk})

    other_ids = {
        str(i.get("performance_id")) for i in existing if i.get("source") != source
    }
    fresh = [
        {**i, "source": source}
        for i in items
        if str(i.get("performance_id")) not in other_ids
    ]
    return upsert_watch_items(fresh, table)


def acquire_watchlist_lock(*, ttl_seconds: int = 840, table=None) -> bool:
    """Best-effort mutex so watchlist/monitor runs don't overlap and race on
    MONITOR state. Conditional-put a LOCK item that only succeeds if none
    exists or the existing one has expired. Returns True if acquired.

    ttl_seconds should exceed a normal run (~2 min) but be below the 15-min
    cadence so a crashed run self-heals by the next schedule.
    """
    import time

    table = table or dynamodb_table()
    now = int(time.time())
    expires = now + ttl_seconds
    try:
        table.put_item(
            Item={"pk": "LOCK", "sk": "WATCHLIST", "expires_at": expires},
            ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        if "ConditionalCheckFailed" in type(exc).__name__ or "ConditionalCheckFailed" in str(exc):
            return False
        raise


def release_watchlist_lock(table=None) -> None:
    table = table or dynamodb_table()
    try:
        table.delete_item(Key={"pk": "LOCK", "sk": "WATCHLIST"})
    except Exception as exc:  # noqa: BLE001
        print(f"warn: could not release watchlist lock: {exc}", flush=True)


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


def _fmt_avail(value: str | None) -> str:
    return {
        "available": "Available",
        "nearly_sold_out": "Nearly sold out",
        "sold_out": "Sold out",
    }.get(value or "", value or "unknown")


def _monitor_email_text(*, show, monitor, openings, statuses) -> str:
    lines = [f"TICKETS AVAILABLE — {show}", "", f"Book now → {monitor.get('url') or ''}"]
    lines.append("")
    lines.append("Newly available:")
    for item in openings:
        rem = item.get("percent_remaining")
        rem_txt = f" · ~{rem}% left" if rem is not None else ""
        lines.append(
            f"  • {item.get('date')} {item.get('time')} "
            f"({_fmt_avail(item.get('availability'))}{rem_txt})"
        )

    if statuses:
        lines.append("")
        lines.append(
            f"All monitored performances ({monitor.get('start_date')} → "
            f"{monitor.get('end_date')}):"
        )
        for s in statuses:
            rem = s.get("percent_remaining")
            rem_txt = f" · ~{rem}% left" if rem is not None else ""
            lines.append(
                f"  • {s.get('date')} {s.get('time')}: "
                f"{_fmt_avail(s.get('availability'))}{rem_txt}"
            )

    lines.append("")
    lines.append("— fringe-monitor")
    return "\n".join(lines)


def _monitor_email_html(*, show, monitor, openings, statuses) -> str:
    import html

    esc = html.escape
    show_e = esc(str(show))
    row_style = "padding:4px 0;border-bottom:1px solid #eee;font-size:14px;"

    def perf_rows(items: list[dict[str, Any]]) -> str:
        out = []
        for it in items:
            rem = it.get("percent_remaining")
            rem_txt = f" · ~{rem}% left" if rem is not None else ""
            avail = _fmt_avail(it.get("availability"))
            colour = {
                "Available": "#1f6b3a",
                "Nearly sold out": "#8a4b12",
                "Sold out": "#8b2430",
            }.get(avail, "#5c564c")
            out.append(
                f'<tr><td style="{row_style}">{esc(str(it.get("date")))} '
                f'{esc(str(it.get("time")))}</td>'
                f'<td style="{row_style}color:{colour};text-align:right;">'
                f"{esc(avail)}{esc(rem_txt)}</td></tr>"
            )
        return "".join(out)

    banner = (
        f'<div style="background:#d7efe9;border:1px solid #0f6a5a;'
        f'border-radius:8px;padding:16px;">'
        f'<div style="font-size:18px;font-weight:700;color:#0f4a40;">'
        f"🎟️ Tickets available</div></div>"
    )
    cta_url = monitor.get("url") or ""
    button = (
        f'<a href="{esc(cta_url)}" '
        f'style="display:inline-block;background:#0f6a5a;color:#f7fffc;'
        f'text-decoration:none;font-weight:600;padding:12px 22px;border-radius:8px;'
        f'font-size:15px;">Book now on edfringe</a>'
        if cta_url
        else ""
    )

    opening_tbl = (
        f'<table style="width:100%;border-collapse:collapse;margin-top:6px;">'
        f"{perf_rows(openings)}</table>"
    )
    status_tbl = ""
    if statuses:
        status_tbl = (
            f'<p style="font-size:13px;color:#5c564c;margin:20px 0 4px;">'
            f"All monitored performances "
            f"({esc(str(monitor.get('start_date')))} → "
            f"{esc(str(monitor.get('end_date')))})</p>"
            f'<table style="width:100%;border-collapse:collapse;">'
            f"{perf_rows(statuses)}</table>"
        )

    return (
        f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'max-width:560px;margin:0 auto;color:#1c1915;">'
        f'<h1 style="font-size:20px;margin:0 0 4px;">{show_e}</h1>'
        f"{banner}"
        f'<div style="margin:18px 0;">{button}</div>'
        f'<p style="font-size:13px;color:#5c564c;margin:0 0 4px;">Newly available</p>'
        f"{opening_tbl}"
        f"{status_tbl}"
        f'<p style="font-size:12px;color:#9a948a;margin-top:24px;">— fringe-monitor</p>'
        f"</div>"
    )


def send_monitor_email(
    *,
    to_address: str,
    from_address: str,
    monitor: dict[str, Any],
    openings: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
) -> None:
    show = monitor.get("show_title") or monitor.get("slug") or "show"
    kwargs = {
        "show": show,
        "monitor": monitor,
        "openings": openings,
        "statuses": statuses,
    }
    subject = f"🎟️ Tickets available: {show}"
    ses_client().send_email(
        FromEmailAddress=from_address,
        Destination={"ToAddresses": [to_address]},
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": _monitor_email_text(**kwargs), "Charset": "UTF-8"},
                    "Html": {"Data": _monitor_email_html(**kwargs), "Charset": "UTF-8"},
                },
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
