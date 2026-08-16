"""Rolling wishlist sell-through history — sampling, retention, statistics."""

from __future__ import annotations

from backend.fringe_lib.wishlist_history import (
    collect_samples,
    merge_samples,
    series_for,
    sold_through_rate,
)


def perf(box_id, *, date="2026-08-18", time="19:30", remaining=40, availability="available"):
    return {
        "box_office_id": box_id,
        "date": date,
        "time": time,
        "percent_remaining": remaining,
        "availability": availability,
    }


def test_collect_samples_records_sold_not_remaining():
    perfs = {"alpha": [perf("911:1", remaining=15)]}
    samples = collect_samples(perfs, {"alpha"})
    assert samples["911:1"]["percent_sold"] == 85.0
    assert samples["911:1"]["slug"] == "alpha"
    assert samples["911:1"]["date"] == "2026-08-18"


def test_collect_samples_treats_sold_out_as_100_and_skips_unpriced():
    perfs = {
        "alpha": [
            perf("911:1", remaining=None, availability="sold_out"),
            # Unpriced: missing data, not an empty house — must not become 0.
            perf("911:2", remaining=None, availability="available"),
        ]
    }
    samples = collect_samples(perfs, {"alpha"})
    assert samples["911:1"]["percent_sold"] == 100.0
    assert "911:2" not in samples


def test_collect_samples_ignores_shows_outside_the_wishlist():
    perfs = {"alpha": [perf("911:1")], "beta": [perf("912:1")]}
    assert set(collect_samples(perfs, {"alpha"})) == {"911:1"}


def test_unchanged_readings_do_not_grow_the_series():
    """96 identical readings a day must not become 96 rows."""
    history = None
    for i in range(5):
        history = merge_samples(
            history,
            collect_samples({"alpha": [perf("911:1", remaining=40)]}, {"alpha"}),
            at=f"2026-08-16T1{i}:00:00Z",
        )
    assert series_for(history, "911:1") == [["2026-08-16T10:00:00Z", 60.0]]
    assert history["sample_count"] == 1
    assert history["appended"] == 0  # the last merge added nothing


def test_changed_readings_append_a_step():
    history = merge_samples(
        None,
        collect_samples({"alpha": [perf("911:1", remaining=40)]}, {"alpha"}),
        at="2026-08-16T10:00:00Z",
    )
    history = merge_samples(
        history,
        collect_samples({"alpha": [perf("911:1", remaining=25)]}, {"alpha"}),
        at="2026-08-16T10:15:00Z",
    )
    assert series_for(history, "911:1") == [
        ["2026-08-16T10:00:00Z", 60.0],
        ["2026-08-16T10:15:00Z", 75.0],
    ]
    assert history["appended"] == 1


def test_series_is_capped_per_performance():
    history = None
    for i in range(12):
        history = merge_samples(
            history,
            collect_samples({"alpha": [perf("911:1", remaining=i)]}, {"alpha"}),
            at=f"2026-08-16T10:{i:02d}:00Z",
            max_samples=5,
        )
    series = series_for(history, "911:1")
    assert len(series) == 5
    assert series[-1][1] == 89.0  # newest kept, oldest dropped


def test_finished_performances_age_out_but_recent_ones_stay():
    history = merge_samples(
        None,
        collect_samples(
            {
                "alpha": [perf("911:old", date="2026-06-01")],
                "beta": [perf("912:new", date="2026-08-18")],
            },
            {"alpha", "beta"},
        ),
        at="2026-08-16T10:00:00Z",
        retention_days=30,
    )
    assert "912:new" in history["performances"]
    assert "911:old" not in history["performances"]


def test_rows_with_unparseable_dates_are_kept():
    """Better to carry data we failed to parse than silently bin it."""
    history = merge_samples(
        None,
        collect_samples({"alpha": [perf("911:1", date=None)]}, {"alpha"}),
        at="2026-08-16T10:00:00Z",
    )
    assert "911:1" in history["performances"]


def test_history_round_trips_through_json():
    import json

    history = merge_samples(
        None,
        collect_samples({"alpha": [perf("911:1", remaining=40)]}, {"alpha"}),
        at="2026-08-16T10:00:00Z",
    )
    revived = json.loads(json.dumps(history))
    history = merge_samples(
        revived,
        collect_samples({"alpha": [perf("911:1", remaining=10)]}, {"alpha"}),
        at="2026-08-16T11:00:00Z",
    )
    assert series_for(history, "911:1")[-1] == ["2026-08-16T11:00:00Z", 90.0]


def test_sold_through_rate_is_points_per_hour():
    history = merge_samples(
        None,
        collect_samples({"alpha": [perf("911:1", remaining=100)]}, {"alpha"}),
        at="2026-08-16T10:00:00Z",
    )
    history = merge_samples(
        history,
        collect_samples({"alpha": [perf("911:1", remaining=80)]}, {"alpha"}),
        at="2026-08-16T12:00:00Z",
    )
    # 0% sold → 20% sold over 2h.
    assert sold_through_rate(history, "911:1") == 10.0


def test_sold_through_rate_needs_two_samples():
    history = merge_samples(
        None,
        collect_samples({"alpha": [perf("911:1")]}, {"alpha"}),
        at="2026-08-16T10:00:00Z",
    )
    assert sold_through_rate(history, "911:1") is None
    assert sold_through_rate(history, "nope") is None


def test_apply_availability_returns_the_readings_it_measured():
    """The per-performance values used to be computed and then discarded.

    Lives in fringe_lib, not the handler: handlers import boto3, which the
    offline suite deliberately does not have.
    """
    from backend.fringe_lib.availability import apply_availability

    perfs = [
        perf("a", date="2026-08-18", time="19:30"),
        perf("b", date="2026-08-18", time="21:00"),
        perf("c", date="2026-08-19", time="19:30"),
    ]
    avail = {
        "a": {"availability": "sold_out", "percent_remaining": 0},
        "b": {"availability": "nearly_sold_out", "percent_remaining": 12},
    }
    out = apply_availability(perfs, avail)

    assert out["sold_out_dates"] == ["2026-08-18"]
    assert out["available_dates"] == ["2026-08-19"]
    # Only performances we actually re-checked are published as fresh; "c" was
    # not in the lookup, so it keeps its scan value and is not claimed as new.
    assert [p["box_office_id"] for p in out["performances"]] == ["a", "b"]
    assert out["performances"][1]["percent_remaining"] == 12
    assert perfs[2]["percent_remaining"] == 40
