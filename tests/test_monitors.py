"""Monitor evaluation logic — alert-once transitions, unchecked handling."""

from __future__ import annotations

from backend.fringe_lib.models import PerformanceRow
from backend.fringe_lib.monitors import evaluate_monitor, monitor_rows, new_monitor


def row(perf_id, date, availability, *, time="19:30", unchecked=False, slug="alpha"):
    return PerformanceRow(
        show_title="Alpha",
        slug=slug,
        genre="",
        venue="",
        performance_id=perf_id,
        performance_title="",
        date_local=date,
        time_local=time,
        datetime_utc="",
        ticket_status="",
        sold_out_flag=False,
        box_office_id=f"9:{perf_id}",
        availability=availability,
        unchecked=unchecked,
    )


def test_new_monitor_seeds_only_in_range():
    monitor = new_monitor(
        slug="alpha",
        show_title="Alpha",
        start_date="2026-08-14",
        end_date="2026-08-16",
        performances=[
            {"performance_id": 1, "box_office_id": "9:1", "date": "2026-08-13", "time": "19:30"},
            {"performance_id": 2, "box_office_id": "9:2", "date": "2026-08-15", "time": "19:30"},
            {"performance_id": None, "date": "2026-08-15", "time": "19:30"},
        ],
    )
    assert [p["performance_id"] for p in monitor["performances"]] == [2]
    assert monitor["active"] is True
    assert monitor["alerted"] == {}


def test_monitor_rows_filters_slug_and_range():
    monitor = {"slug": "alpha", "start_date": "2026-08-14", "end_date": "2026-08-15"}
    rows = [
        row(1, "2026-08-13", "available"),
        row(2, "2026-08-14", "available"),
        row(3, "2026-08-15", "available", slug="other"),
    ]
    assert [r.performance_id for r in monitor_rows(monitor, rows)] == [2]


def test_evaluate_alerts_once_per_transition():
    monitor = {"alerted": {}}
    rows = [row(1, "2026-08-14", "sold_out"), row(2, "2026-08-15", "available")]

    first = evaluate_monitor(monitor, rows)
    assert [r.performance_id for r in first["openings"]] == [2]
    assert first["alerted"] == {"1": "sold_out", "2": "buyable"}

    # Same state next check — no repeat alert.
    monitor["alerted"] = first["alerted"]
    second = evaluate_monitor(monitor, rows)
    assert second["openings"] == []

    # Perf 1 reopens → alerts; perf 2 stays quiet.
    rows2 = [row(1, "2026-08-14", "nearly_sold_out"), row(2, "2026-08-15", "available")]
    third = evaluate_monitor(monitor, rows2)
    assert [r.performance_id for r in third["openings"]] == [1]

    # Sold out again, then reopens → alerts again (flag reset).
    monitor["alerted"] = third["alerted"]
    fourth = evaluate_monitor(monitor, [row(1, "2026-08-14", "sold_out")])
    assert fourth["openings"] == []
    monitor["alerted"] = fourth["alerted"]
    fifth = evaluate_monitor(monitor, [row(1, "2026-08-14", "available")])
    assert [r.performance_id for r in fifth["openings"]] == [1]


def test_evaluate_ignores_unchecked_rows():
    """A failed price lookup must never fabricate a reopen email."""
    monitor = {"alerted": {"1": "sold_out"}}
    outcome = evaluate_monitor(
        monitor, [row(1, "2026-08-14", "available", unchecked=True)]
    )
    assert outcome["openings"] == []
    assert outcome["statuses"] == []
    # Alert memory untouched, so a real reopen later still fires.
    assert outcome["alerted"] == {"1": "sold_out"}
