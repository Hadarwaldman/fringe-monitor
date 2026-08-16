"""Rolling per-performance sell-through history for the wishlist.

The 15-minute wishlist refresh already prices every wishlist performance; this
module turns those readings into a retained time series so sell-through can be
analysed after the fact (how fast a show filled, when it tipped over, what the
curve looked like on the day).

Two design choices worth knowing:

* **Stored as percent SOLD**, matching `trend.py` and what the UI displays —
  a sell-through series that climbs toward 100 reads naturally, and it means
  `sold_out` collapses to a plain 100 instead of a special case.
* **Change-only sampling.** A sample is appended only when the value differs
  from the last one recorded for that performance. Availability moves slowly,
  so 96 readings a day collapse to a handful of rows; the series is still
  lossless if you read it as a step function (a value holds until the next
  sample). Without this the file would grow by ~1,500 rows every 15 minutes.

AWS-free and side-effect-free: safe to import from the local CLI venv and
exercised entirely offline in tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .trend import performance_percent_sold

EDINBURGH = ZoneInfo("Europe/London")

# Keep a festival's worth of detail, then let it age out. Both bounds exist so
# neither a long-running deployment nor one pathologically flappy performance
# can grow the object without limit.
RETENTION_DAYS = 30
MAX_SAMPLES_PER_PERF = 400


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=EDINBURGH) if dt.tzinfo is None else dt


def now_iso() -> str:
    return datetime.now(tz=EDINBURGH).isoformat(timespec="seconds")


def collect_samples(
    perfs_by_slug: dict[str, list[dict[str, Any]]],
    wishlist_slugs: set[str],
) -> dict[str, dict[str, Any]]:
    """Read the current sell-through of every wishlist performance.

    Expects the performance dicts to already carry refreshed availability (the
    refresh job updates them in place before calling this). Performances with
    no percentage are skipped rather than recorded as zero — an unpriced row is
    missing data, not an empty house.
    """
    samples: dict[str, dict[str, Any]] = {}
    for slug in sorted(wishlist_slugs):
        for perf in perfs_by_slug.get(slug) or []:
            box_id = perf.get("box_office_id")
            if not box_id:
                continue
            sold = performance_percent_sold(perf)
            if sold is None:
                continue
            samples[str(box_id)] = {
                "slug": slug,
                "date": perf.get("date"),
                "time": perf.get("time"),
                "percent_sold": round(float(sold), 1),
            }
    return samples


def merge_samples(
    history: dict[str, Any] | None,
    samples: dict[str, dict[str, Any]],
    *,
    at: str,
    retention_days: int = RETENTION_DAYS,
    max_samples: int = MAX_SAMPLES_PER_PERF,
) -> dict[str, Any]:
    """Fold one reading into the history, appending only what changed."""
    perfs: dict[str, Any] = dict((history or {}).get("performances") or {})
    appended = 0

    for box_id, sample in samples.items():
        entry = dict(perfs.get(box_id) or {})
        series: list[list[Any]] = list(entry.get("samples") or [])
        value = sample["percent_sold"]

        # Step function: only record transitions. Re-recording an unchanged
        # value would multiply the file size by ~96 a day for no new
        # information — the previous sample already asserts it.
        if series and series[-1][1] == value:
            entry["samples"] = series
        else:
            series.append([at, value])
            entry["samples"] = series[-max_samples:]
            appended += 1

        entry["slug"] = sample["slug"]
        entry["date"] = sample["date"]
        entry["time"] = sample["time"]
        entry["last_seen"] = at
        perfs[box_id] = entry

    perfs = _prune(perfs, at=at, retention_days=retention_days)
    return {
        "updated_at": at,
        "retention_days": retention_days,
        "performance_count": len(perfs),
        "sample_count": sum(len(p.get("samples") or []) for p in perfs.values()),
        "appended": appended,
        "performances": perfs,
    }


def _prune(
    perfs: dict[str, Any], *, at: str, retention_days: int
) -> dict[str, Any]:
    """Drop performances whose run finished longer than the retention window ago.

    Anchored on the performance date rather than the sample timestamps: once a
    show has played, its curve is complete and ages out on a predictable
    schedule. A row with an unparseable date is kept — silently discarding data
    we failed to parse is worse than carrying it.
    """
    now = _parse_iso(at) or datetime.now(tz=EDINBURGH)
    cutoff = (now - timedelta(days=retention_days)).date().isoformat()
    return {
        box_id: entry
        for box_id, entry in perfs.items()
        if not entry.get("date") or str(entry["date"]) >= cutoff
    }


def series_for(history: dict[str, Any] | None, box_office_id: str) -> list[list[Any]]:
    """The recorded [timestamp, percent_sold] steps for one performance."""
    perfs = (history or {}).get("performances") or {}
    entry = perfs.get(str(box_office_id)) or {}
    return list(entry.get("samples") or [])


def sold_through_rate(
    history: dict[str, Any] | None,
    box_office_id: str,
    *,
    hours: float = 24.0,
) -> float | None:
    """Percentage points sold per hour over the last `hours` of samples.

    Returns None when there is nothing to measure from: fewer than two samples,
    or two samples at the same instant.
    """
    series = series_for(history, box_office_id)
    if len(series) < 2:
        return None

    last_at = _parse_iso(series[-1][0])
    if last_at is None:
        return None
    window_start = last_at - timedelta(hours=hours)

    windowed = [s for s in series if (t := _parse_iso(s[0])) and t >= window_start]
    # Keep the step that carried us into the window so the first delta is real.
    if len(windowed) < len(series):
        windowed.insert(0, series[len(series) - len(windowed) - 1])
    if len(windowed) < 2:
        return None

    first_at, first_val = _parse_iso(windowed[0][0]), float(windowed[0][1])
    last_val = float(windowed[-1][1])
    if first_at is None:
        return None
    elapsed_h = (last_at - first_at).total_seconds() / 3600.0
    if elapsed_h <= 0:
        return None
    return round((last_val - first_val) / elapsed_h, 3)
