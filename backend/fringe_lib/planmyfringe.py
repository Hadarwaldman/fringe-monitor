"""PlanMyFringe (www.planmyfringe.co.uk) integration.

Logs into the user's PlanMyFringe account and pulls their schedule
(CalendarList) and wishlist, parsing them into structured entries that the
sync endpoint matches against the latest scan data:

- schedule entries marked *confirmed* are shows already booked → they are
  reported but NOT added to the watchlist;
- unconfirmed schedule entries are matched to performances and imported as
  watch items (source "planmyfringe");
- wishlist entries carry the user's score/rating so the UI can surface it
  next to matching shows.

The site is classic ASP.NET Web Forms: login is a plain POST to /LogOn that
round-trips the __VIEWSTATE hidden fields and sets a session cookie; the
logged-in pages are server-rendered HTML tables. Parsing is header-driven
and deliberately tolerant because GridView markup varies.

Credentials are JSON {"user_id": "...", "password": "..."} and live ONLY in:
- Lambda: the SSM SecureString named by PLANMYFRINGE_CREDS_PARAM;
- locally: the file named by PLANMYFRINGE_CREDS_FILE (default
  output/planmyfringe-creds.json, gitignored) or the PLANMYFRINGE_CREDS
  env var (raw JSON).
Never in code, Terraform, DynamoDB, or git.

NOTE: the logged-in markup has not been observed yet — validate the parsers
with `python sync_planmyfringe.py` once credentials are configured.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

BASE_URL = "https://www.planmyfringe.co.uk"
DEFAULT_YEAR = 2026

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# One page carries both: the day-by-day schedule table AND the full rated
# show list ("wishlist") table below it.
SCHEDULE_PATHS = ["/CalendarList"]

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Observed live markup (Aug 2026): the schedule table is headed
# Description|From|To|Duration|Venue|Price (£)|2For1|Rating with single-cell
# day-separator rows ("Fri 14/08 …"); the wishlist table is headed
# Title|Type|Artist|Genre|Venue|Rating|First|Last. Extra synonyms kept for
# resilience against site tweaks.
_TITLE_HEADERS = {"description", "name", "show", "show name", "title", "event"}
_TIME_HEADERS = {"from", "time", "start", "start time"}
_VENUE_HEADERS = {"venue", "location", "where"}
_SCORE_HEADERS = {"rating", "score", "my rating", "my score", "stars"}
_WISHLIST_MARKER_HEADERS = {"artist", "genre", "type"}


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def get_planmyfringe_credentials() -> dict[str, str] | None:
    """Load {"user_id", "password"} from (in order): PLANMYFRINGE_CREDS env
    JSON, the local creds file, or the SSM SecureString (Lambda). None when
    not configured anywhere."""
    raw = os.environ.get("PLANMYFRINGE_CREDS")
    if raw:
        return _parse_creds(raw)

    path = os.environ.get(
        "PLANMYFRINGE_CREDS_FILE", "output/planmyfringe-creds.json"
    )
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return _parse_creds(fh.read())

    param_name = os.environ.get("PLANMYFRINGE_CREDS_PARAM")
    if not param_name:
        return None
    import boto3
    from botocore.exceptions import ClientError

    try:
        resp = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise
    return _parse_creds(resp["Parameter"]["Value"])


def _parse_creds(raw: str) -> dict[str, str] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    user_id = data.get("user_id") or data.get("email") or data.get("username")
    password = data.get("password")
    if not user_id or not password:
        return None
    return {"user_id": str(user_id), "password": str(password)}


def mask_user_id(user_id: str) -> str:
    """'Adi.ajz@gmail.com' → 'Ad…@gmail.com' — safe to echo to the UI."""
    text = str(user_id or "")
    if "@" in text:
        local, _, domain = text.partition("@")
        return f"{local[:2]}…@{domain}"
    return f"{text[:2]}…" if text else ""


def store_credentials(credentials: dict[str, str]) -> None:
    """Write credentials to the SSM SecureString named by
    PLANMYFRINGE_CREDS_PARAM (Lambda-only; the UI settings page uses this)."""
    param_name = os.environ.get("PLANMYFRINGE_CREDS_PARAM")
    if not param_name:
        raise RuntimeError("PLANMYFRINGE_CREDS_PARAM not configured")
    import boto3

    boto3.client("ssm").put_parameter(
        Name=param_name,
        Type="SecureString",
        Overwrite=True,
        Value=json.dumps(
            {"user_id": credentials["user_id"], "password": credentials["password"]}
        ),
    )


def verify_credentials(credentials: dict[str, str]) -> None:
    """Attempt a real login; raises RuntimeError on rejection. Falls back to
    the residential proxy if the direct egress IP is blocked."""
    proxy = os.environ.get("FRINGE_PROXY_URL") or None
    client = _make_client()
    try:
        try:
            login(client, credentials)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403 and proxy:
                client.close()
                client = _make_client(proxy)
                login(client, credentials)
            else:
                raise
    finally:
        client.close()


# --------------------------------------------------------------------------
# HTML parsing (stdlib only — Lambda deps stay httpx-only)
# --------------------------------------------------------------------------

class _HtmlDoc(HTMLParser):
    """Collects hidden form inputs and every table's headers/rows.

    Each cell is {"text": str, "marker": str} where marker accumulates
    lowercase class/alt/title/checked hints so callers can detect confirmed
    ticks rendered as icons or checkboxes rather than text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_inputs: dict[str, str] = {}
        self.tables: list[dict[str, Any]] = []
        self._table_stack: list[dict[str, Any]] = []
        self._row: dict[str, Any] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "input":
            if a.get("type", "").lower() == "hidden" and a.get("name"):
                self.hidden_inputs[a["name"]] = a.get("value", "")
            if self._cell is not None:
                marker = a.get("type", "input").lower()
                if "checked" in a:
                    marker += " checked"
                self._cell["marker"] += f" {marker}"
            return
        if tag == "table":
            self._table_stack.append(
                {"id": a.get("id", ""), "class": a.get("class", ""), "rows": []}
            )
            return
        if not self._table_stack:
            return
        if tag == "tr":
            self._row = {"cells": [], "marker": a.get("class", "").lower()}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {
                "text": "",
                "is_header": tag == "th",
                "marker": a.get("class", "").lower(),
            }
        elif self._cell is not None and tag == "img":
            self._cell["marker"] += " " + " ".join(
                a.get(k, "").lower() for k in ("alt", "title", "src", "class")
            )
        elif self._cell is not None and tag == "a":
            self._cell["marker"] += " " + a.get("href", "").lower()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._cell["text"] = re.sub(r"\s+", " ", self._cell["text"]).strip()
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table_stack:
            if self._row["cells"]:
                self._table_stack[-1]["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table_stack:
            table = self._table_stack.pop()
            if table["rows"]:
                self.tables.append(table)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data


def parse_html(html: str) -> _HtmlDoc:
    doc = _HtmlDoc()
    doc.feed(html)
    return doc


def _table_records(table: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    """Turn a parsed table into records keyed by lowercase header text."""
    rows = table["rows"]
    header_row = next(
        (r for r in rows if all(c["is_header"] for c in r["cells"])), None
    )
    if header_row is None:
        header_row = rows[0]
    headers = [c["text"].strip().lower() for c in header_row["cells"]]
    records = []
    for row in rows:
        if row is header_row:
            continue
        record: dict[str, Any] = {"__row_marker": row["marker"]}
        for i, cell in enumerate(row["cells"]):
            key = headers[i] if i < len(headers) and headers[i] else f"col{i}"
            record[key] = cell
        records.append(record)
    return records


def _find(record: dict[str, Any], names: set[str]) -> dict[str, Any] | None:
    for key, value in record.items():
        if key.startswith("__"):
            continue
        base = key.strip().lower()
        if base in names or any(base.startswith(n) for n in names):
            return value
    return None


def _cell_text(cell: dict[str, Any] | None) -> str:
    return (cell or {}).get("text", "").strip()


def _is_confirmed(record: dict[str, Any]) -> bool:
    """Booked/confirmed rows carry a BookShow link with remove=Y — the row's
    action is 'remove booking', so a booking exists."""
    markers = " ".join(
        c.get("marker", "")
        for c in record.values()
        if isinstance(c, dict)
    )
    return "remove=y" in markers.replace("&amp;", "&")


def parse_pmf_date(raw: str, *, year: int = DEFAULT_YEAR) -> str | None:
    """Parse the site's date formats to ISO. Handles '2026-08-14',
    '14/08/2026', 'Fri 14/08', 'Fri 14 Aug', '14 Aug 2026'."""
    text = str(raw or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if m:
        y = int(m.group(3)) if m.group(3) else year
        if y < 100:
            y += 2000
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,})(?:\s+(\d{4}))?", text)
    if m:
        month = _MONTHS.get(m.group(2)[:3].lower())
        if month:
            y = int(m.group(3)) if m.group(3) else year
            return f"{y:04d}-{month:02d}-{int(m.group(1)):02d}"
    return None


def _parse_time(raw: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", str(raw or ""))
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _parse_score(raw: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
    if not m:
        return None
    return float(m.group(0))


def _headers_of(table: dict[str, Any]) -> list[str]:
    row = next(
        (r for r in table["rows"] if all(c["is_header"] for c in r["cells"])), None
    )
    if row is None:
        return []
    return [c["text"].strip().lower() for c in row["cells"]]


def _is_schedule_table(headers: list[str]) -> bool:
    return "description" in headers and ("from" in headers or "time" in headers)


def _is_wishlist_table(headers: list[str]) -> bool:
    return (
        "title" in headers
        and any(h in headers for h in _WISHLIST_MARKER_HEADERS)
        and any(h in headers for h in _SCORE_HEADERS)
    )


def parse_schedule(html: str, *, year: int = DEFAULT_YEAR) -> list[dict[str, Any]]:
    """Extract schedule entries {title, date, time, venue, confirmed, score}
    from the day-by-day calendar table. The date comes from single-cell
    separator rows ("Fri 14/08 …"); show rows sit under them."""
    entries: list[dict[str, Any]] = []
    for table in parse_html(html).tables:
        headers = _headers_of(table)
        if not _is_schedule_table(headers):
            continue

        def col(names: set[str]) -> int | None:
            return next((i for i, h in enumerate(headers) if h in names), None)

        title_i, time_i = col(_TITLE_HEADERS), col(_TIME_HEADERS)
        venue_i, score_i = col(_VENUE_HEADERS), col(_SCORE_HEADERS)
        current_date: str | None = None
        for row in table["rows"]:
            cells = row["cells"]
            if all(c["is_header"] for c in cells):
                continue
            populated = [c for c in cells if c["text"]]
            if len(populated) == 1:
                day = parse_pmf_date(populated[0]["text"], year=year)
                if day:
                    current_date = day
                continue
            def cell_at(i: int | None, cells: list = cells) -> str:
                return _cell_text(cells[i]) if i is not None and i < len(cells) else ""

            title = cell_at(title_i)
            if not title or not current_date:
                continue
            markers = " ".join(c.get("marker", "") for c in cells)
            entries.append(
                {
                    "title": title,
                    "date": current_date,
                    "time": _parse_time(cell_at(time_i)),
                    "venue": cell_at(venue_i),
                    "confirmed": "remove=y" in markers.replace("&amp;", "&"),
                    "score": _parse_score(cell_at(score_i)),
                }
            )
    return entries


def parse_wishlist(html: str) -> list[dict[str, Any]]:
    """Extract the rated show list {title, score, venue, artist, genre} —
    rendered below the calendar on the same page."""
    entries: list[dict[str, Any]] = []
    for table in parse_html(html).tables:
        if not _is_wishlist_table(_headers_of(table)):
            continue
        for record in _table_records(table):
            title = _cell_text(_find(record, {"title"}))
            if not title:
                continue
            entries.append(
                {
                    "title": title,
                    "score": _parse_score(_cell_text(_find(record, _SCORE_HEADERS))),
                    "venue": _cell_text(_find(record, _VENUE_HEADERS)),
                    "artist": _cell_text(_find(record, {"artist"})),
                    "genre": _cell_text(_find(record, {"genre"})),
                }
            )
    return entries


# --------------------------------------------------------------------------
# Login + fetch
# --------------------------------------------------------------------------

def _make_client(proxy: str | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
        proxy=proxy,
    )


def login(client: httpx.Client, credentials: dict[str, str]) -> None:
    """Perform the ASP.NET form login; raises RuntimeError on rejection."""
    resp = client.get(f"{BASE_URL}/LogOn")
    resp.raise_for_status()
    form = dict(parse_html(resp.text).hidden_inputs)
    form.update(
        {
            "ctl00$MainContent$txtUserId": credentials["user_id"],
            "ctl00$MainContent$txtPassword": credentials["password"],
            "ctl00$MainContent$LogonButton": "Log On",
        }
    )
    resp = client.post(
        f"{BASE_URL}/LogOn", data=form, headers={"Referer": f"{BASE_URL}/LogOn"}
    )
    resp.raise_for_status()
    # A successful login redirects away from the form / shows a log-off link.
    if "txtPassword" in resp.text and "LogOff" not in resp.text:
        raise RuntimeError("PlanMyFringe login rejected — check user id/password")


def _fetch_first(client: httpx.Client, paths: list[str]) -> tuple[str, str]:
    """Return (path, html) for the first candidate path that renders tables."""
    last_error = "no candidate paths"
    for path in paths:
        try:
            resp = client.get(f"{BASE_URL}{path}")
            if resp.status_code != 200:
                last_error = f"{path}: HTTP {resp.status_code}"
                continue
            if parse_html(resp.text).tables:
                return path, resp.text
            last_error = f"{path}: no tables in page"
        except httpx.HTTPError as exc:
            last_error = f"{path}: {exc}"
    return "", last_error


def fetch_planner_data(
    credentials: dict[str, str], *, year: int = DEFAULT_YEAR
) -> dict[str, Any]:
    """Log in and scrape schedule + wishlist. Falls back to the residential
    proxy (FRINGE_PROXY_URL) if the site blocks the direct egress IP."""
    proxy = os.environ.get("FRINGE_PROXY_URL") or None
    client = _make_client()
    try:
        try:
            login(client, credentials)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403 and proxy:
                client.close()
                client = _make_client(proxy)
                login(client, credentials)
            else:
                raise

        page_path, page_html = _fetch_first(client, SCHEDULE_PATHS)
        schedule = parse_schedule(page_html, year=year) if page_path else []
        wishlist = parse_wishlist(page_html) if page_path else []
        return {
            "schedule": schedule,
            "wishlist": wishlist,
            "debug": {
                "page": page_path or f"not found ({page_html})",
            },
        }
    finally:
        client.close()


# --------------------------------------------------------------------------
# Matching against scan data + sync payload
# --------------------------------------------------------------------------

def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _match_show(title: str, shows: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = normalize_title(title)
    if not key:
        return None
    for show in shows:
        if normalize_title(show.get("show_title")) == key:
            return show
    for show in shows:
        t = normalize_title(show.get("show_title"))
        if t and (key in t or t in key):
            return show
    return None


def _perf_for(show: dict[str, Any], date: str, time: str) -> dict[str, Any] | None:
    perfs = [p for p in show.get("performances") or [] if p.get("date") == date]
    if not perfs:
        return None
    if time:
        exact = [p for p in perfs if p.get("time") == time]
        if exact:
            return exact[0]
    return perfs[0]


def sync_planner(
    *,
    credentials: dict[str, str],
    latest: dict[str, Any] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Scrape PlanMyFringe and build the sync outputs:

    returns {"planner": <payload for data/planner.json>,
             "watch_items": <watchlist imports (unconfirmed, matched)>,
             "summary": <counts for the API response>}
    """
    latest = latest or {}
    year = DEFAULT_YEAR
    start = latest.get("start_date") or ""
    if re.match(r"^\d{4}-", start):
        year = int(start[:4])

    scraped = fetch_planner_data(credentials, year=year)
    shows = latest.get("shows") or []

    schedule_out: list[dict[str, Any]] = []
    watch_items: list[dict[str, Any]] = []
    for entry in scraped["schedule"]:
        show = _match_show(entry["title"], shows)
        perf = _perf_for(show, entry["date"], entry["time"]) if show else None
        item = {
            **entry,
            "matched_show_title": show.get("show_title") if show else None,
            "slug": show.get("slug") if show else None,
            "url": show.get("url") if show else None,
            "performance_id": perf.get("performance_id") if perf else None,
            "availability": perf.get("availability") if perf else None,
        }
        schedule_out.append(item)
        if entry["confirmed"] or perf is None:
            continue
        watch_items.append(
            {
                "slug": show.get("slug") or "",
                "show_title": show.get("show_title") or entry["title"],
                "performance_id": perf["performance_id"],
                "box_office_id": perf.get("box_office_id") or "",
                "date": perf.get("date") or entry["date"],
                "time": perf.get("time") or entry["time"],
                "availability": perf.get("availability") or "",
                "url": show.get("url") or "",
                "source": "planmyfringe",
            }
        )

    wishlist_out: list[dict[str, Any]] = []
    for entry in scraped["wishlist"]:
        show = _match_show(entry["title"], shows)
        wishlist_out.append(
            {
                **entry,
                "matched_show_title": show.get("show_title") if show else None,
                "slug": show.get("slug") if show else None,
                "url": show.get("url") if show else None,
                "sold_out_dates": show.get("sold_out_dates") if show else [],
                "available_dates": show.get("available_dates") if show else [],
            }
        )

    confirmed = sum(1 for e in schedule_out if e["confirmed"])
    summary = {
        "schedule_entries": len(schedule_out),
        "confirmed_booked": confirmed,
        "watchlist_imported": len(watch_items),
        "unmatched_schedule": sum(
            1 for e in schedule_out if e["matched_show_title"] is None
        ),
        "wishlist_entries": len(wishlist_out),
    }
    planner = {
        "synced_at": now_iso or datetime.utcnow().isoformat() + "Z",
        "source": "planmyfringe",
        "schedule": schedule_out,
        "wishlist": wishlist_out,
        "summary": summary,
        "debug": scraped["debug"],
    }
    return {"planner": planner, "watch_items": watch_items, "summary": summary}
