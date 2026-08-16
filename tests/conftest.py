"""Shared test setup.

Everything here runs with NO network access: the edfringe API sits behind
Cloudflare and rate limits aggressively, so tests must never touch it.
Network behavior is exercised through httpx.MockTransport and fake API
objects instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def events() -> list[dict]:
    """Small synthetic programme snapshot (same shape as the GraphQL feed)."""
    return json.loads((FIXTURES / "events.json").read_text())["events"]


@pytest.fixture()
def prices() -> dict:
    """performancePrices responses keyed by box_office_id."""
    return json.loads((FIXTURES / "prices.json").read_text())


from tests.fakes import FakePricesApi  # noqa: E402  (path inserted above)


@pytest.fixture()
def fake_api_factory(prices):
    def make(fail_ids: set[str] | None = None) -> FakePricesApi:
        return FakePricesApi(prices, fail_ids)

    return make
