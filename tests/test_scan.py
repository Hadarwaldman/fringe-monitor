"""Scanner pipeline tests — window filtering, classification, payload shape.

All offline: prices come from tests/fixtures/prices.json via FakePricesApi.
"""

from __future__ import annotations

import asyncio
from datetime import date

from backend.fringe_lib.scan import (
    build_latest_payload,
    classify_availability,
    collect_show_details,
    collect_window_rows,
    enrich_with_prices,
    summarize_shows,
    watch_candidates_from_shows,
)

START = date(2026, 8, 12)
END = date(2026, 8, 20)


# ------------------------------------------------------------------ classify


def test_classify_sold_out_flag_wins():
    assert (
        classify_availability(
            sold_out=True,
            ticket_status="TICKETS_AVAILABLE",
            percent_remaining=90,
            availability_level="High",
            nearly_threshold=20,
        )
        == "sold_out"
    )


def test_classify_no_allocation_status_is_sold_out():
    assert (
        classify_availability(
            sold_out=False,
            ticket_status="NO_ALLOCATION_CONTACT_VENUE",
            percent_remaining=None,
            availability_level=None,
            nearly_threshold=20,
        )
        == "sold_out"
    )


def test_classify_zero_percent_is_sold_out():
    assert (
        classify_availability(
            sold_out=False,
            ticket_status="TICKETS_AVAILABLE",
            percent_remaining=0,
            availability_level="Low",
            nearly_threshold=20,
        )
        == "sold_out"
    )


def test_classify_threshold_boundary():
    common = dict(sold_out=False, ticket_status="TICKETS_AVAILABLE", availability_level=None)
    assert classify_availability(percent_remaining=20, nearly_threshold=20, **common) == "nearly_sold_out"
    assert classify_availability(percent_remaining=21, nearly_threshold=20, **common) == "available"


def test_classify_low_level_without_percent():
    assert (
        classify_availability(
            sold_out=False,
            ticket_status="TICKETS_AVAILABLE",
            percent_remaining=None,
            availability_level="Low",
            nearly_threshold=20,
        )
        == "nearly_sold_out"
    )


# ------------------------------------------------------------------ window rows


def test_collect_window_rows_filters(events):
    rows = collect_window_rows(events, START, END)
    by_id = {r.performance_id: r for r in rows}

    # Cancelled (1004), placeholder date (1005), outside window (1006) and
    # OFF_SALE (2003) are all excluded.
    assert set(by_id) == {1001, 1002, 1003, 2001, 2002, 3001}

    # 2026-08-11T23:30Z is 00:30 on Aug 12 in Edinburgh (BST) — inside the
    # window even though the UTC date is the 11th.
    assert by_id[2001].date_local == "2026-08-12"
    assert by_id[2001].time_local == "00:30"

    # UTC 18:30 renders as 19:30 local.
    assert by_id[1001].time_local == "19:30"

    alpha = by_id[1001]
    assert alpha.show_title == "Alpha Comedy Hour"
    assert alpha.venue == "Roaring Stag"
    assert alpha.url.endswith("/alpha-comedy-hour")
    assert alpha.price_types == ["PAID"]

    # priceType arrives as a bare string on some events.
    assert by_id[2001].price_types == ["PAID"]


def test_collect_window_rows_slug_filter(events):
    rows = collect_window_rows(events, START, END, slugs={"gamma-for-kids"})
    assert {r.performance_id for r in rows} == {3001}


# ------------------------------------------------------------------ enrichment


def classify_fixture_rows(events, fake_api, **kwargs):
    rows = collect_window_rows(events, START, END)
    return asyncio.run(enrich_with_prices(fake_api, rows, **kwargs)), rows


def test_enrich_classifies_from_prices(events, fake_api_factory):
    api = fake_api_factory()
    rows, _ = classify_fixture_rows(events, api)
    by_id = {r.performance_id: r for r in rows}

    assert by_id[1001].availability == "available"
    assert by_id[1001].percent_remaining == 80
    assert by_id[1002].availability == "nearly_sold_out"
    assert by_id[1003].availability == "sold_out"

    # NO_ALLOCATION rows never hit the API and read as fully sold.
    assert by_id[2002].availability == "sold_out"
    assert by_id[2002].percent_remaining == 0

    # No box office id → assumed available, no lookup.
    assert by_id[3001].availability == "available"

    # Only the three checkable rows with box office ids were queried.
    assert set(api.calls) == {"911:1001", "911:1002", "912:2001"}


def test_enrich_marks_failed_lookups_unchecked(events, fake_api_factory):
    api = fake_api_factory(fail_ids={"911:1001"})
    rows, _ = classify_fixture_rows(events, api)
    by_id = {r.performance_id: r for r in rows}
    assert by_id[1001].unchecked is True
    assert by_id[1001].availability == "available"  # fallback label
    assert by_id[1002].unchecked is False


def test_enrich_deadline_skips_remaining(events, fake_api_factory):
    api = fake_api_factory()
    rows, _ = classify_fixture_rows(events, api, deadline=0.0)
    checked = [r for r in rows if r.box_office_id and not r.sold_out_flag
               and r.ticket_status not in {"NO_ALLOCATION_CONTACT_VENUE"}]
    assert checked, "fixture should have checkable rows"
    assert all(r.unchecked for r in checked)
    assert api.calls == []


# ------------------------------------------------------------------ summary payload


def test_summarize_and_payload(events, fake_api_factory):
    rows, _ = classify_fixture_rows(events, fake_api_factory())
    payload = build_latest_payload(rows, start=START, end=END, nearly_threshold=20)

    assert payload["show_count"] == 3
    assert payload["performance_count"] == 6
    counts = payload["counts"]
    assert counts["sold_out"] == 2  # 1003 + 2002
    assert counts["nearly_sold_out"] == 1  # 1002
    assert counts["available"] == 3  # 1001, 2001, 3001
    assert counts["shows_with_sold_out"] == 2

    shows = {s["slug"]: s for s in payload["shows"]}
    alpha = shows["alpha-comedy-hour"]
    assert alpha["sold_out_dates"] == ["2026-08-16"]
    assert alpha["nearly_sold_out_dates"] == ["2026-08-15"]
    assert alpha["available_dates"] == ["2026-08-14"]

    perf = alpha["performances"][0]
    # Slim payload contract: exactly the fields the frontend consumes.
    assert set(perf) == {
        "performance_id",
        "date",
        "time",
        "availability",
        "percent_remaining",
        "box_office_id",
        "offers",
    }


def test_watch_candidates(events, fake_api_factory):
    rows, _ = classify_fixture_rows(events, fake_api_factory())
    shows = summarize_shows(rows)
    candidates = watch_candidates_from_shows(shows)
    ids = {c["performance_id"] for c in candidates}
    assert ids == {1002, 1003, 2002}
    for c in candidates:
        assert c["source"] == "auto"
        assert c["slug"]


# ------------------------------------------------------------------ details


def test_collect_show_details(events):
    details = collect_show_details(events, slugs={"alpha-comedy-hour"})
    assert set(details) == {"alpha-comedy-hour"}
    alpha = details["alpha-comedy-hour"]
    assert alpha["image_url"] == "https://img.example/alpha.jpg"
    assert alpha["venues"][0]["post_code"] == "EH1 1AA"
