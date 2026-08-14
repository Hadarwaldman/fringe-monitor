"""Show monitors: watch one show across a date range, email when any
performance in the range becomes buyable, and optionally hold tickets in the
user's edfringe basket (see cart.py).

Monitor items live in DynamoDB as pk=MONITOR / sk=<monitor_id>. This module
keeps the evaluation logic AWS-free (testable locally); orchestration imports
aws_util/cart lazily so the local CLI venv (no boto3) can import it.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from .models import PerformanceRow

# "Buyable" = anything you can still get a ticket for.
BUYABLE = {"available", "nearly_sold_out"}


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_monitor(
    *,
    slug: str,
    show_title: str,
    start_date: str,
    end_date: str,
    quantity: int = 1,
    hold_tickets: bool = False,
    url: str = "",
) -> dict[str, Any]:
    return {
        "monitor_id": uuid.uuid4().hex[:12],
        "slug": slug,
        "show_title": show_title,
        "url": url,
        "start_date": start_date,
        "end_date": end_date,
        "quantity": max(1, int(quantity)),
        "hold_tickets": bool(hold_tickets),
        "active": True,
        "created_at": now_iso(),
        "alerted": {},
        "holds_json": "{}",
        "last_result": [],
    }


def monitor_rows(monitor: dict[str, Any], rows: list[PerformanceRow]) -> list[PerformanceRow]:
    """Rows for this monitor's show inside its date range, chronological."""
    picked = [
        r
        for r in rows
        if r.slug == monitor.get("slug")
        and monitor.get("start_date", "") <= r.date_local <= monitor.get("end_date", "")
    ]
    return sorted(picked, key=lambda r: (r.date_local, r.time_local))


def evaluate_monitor(
    monitor: dict[str, Any], rows: list[PerformanceRow]
) -> dict[str, Any]:
    """Compare classified rows against the monitor's alert memory.

    Returns statuses (all rows), openings (rows newly buyable since last
    check — these trigger the email), and the updated alerted map. A
    performance alerts once per transition into buyable; the flag resets
    when it goes back to sold_out.
    """
    alerted = dict(monitor.get("alerted") or {})
    statuses: list[dict[str, Any]] = []
    openings: list[PerformanceRow] = []

    for row in rows:
        perf_key = str(row.performance_id)
        buyable = row.availability in BUYABLE
        statuses.append(
            {
                "performance_id": row.performance_id,
                "date": row.date_local,
                "time": row.time_local,
                "availability": row.availability,
                "percent_remaining": row.percent_remaining,
                "box_office_id": row.box_office_id or "",
            }
        )
        if buyable and alerted.get(perf_key) != "buyable":
            openings.append(row)
        alerted[perf_key] = "buyable" if buyable else "sold_out"

    return {"statuses": statuses, "openings": openings, "alerted": alerted}


async def run_monitor_checks(
    api,
    http,
    events: list[dict[str, Any]],
    config: dict[str, Any],
    monitors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check every active monitor against a fresh programme snapshot.

    `events` is the raw programme (from fetch_all_programme) so the 15-minute
    lambda only fetches it once for watchlist + monitors combined.
    """
    from .aws_util import env, patch_monitor, send_monitor_email
    from .cart import get_fringe_credentials, hold_tickets
    from .scan import collect_window_rows, enrich_with_prices

    nearly = int(config.get("nearly_threshold") or 20)
    notify_email = config["notify_email"]
    from_email = env("FROM_EMAIL", notify_email)
    today = date.today().isoformat()

    checked = 0
    emailed = 0
    holds_attempted = 0

    # One price-enrich pass across all monitors (dedup by performance).
    slug_set = {m["slug"] for m in monitors if m.get("slug")}
    all_rows: list[PerformanceRow] = []
    if slug_set:
        starts = [m["start_date"] for m in monitors]
        ends = [m["end_date"] for m in monitors]
        rows = collect_window_rows(
            events,
            date.fromisoformat(min(starts)),
            date.fromisoformat(max(ends)),
            slugs=slug_set,
        )
        print(f"Monitors: classifying {len(rows)} performances…", flush=True)
        all_rows = await enrich_with_prices(
            api, rows, concurrency=20, nearly_threshold=nearly
        )

    credentials = None
    creds_checked = False

    for monitor in monitors:
        if monitor.get("end_date", "") < today:
            patch_monitor(monitor["monitor_id"], {"active": False})
            print(
                f"Monitor {monitor['monitor_id']} ({monitor.get('show_title')}) "
                "expired; deactivated",
                flush=True,
            )
            continue

        rows = monitor_rows(monitor, all_rows)
        outcome = evaluate_monitor(monitor, rows)
        checked += 1

        holds = json.loads(monitor.get("holds_json") or "{}")
        hold_result: dict[str, Any] | None = None
        openings = outcome["openings"]

        if openings and monitor.get("hold_tickets"):
            if not creds_checked:
                credentials = get_fringe_credentials()
                creds_checked = True
            # Hold only the earliest newly-opened performance — enough to
            # secure the trip without hoarding inventory across dates.
            target = next((r for r in openings if r.box_office_id), None)
            if credentials is None:
                hold_result = {
                    "success": False,
                    "error": "edfringe credentials not configured (SSM parameter missing)",
                }
            elif target is None:
                hold_result = {
                    "success": False,
                    "error": "no box office id for opened performance",
                }
            else:
                holds_attempted += 1
                hold_result = await hold_tickets(
                    api,
                    http,
                    box_office_id=target.box_office_id,
                    quantity=int(monitor.get("quantity") or 1),
                    credentials=credentials,
                )
                hold_result["performance_id"] = target.performance_id
                hold_result["date"] = target.date_local
                hold_result["time"] = target.time_local
                holds[str(target.performance_id)] = {
                    **hold_result,
                    "at": now_iso(),
                }

        patch: dict[str, Any] = {
            "last_checked_at": now_iso(),
            "alerted": outcome["alerted"],
            "last_result": outcome["statuses"],
            "holds_json": json.dumps(holds),
        }

        if openings:
            send_monitor_email(
                to_address=notify_email,
                from_address=from_email,
                monitor=monitor,
                openings=[
                    {
                        "date": r.date_local,
                        "time": r.time_local,
                        "availability": r.availability,
                        "percent_remaining": r.percent_remaining,
                    }
                    for r in openings
                ],
                statuses=outcome["statuses"],
                hold=hold_result,
            )
            emailed += 1
            patch["last_alert_at"] = now_iso()

        patch_monitor(monitor["monitor_id"], patch)
        summary = ", ".join(
            f"{s['date']} {s['time']}={s['availability']}" for s in outcome["statuses"]
        )
        print(
            f"Monitor {monitor['monitor_id']} ({monitor.get('show_title')}): "
            f"{len(rows)} perfs, {len(openings)} new opening(s). {summary}",
            flush=True,
        )

    return {
        "monitors_checked": checked,
        "monitor_emails": emailed,
        "holds_attempted": holds_attempted,
    }
