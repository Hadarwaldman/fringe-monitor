"""Trend/history math tests."""

from __future__ import annotations

from backend.fringe_lib.trend import (
    attach_trends,
    build_day_snapshot,
    compute_show_trend,
    merge_history,
    performance_percent_sold,
    scan_date_from_payload,
    show_avg_percent_sold,
)


def hist(*day_values, slug="alpha"):
    """History with one show whose avg_percent_sold walks through day_values."""
    return {
        "days": [
            {"date": f"2026-08-{10 + i:02d}", "shows": {slug: {"avg_percent_sold": v}}}
            for i, v in enumerate(day_values)
        ]
    }


def test_percent_sold_basics():
    assert performance_percent_sold({"availability": "sold_out"}) == 100.0
    assert performance_percent_sold({"availability": "available", "percent_remaining": 30}) == 70.0
    assert performance_percent_sold({"availability": "available", "percent_remaining": None}) is None
    # Clamped to [0, 100] even on nonsense input.
    assert performance_percent_sold({"availability": "available", "percent_remaining": 150}) == 0.0
    assert performance_percent_sold({"availability": "available", "percent_remaining": -5}) == 100.0


def test_show_avg_ignores_unknown():
    show = {
        "performances": [
            {"availability": "sold_out"},
            {"availability": "available", "percent_remaining": 50},
            {"availability": "available", "percent_remaining": None},
        ]
    }
    assert show_avg_percent_sold(show) == 75.0


def test_scan_date_uses_edinburgh_day():
    # 23:30 UTC on the 14th is already the 15th in Edinburgh (BST).
    assert scan_date_from_payload({"fetched_at": "2026-08-14T23:30:00Z"}) == "2026-08-15"


def test_merge_history_replaces_same_day_and_caps():
    history = hist(10, 20, 30)
    snap = {"date": "2026-08-12", "shows": {"alpha": {"avg_percent_sold": 99}}}
    merged = merge_history(history, snap)
    by_date = {d["date"]: d for d in merged["days"]}
    assert by_date["2026-08-12"]["shows"]["alpha"]["avg_percent_sold"] == 99

    long_history = hist(*range(1, 13))  # 12 days
    merged = merge_history(long_history, {"date": "2026-08-30", "shows": {}})
    assert len(merged["days"]) == 8  # HISTORY_KEEP_DAYS
    assert merged["days"][-1]["date"] == "2026-08-30"


def test_compute_trend_deltas():
    trend = compute_show_trend("alpha", hist(10, 15, 25))
    assert trend["avg_daily_sold_pct"] == 7.5  # (5 + 10) / 2
    assert trend["sample_intervals"] == 2
    assert trend["sold_pct_series"] == [10, 15, 25]


def test_compute_trend_needs_two_points():
    trend = compute_show_trend("alpha", hist(40))
    assert trend["avg_daily_sold_pct"] is None
    assert trend["sample_intervals"] == 0


def test_attach_trends_sets_fields():
    shows = [
        {
            "slug": "alpha",
            "performances": [{"availability": "available", "percent_remaining": 40}],
        }
    ]
    attach_trends(shows, hist(10, 20))
    assert shows[0]["trend"]["avg_daily_sold_pct"] == 10.0
    assert shows[0]["avg_percent_sold"] == 60.0
