#!/usr/bin/env python3
"""Local dev server: run the real frontend with NO AWS and NO edfringe.

The edfringe API sits behind Cloudflare (403s datacenter IPs, aggressive 429s),
so "open the live site to check something" is not a reliable test path — this
server is. It serves:

  /            → frontend/ (the actual production HTML/JS/CSS)
  /config.js   → overridden to point the API at this server's /api stubs
  /data/*      → scan JSON from --data DIR, or demo data built from
                 tests/fixtures through the real scan pipeline (no network)
  /api/*       → minimal stubs for /config and /monitors

Weak-network simulation (to exercise the cached-first/retry/offline UI):

  --latency 2000     add 2s to every response
  --fail-rate 0.5    randomly 503 half of /data/* responses
  --gzip             serve /data/*.json gzip-encoded like production

Examples:
  python scripts/dev_server.py                        # demo data
  python scripts/dev_server.py --data output          # your local scan output
  python scripts/dev_server.py --latency 3000 --fail-rate 0.4
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FRONTEND = REPO_ROOT / "frontend"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

DEV_CONFIG_JS = 'window.FRINGE_CONFIG = {\n  apiUrl: "/api",\n};\n'


def build_demo_data() -> dict[str, dict]:
    """Run the fixture programme through the real scan pipeline, offline."""
    import asyncio

    from backend.fringe_lib.scan import (
        build_latest_payload,
        collect_show_details,
        collect_window_rows,
        enrich_with_prices,
    )
    from backend.fringe_lib.trend import (
        attach_trends,
        build_day_snapshot,
        merge_history,
        scan_date_from_payload,
    )
    from tests.fakes import FakePricesApi

    events = json.loads((FIXTURES / "events.json").read_text())["events"]
    prices = json.loads((FIXTURES / "prices.json").read_text())
    start, end = date(2026, 8, 12), date(2026, 8, 20)

    rows = collect_window_rows(events, start, end)
    asyncio.run(enrich_with_prices(FakePricesApi(prices), rows))
    latest = build_latest_payload(rows, start=start, end=end, nearly_threshold=20)

    # A little synthetic history so the trend column renders.
    history = {"days": []}
    for offset, factor in ((3, 0.7), (2, 0.8), (1, 0.9)):
        day = f"2026-08-{13 - offset:02d}"
        snap = build_day_snapshot(latest["shows"], day)
        for entry in snap["shows"].values():
            entry["avg_percent_sold"] = round(entry["avg_percent_sold"] * factor, 2)
        history = merge_history(history, snap)
    history = merge_history(history, build_day_snapshot(latest["shows"], scan_date_from_payload(latest)))
    attach_trends(latest["shows"], history)

    details = collect_show_details(events, slugs={s["slug"] for s in latest["shows"]})
    config = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "nearly_threshold": 20,
        "notify_email": "dev@example.test",
        "auto_watch_sold_out": True,
    }
    return {
        "latest.json": latest,
        "details.json": {"fetched_at": latest["fetched_at"], "shows": details},
        "history.json": history,
        "config.json": config,
        "planner.json": {
            "synced_at": latest["fetched_at"],
            "schedule": [
                {
                    "date": "2026-08-14",
                    "title": "Alpha Comedy Hour",
                    "venue": "Roaring Stag",
                    "time": "19:30",
                    "confirmed": True,
                    "past": False,
                },
                {
                    "date": "2026-08-15",
                    "title": "Beta: A Drama",
                    "venue": "Hidden Door",
                    "time": "21:00",
                    "confirmed": False,
                    "past": False,
                },
            ],
            "wishlist": [
                {"title": "Gamma for Kids!", "matched_show_title": "Gamma for Kids!", "score": 9},
            ],
        },
    }


DEMO_MONITORS = {
    "items": [
        {
            "monitor_id": "demo00000001",
            "slug": "alpha-comedy-hour",
            "show_title": "Alpha Comedy Hour",
            "url": "https://www.edfringe.com/tickets/whats-on/alpha-comedy-hour",
            "start_date": "2026-08-14",
            "end_date": "2026-08-16",
            "active": True,
            "last_checked_at": "2026-08-16T08:00:00Z",
            "last_alert_at": None,
            "last_result": [
                {
                    "performance_id": 1002,
                    "date": "2026-08-15",
                    "time": "19:30",
                    "availability": "nearly_sold_out",
                    "percent_remaining": 15,
                },
            ],
        }
    ]
}


def make_handler(args, data_files: dict[str, dict] | None, data_dir: Path | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *log_args):  # quieter default log line
            sys.stderr.write(f"  {self.command} {self.path} → {fmt % log_args}\n")

        def _send(self, status, body: bytes, content_type: str, *, gzipped=False):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status, payload, *, allow_gzip=False):
            body = json.dumps(payload).encode("utf-8")
            gzipped = False
            if allow_gzip and args.gzip and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body = gzip.compress(body)
                gzipped = True
            self._send(status, body, "application/json", gzipped=gzipped)

        def _maybe_degrade(self, is_data: bool) -> bool:
            """Apply --latency / --fail-rate. Returns True if a failure was sent."""
            if args.latency:
                time.sleep(args.latency / 1000.0)
            if is_data and args.fail_rate and random.random() < args.fail_rate:
                self._send(503, b'{"error":"simulated failure"}', "application/json")
                return True
            return False

        def do_POST(self):
            if self._maybe_degrade(False):
                return
            if self.path.startswith("/api/"):
                self._send_json(501, {"error": "dev server: write endpoints are stubs"})
                return
            self._send(404, b"not found", "text/plain")

        do_PUT = do_POST
        do_DELETE = do_POST

        def do_GET(self):
            path = self.path.split("?", 1)[0]

            is_data = path.startswith("/data/")
            if self._maybe_degrade(is_data):
                return

            if path == "/config.js":
                self._send(200, DEV_CONFIG_JS.encode(), CONTENT_TYPES[".js"])
                return

            if path == "/api/config":
                cfg = (data_files or {}).get("config.json") if data_files else None
                if cfg is None and data_dir and (data_dir / "config.json").exists():
                    cfg = json.loads((data_dir / "config.json").read_text())
                self._send_json(200, cfg or {})
                return

            if path == "/api/monitors":
                self._send_json(200, DEMO_MONITORS)
                return

            if path.startswith("/api/"):
                self._send_json(404, {"error": f"dev server: no stub for {path}"})
                return

            if is_data:
                name = path[len("/data/") :]
                if data_files is not None and name in data_files:
                    self._send_json(200, data_files[name], allow_gzip=True)
                    return
                if data_dir is not None:
                    file_path = (data_dir / name).resolve()
                    if file_path.is_file() and data_dir.resolve() in file_path.parents:
                        self._send_json(
                            200, json.loads(file_path.read_text()), allow_gzip=True
                        )
                        return
                self._send(404, b'{"error":"no such data file"}', "application/json")
                return

            # Static frontend files.
            name = path.lstrip("/") or "index.html"
            file_path = (FRONTEND / name).resolve()
            if not file_path.is_file() or FRONTEND.resolve() not in file_path.parents:
                self._send(404, b"not found", "text/plain")
                return
            body = file_path.read_bytes()
            self._send(200, body, CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument(
        "--data",
        default=None,
        help="Directory with latest.json etc. (e.g. output/ after a local scan). "
        "Default: demo data generated from tests/fixtures.",
    )
    parser.add_argument("--latency", type=int, default=0, help="Added latency per request, ms")
    parser.add_argument("--fail-rate", type=float, default=0.0, help="Probability a /data/* request 503s")
    parser.add_argument("--gzip", action="store_true", help="Serve /data/*.json gzip-encoded like production")
    args = parser.parse_args()

    data_dir = Path(args.data) if args.data else None
    data_files = None
    if data_dir is None:
        data_files = build_demo_data()
        print("Serving demo data built from tests/fixtures (offline).")
    else:
        if not (data_dir / "latest.json").exists():
            print(f"warning: {data_dir}/latest.json not found — run scan_fringe.py first", file=sys.stderr)
        print(f"Serving data from {data_dir}/")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, data_files, data_dir))
    extras = []
    if args.latency:
        extras.append(f"latency {args.latency}ms")
    if args.fail_rate:
        extras.append(f"fail-rate {args.fail_rate}")
    if args.gzip:
        extras.append("gzip")
    print(f"Fringe Monitor dev server → http://127.0.0.1:{args.port}/ {'(' + ', '.join(extras) + ')' if extras else ''}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
