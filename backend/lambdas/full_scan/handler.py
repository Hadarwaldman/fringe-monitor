from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date
from typing import Any

from fringe_lib.aws_util import (
    env,
    get_config,
    get_json_s3,
    put_json_s3,
    replace_auto_watchlist,
)
from fringe_lib.cart import load_proxy_into_env
from fringe_lib.client import FringeClient, make_async_client
from fringe_lib.edfest_offers import fetch_and_attach_edfest_offers
from fringe_lib.models import PerformanceRow
from fringe_lib.scan import (
    build_latest_payload,
    collect_window_rows,
    enrich_with_prices,
    fetch_all_programme,
    watch_candidates_from_shows,
)
from fringe_lib.trend import (
    attach_trends,
    build_day_snapshot,
    merge_history,
    scan_date_from_payload,
)


def _write_performances_csv(rows: list[PerformanceRow]) -> str:
    fieldnames = [
        "availability",
        "show_title",
        "genre",
        "venue",
        "date",
        "time",
        "performance_title",
        "ticket_status",
        "sold_out",
        "percent_remaining",
        "availability_level",
        "offers",
        "url",
        "performance_id",
        "box_office_id",
        "datetime_utc",
    ]
    urgency_rank = {"sold_out": 0, "nearly_sold_out": 1, "available": 2}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in sorted(
        rows,
        key=lambda r: (
            urgency_rank.get(r.availability, 9),
            r.percent_remaining if r.percent_remaining is not None else 999,
            r.date_local,
            r.time_local,
            r.show_title,
        ),
    ):
        writer.writerow(row.to_csv_dict())
    return buf.getvalue()


async def run_full_scan() -> dict[str, Any]:
    config = get_config()
    start = date.fromisoformat(config["start_date"])
    end = date.fromisoformat(config["end_date"])
    nearly = int(config["nearly_threshold"])
    data_bucket = env("DATA_BUCKET")

    print(
        f"Full scan {start} → {end}; nearly≤{nearly}%",
        flush=True,
    )

    load_proxy_into_env()
    async with make_async_client() as client:
        api = FringeClient(client)
        await api.authenticate()
        events = await fetch_all_programme(api, page_size=500)
        candidates = collect_window_rows(events, start, end)
        print(f"Classifying {len(candidates)} performances…", flush=True)
        classified = await enrich_with_prices(
            api,
            candidates,
            concurrency=25,
            nearly_threshold=nearly,
        )
        offers_meta = await fetch_and_attach_edfest_offers(
            client,
            classified,
            start=start,
            end=end,
        )

    payload = build_latest_payload(
        classified,
        start=start,
        end=end,
        nearly_threshold=nearly,
        offers_meta=offers_meta,
    )

    # Rolling history for 7-day average daily sell-through.
    scan_date = scan_date_from_payload(payload)
    previous_history = get_json_s3(data_bucket, "data/history.json") or {"days": []}
    snapshot = build_day_snapshot(payload["shows"], scan_date)
    history = merge_history(previous_history, snapshot)
    attach_trends(payload["shows"], history)
    put_json_s3(data_bucket, "data/history.json", history)

    put_json_s3(data_bucket, "data/latest.json", payload)
    put_json_s3(
        data_bucket,
        "data/config.json",
        {
            "start_date": config["start_date"],
            "end_date": config["end_date"],
            "nearly_threshold": nearly,
            "notify_email": config["notify_email"],
            "auto_watch_sold_out": config["auto_watch_sold_out"],
            "fetched_at": payload["fetched_at"],
        },
    )

    # Also store a compact CSV for download.
    import boto3

    boto3.client("s3").put_object(
        Bucket=data_bucket,
        Key="data/fringe_availability.csv",
        Body=_write_performances_csv(classified).encode("utf-8"),
        ContentType="text/csv",
        CacheControl="no-cache",
    )

    watch_count = 0
    if config.get("auto_watch_sold_out", True):
        watch_items = watch_candidates_from_shows(payload["shows"])
        watch_count = replace_auto_watchlist(watch_items)

    result = {
        "ok": True,
        "fetched_at": payload["fetched_at"],
        "show_count": payload["show_count"],
        "performance_count": payload["performance_count"],
        "counts": payload["counts"],
        "watchlist_auto_count": watch_count,
    }
    print(json.dumps(result), flush=True)
    return result


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    return asyncio.run(run_full_scan())
