"""Ticket 'hold' support: log into the user's edfringe account and add
tickets to their basket, which reserves them for ~30 minutes while the user
completes the purchase on edfringe.com.

Credentials live in an SSM SecureString parameter (JSON: {"email", "password"})
named by the EDFRINGE_CREDS_PARAM env var — never in code, config, or DynamoDB.
If the parameter is missing, hold attempts are skipped gracefully.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .client import FringeClient, graphql_with_token

LOGIN_QUERY = """
query Login($email: String!, $password: String!) {
  login(emailAddress: $email, password: $password, userAgent: "web", keepMeSignedIn: false) {
    success
    error
    message
    result { viaCustomerId token }
  }
}
"""

HOLD_PRICES_QUERY = """
query PerformancePrices($performanceId: String!) {
  performancePrices(performanceRef: $performanceId) {
    success
    error
    result {
      performanceId
      prices {
        priceBandId
        priceValue
        totalPrice
        description
        pricetype
        availabilityLevel
      }
    }
  }
}
"""

ADD_TICKETS_MUTATION = """
mutation AddTickets($performanceRef: String!, $customerId: Int, $sessionId: String, $tickets: [AddTicketRequestInput!]!) {
  addTickets(performanceRef: $performanceRef, customerId: $customerId, sessionId: $sessionId, tickets: $tickets) {
    success
    error
    message
    result {
      sessionId
      summary { total noTickets timeToExpiry }
      events {
        name
        performances {
          performanceId
          dateTime
          tickets { quantity priceBandTitle finalPrice }
        }
      }
    }
  }
}
"""


def load_proxy_into_env() -> str | None:
    """Populate FRINGE_PROXY_URL from the SSM SecureString named by
    FRINGE_PROXY_PARAM, so make_async_client() routes through the residential
    proxy. The parameter's value is a full proxy URL
    (http://user:pass@host:port). Idempotent; returns the URL or None.

    Kept out of Terraform state: only the parameter *name* is configured; the
    secret value is written manually via `aws ssm put-parameter`.
    """
    if os.environ.get("FRINGE_PROXY_URL"):
        return os.environ["FRINGE_PROXY_URL"]
    param_name = os.environ.get("FRINGE_PROXY_PARAM")
    if not param_name:
        return None
    import boto3
    from botocore.exceptions import ClientError

    try:
        resp = boto3.client("ssm").get_parameter(Name=param_name, WithDecryption=True)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            print("warn: proxy SSM parameter not found; using direct egress", flush=True)
            return None
        raise
    url = (resp["Parameter"]["Value"] or "").strip()
    if not url:
        return None
    os.environ["FRINGE_PROXY_URL"] = url
    return url


def get_fringe_credentials() -> dict[str, str] | None:
    """Read {"email", "password"} from SSM; None when not configured."""
    param_name = os.environ.get("EDFRINGE_CREDS_PARAM")
    if not param_name:
        return None
    import boto3
    from botocore.exceptions import ClientError

    try:
        resp = boto3.client("ssm").get_parameter(
            Name=param_name, WithDecryption=True
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise
    try:
        creds = json.loads(resp["Parameter"]["Value"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not creds.get("email") or not creds.get("password"):
        return None
    return {"email": creds["email"], "password": creds["password"]}


def credentials_configured() -> bool:
    try:
        return get_fringe_credentials() is not None
    except Exception as exc:  # noqa: BLE001
        print(f"warn: could not check edfringe credentials: {exc}", flush=True)
        return False


def pick_price_band(prices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the band to hold: prefer one with availability, else the first."""
    if not prices:
        return None
    for price in prices:
        level = (price.get("availabilityLevel") or "").strip().lower()
        if level not in {"none", "soldout", "sold_out"}:
            return price
    return prices[0]


async def hold_tickets(
    api: FringeClient,
    http: httpx.AsyncClient,
    *,
    box_office_id: str,
    quantity: int,
    credentials: dict[str, str],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Log in and add `quantity` full-price tickets for the performance to the
    user's basket. Returns a summary dict; never raises on the expected
    failure modes (bad login, no prices, add rejected)."""
    result: dict[str, Any] = {
        "success": False,
        "box_office_id": box_office_id,
        "quantity": quantity,
    }

    try:
        data = await api.graphql(
            LOGIN_QUERY,
            {"email": credentials["email"], "password": credentials["password"]},
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"login request failed: {exc}"
        return result
    login = data.get("login") or {}
    if not login.get("success") or not (login.get("result") or {}).get("token"):
        result["error"] = f"login rejected: {login.get('error') or login.get('message')}"
        return result
    user_token = login["result"]["token"]
    customer_id = login["result"]["viaCustomerId"]

    try:
        data = await graphql_with_token(
            http, user_token, HOLD_PRICES_QUERY, {"performanceId": box_office_id}
        )
        prices = ((data.get("performancePrices") or {}).get("result") or {}).get(
            "prices"
        ) or []
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"price lookup failed: {exc}"
        return result

    band = pick_price_band(prices)
    if band is None:
        result["error"] = "no price bands returned for performance"
        return result

    variables: dict[str, Any] = {
        "performanceRef": box_office_id,
        "customerId": customer_id,
        "sessionId": session_id,
        "tickets": [
            {
                "priceBandId": int(band["priceBandId"]),
                "quantity": int(quantity),
                "priceOverrideSpecified": False,
            }
        ],
    }
    try:
        data = await graphql_with_token(http, user_token, ADD_TICKETS_MUTATION, variables)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"addTickets failed: {exc}"
        return result

    added = data.get("addTickets") or {}
    basket = added.get("result") or {}
    summary = basket.get("summary") or {}
    result.update(
        {
            "success": bool(added.get("success")),
            "error": added.get("error") or added.get("message"),
            "session_id": basket.get("sessionId"),
            "price_band": band.get("description") or "",
            "unit_price": band.get("priceValue"),
            "basket_total": summary.get("total"),
            "basket_tickets": summary.get("noTickets"),
            "expires_in": summary.get("timeToExpiry"),
        }
    )
    if result["success"]:
        result.pop("error", None)
    return result
