"""Offline stand-ins shared by the test suite and scripts/dev_server.py."""

from __future__ import annotations


class FakePricesApi:
    """Stands in for FringeClient in enrich_with_prices code paths.

    `prices` maps box_office_id → performancePrices result dict.
    IDs listed in `fail_ids` raise, mimicking 429 exhaustion.
    """

    def __init__(self, prices: dict, fail_ids: set[str] | None = None) -> None:
        self.prices = prices
        self.fail_ids = fail_ids or set()
        self.calls: list[str] = []

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        box_id = (variables or {}).get("performanceId")
        self.calls.append(box_id)
        if box_id in self.fail_ids:
            raise RuntimeError("rate limited (429) on all 4 attempts")
        result = self.prices.get(box_id)
        return {"performancePrices": {"success": True, "result": result}}
