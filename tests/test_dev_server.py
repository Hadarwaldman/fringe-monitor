"""Dev-server smoke test: the offline test rig itself must keep working.

Boots scripts/dev_server.py on an ephemeral port and checks the frontend
pages and demo /data endpoints serve — this is the path both humans and
Claude use to verify the site without touching CloudFront or edfringe.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "dev_server", REPO_ROOT / "scripts" / "dev_server.py"
)
dev_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_server)


class Args:
    latency = 0
    fail_rate = 0.0
    gzip = True


@pytest.fixture(scope="module")
def server_url():
    data_files = dev_server.build_demo_data()
    handler = dev_server.make_handler(Args, data_files, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as res:
        return res.status, dict(res.headers), res.read()


def test_serves_frontend_pages(server_url):
    for page in ["/", "/index.html", "/shows.html", "/show.html", "/monitors.html", "/settings.html"]:
        status, headers, body = get(server_url + page)
        assert status == 200, page
        assert b"<body" in body, page
    # Every page must load net.js before its page script.
    _, _, body = get(server_url + "/shows.html")
    assert body.index(b"net.js") < body.index(b"app.js")


def test_config_js_points_at_stub_api(server_url):
    _, _, body = get(server_url + "/config.js")
    assert b'"/api"' in body


def test_demo_latest_json_shape(server_url):
    status, headers, body = get(server_url + "/data/latest.json")
    assert status == 200
    data = json.loads(body)
    assert data["show_count"] == 3
    show = data["shows"][0]
    for key in ["show_title", "slug", "performances", "sold_out_dates", "offer_dates", "trend"]:
        assert key in show
    perf = show["performances"][0]
    for key in ["performance_id", "date", "time", "availability", "percent_remaining"]:
        assert key in perf


def test_data_served_gzipped_like_production(server_url):
    status, headers, body = get(
        server_url + "/data/latest.json", {"Accept-Encoding": "gzip"}
    )
    assert status == 200
    assert headers.get("Content-Encoding") == "gzip"
    json.loads(gzip.decompress(body))


def test_api_stubs(server_url):
    _, _, body = get(server_url + "/api/config")
    assert json.loads(body)["start_date"] == "2026-08-12"
    _, _, body = get(server_url + "/api/monitors")
    assert json.loads(body)["items"]


def post(url: str, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        return res.status, json.loads(res.read())


def test_live_availability_stub(server_url):
    """POST /availability — the itinerary's live re-price, one id per entry."""
    _, latest = None, json.loads(get(server_url + "/data/latest.json")[2])
    box_ids = [
        p["box_office_id"]
        for show in latest["shows"]
        for p in show["performances"]
        if p.get("box_office_id")
    ][:3]
    assert box_ids, "demo scan should carry box-office ids"

    status, data = post(server_url + "/api/availability", {"box_office_ids": box_ids})
    assert status == 200
    assert data["checked_at"]
    for box_id in box_ids:
        fresh = data["performances"][box_id]
        assert fresh["availability"] in {"sold_out", "nearly_sold_out", "available"}
        assert isinstance(fresh["percent_remaining"], int)


def test_live_availability_stub_rejects_bad_body(server_url):
    try:
        post(server_url + "/api/availability", {"box_office_ids": "nope"})
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
    else:
        raise AssertionError("expected HTTP 400")


def test_optional_data_files_present(server_url):
    for name in ["details.json", "config.json", "planner.json", "history.json"]:
        status, _, body = get(f"{server_url}/data/{name}")
        assert status == 200, name
        json.loads(body)
