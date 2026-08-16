"""FringeClient auth/retry behavior via httpx.MockTransport — no network."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.fringe_lib.client import FringeClient

GRAPHQL_OK = {"data": {"events": {"total": 0, "results": []}}}


class Script:
    """Programmable fake edfringe API."""

    def __init__(self, graphql_responses):
        self.graphql_responses = list(graphql_responses)
        self.token_calls = 0
        self.graphql_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            self.token_calls += 1
            return httpx.Response(200, json={"token": f"tok-{self.token_calls}"})
        if request.url.path == "/graphql":
            self.graphql_calls += 1
            if not self.graphql_responses:
                raise AssertionError("graphql called more times than scripted")
            status, body = self.graphql_responses.pop(0)
            return httpx.Response(status, json=body)
        raise AssertionError(f"unexpected path {request.url.path}")


def run(script: Script, *, max_retries: int = 4):
    async def go():
        transport = httpx.MockTransport(script.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            api = FringeClient(client, max_retries=max_retries)
            return await api.graphql("query { x }")

    return asyncio.run(go())


def test_authenticates_then_queries():
    script = Script([(200, GRAPHQL_OK)])
    data = run(script)
    assert data == GRAPHQL_OK["data"]
    assert script.token_calls == 1


def test_reauthenticates_on_401():
    script = Script([(401, {}), (200, GRAPHQL_OK)])
    data = run(script)
    assert data == GRAPHQL_OK["data"]
    assert script.token_calls == 2  # initial + refresh
    assert script.graphql_calls == 2


def test_retries_429_then_succeeds():
    script = Script([(429, {}), (429, {}), (200, GRAPHQL_OK)])
    data = run(script)
    assert data == GRAPHQL_OK["data"]
    assert script.graphql_calls == 3


def test_all_429_raises_rate_limited():
    script = Script([(429, {}), (429, {})])
    with pytest.raises(RuntimeError, match="rate limited"):
        run(script, max_retries=2)


def test_graphql_errors_raise():
    script = Script([(200, {"data": None, "errors": [{"message": "boom"}]})])
    with pytest.raises(RuntimeError, match="GraphQL error"):
        run(script)


def test_http_500_raises():
    script = Script([(500, {})])
    with pytest.raises(httpx.HTTPStatusError):
        run(script)
