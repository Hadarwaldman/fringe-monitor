"""Shared Edinburgh Fringe availability scanner library."""

from .edfest_offers import fetch_and_attach_edfest_offers
from .models import PerformanceRow
from .scan import (
    DEFAULT_END,
    DEFAULT_START,
    build_latest_payload,
    classify_availability,
    collect_window_rows,
    enrich_with_prices,
    fetch_all_programme,
    summarize_shows,
)
from .trend import attach_trends, build_day_snapshot, merge_history

__all__ = [
    "DEFAULT_END",
    "DEFAULT_START",
    "PerformanceRow",
    "attach_trends",
    "build_day_snapshot",
    "build_latest_payload",
    "classify_availability",
    "collect_window_rows",
    "enrich_with_prices",
    "fetch_all_programme",
    "fetch_and_attach_edfest_offers",
    "merge_history",
    "summarize_shows",
]
