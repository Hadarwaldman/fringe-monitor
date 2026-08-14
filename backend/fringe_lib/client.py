from __future__ import annotations

import asyncio
from typing import Any

import httpx

API_BASE = "https://edfringe-tikketr-web-api.equhost.com"
SHOW_URL = "https://www.edfringe.com/tickets/whats-on/{slug}"

# Anonymous credentials embedded in the public edfringe tickets web app.
ANON_USER = "anonymous"
ANON_PASS = "2add50c2-ac54-4c1e-b5bc-f8d9ca66a067"

# The API's WAF rejects requests without browser-like headers (403).
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.edfringe.com",
    "Referer": "https://www.edfringe.com/",
    "Accept": "application/json",
}

EVENTS_QUERY = """
query EventsSearch($criteria: SearchCriteriaInput!) {
  events(input: $criteria) {
    total
    page
    per
    results {
      id
      title
      slug
      genre
      priceType
      startingDate
      endingDate
      datesDisplay
      venues { title }
      performances {
        id
        title
        dateTime
        soldOut
        ticketStatus
        status
        ticketsAvailable
        boxOfficeId
        cancelled
        badges { label colour }
      }
    }
  }
}
"""

PRICES_QUERY = """
query PerformancePrices($performanceId: String!) {
  performancePrices(performanceRef: $performanceId) {
    success
    error
    result {
      performanceId
      performancePercentageRemaining
      performanceAvailabilityLevel
      allocationDetails
    }
  }
}
"""


class FringeClient:
    def __init__(self, client: httpx.AsyncClient, *, max_retries: int = 4) -> None:
        self._client = client
        self._token: str | None = None
        self._max_retries = max_retries
        self._auth_lock = asyncio.Lock()

    async def authenticate(self) -> None:
        async with self._auth_lock:
            resp = await self._client.post(
                f"{API_BASE}/token",
                json={"username": ANON_USER, "password": ANON_PASS},
                headers=DEFAULT_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            if not token:
                raise RuntimeError(f"Token endpoint returned no token: {data!r}")
            self._token = token

    async def graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._token:
            await self.authenticate()
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(
                    f"{API_BASE}/graphql",
                    json=payload,
                    headers={
                        **DEFAULT_HEADERS,
                        "Authorization": f"Bearer {self._token}",
                    },
                )
                if resp.status_code == 401:
                    await self.authenticate()
                    resp = await self._client.post(
                        f"{API_BASE}/graphql",
                        json=payload,
                        headers={
                            **DEFAULT_HEADERS,
                            "Authorization": f"Bearer {self._token}",
                        },
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get("errors"):
                    raise RuntimeError(f"GraphQL error: {data['errors']}")
                return data["data"]
            except (httpx.TransportError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                await asyncio.sleep(0.4 * (2**attempt))
        assert last_exc is not None
        raise last_exc


async def graphql_with_token(
    client: httpx.AsyncClient,
    token: str,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One-shot GraphQL call with an explicit bearer token (e.g. a logged-in
    customer session for basket operations). No re-auth on 401 — the caller
    owns the token lifecycle."""
    payload: dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    resp = await client.post(
        f"{API_BASE}/graphql",
        json=payload,
        headers={**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]
