"""Rolling sell-through history and 7-day average daily sold %."""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

EDINBURGH = ZoneInfo("Europe/London")
HISTORY_KEEP_DAYS = 8  # 8 snapshots → up to 7 day-to-day deltas
TREND_WINDOW_DAYS = 7


def scan_date_from_payload(payload: dict[str, Any]) -> str:
    """Local calendar date of the scan (Edinburgh)."""
    raw = payload.get("fetched_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=EDINBURGH)
            return dt.astimezone(EDINBURGH).date().isoformat()
        except ValueError:
            pass
    return datetime.now(tz=EDINBURGH).date().isoformat()


def performance_percent_sold(perf: dict[str, Any]) -> float | None:
    if perf.get("availability") == "sold_out":
        return 100.0
    rem = perf.get("percent_remaining")
    if rem is None or rem == "":
        return None
    try:
        return max(0.0, min(100.0, 100.0 - float(rem)))
    except (TypeError, ValueError):
        return None


def show_avg_percent_sold(show: dict[str, Any]) -> float | None:
    values = [
        sold
        for perf in show.get("performances") or []
        if (sold := performance_percent_sold(perf)) is not None
    ]
    if not values:
        return None
    return round(mean(values), 2)


def build_day_snapshot(shows: list[dict[str, Any]], scan_date: str) -> dict[str, Any]:
    by_show: dict[str, dict[str, float]] = {}
    for show in shows:
        key = show.get("slug") or show.get("show_title") or ""
        if not key:
            continue
        avg = show_avg_percent_sold(show)
        if avg is None:
            continue
        by_show[str(key)] = {"avg_percent_sold": avg}
    return {"date": scan_date, "shows": by_show}


def merge_history(
    history: dict[str, Any] | None,
    snapshot: dict[str, Any],
    *,
    keep_days: int = HISTORY_KEEP_DAYS,
) -> dict[str, Any]:
    days = list((history or {}).get("days") or [])
    scan_date = snapshot["date"]
    days = [d for d in days if d.get("date") != scan_date]
    days.append(snapshot)
    days.sort(key=lambda d: d.get("date") or "")
    days = days[-keep_days:]
    return {"updated_at": datetime.now(tz=EDINBURGH).isoformat(), "days": days}


def compute_show_trend(
    show_key: str,
    history: dict[str, Any] | None,
    *,
    window_days: int = TREND_WINDOW_DAYS,
) -> dict[str, Any]:
    """Average daily % sold over the last `window_days` day-to-day changes."""
    days = list((history or {}).get("days") or [])
    series: list[dict[str, Any]] = []
    for day in days:
        entry = (day.get("shows") or {}).get(show_key)
        if not entry or entry.get("avg_percent_sold") is None:
            continue
        series.append(
            {
                "date": day.get("date"),
                "avg_percent_sold": float(entry["avg_percent_sold"]),
            }
        )

    series = series[-(window_days + 1) :]
    sold_pct_series = [round(p["avg_percent_sold"], 1) for p in series]
    deltas: list[float] = []
    for prev, cur in zip(series, series[1:]):
        deltas.append(cur["avg_percent_sold"] - prev["avg_percent_sold"])

    avg_daily: float | None = None
    if deltas:
        avg_daily = round(mean(deltas[-window_days:]), 2)

    return {
        "avg_daily_sold_pct": avg_daily,
        "sample_intervals": len(deltas[-window_days:]) if deltas else 0,
        "sold_pct_series": sold_pct_series,
        "series_dates": [p["date"] for p in series],
    }


def attach_trends(
    shows: list[dict[str, Any]],
    history: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    for show in shows:
        key = str(show.get("slug") or show.get("show_title") or "")
        trend = compute_show_trend(key, history)
        show["trend"] = trend
        show["avg_percent_sold"] = show_avg_percent_sold(show)
    return shows
