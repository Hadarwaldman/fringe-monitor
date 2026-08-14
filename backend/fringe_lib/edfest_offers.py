"""Fetch EdFest / Love the Fringe offer listings and attach them to performances."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import date
from typing import Any

import httpx

from .models import PerformanceRow

EDFEST_BASE = "https://edfest.com"
OFFERS_URL = f"{EDFEST_BASE}/api/projects/offers"
PRODUCTS_URL = f"{EDFEST_BASE}/api/products"
PROJECTS_URL = f"{EDFEST_BASE}/api/projects"
OFFERS_PAGE_URL = f"{EDFEST_BASE}/offers"
EDFEST_SHOW_URL = f"{EDFEST_BASE}/whats-on/{{slug}}"

# Prefer larger pages; API accepts at least 200.
PAGE_LIMIT = 200

# Native edfringe ticket statuses that are themselves offers.
STATUS_OFFERS = {
    "TWO_FOR_ONE": {"code": "TWO_FOR_ONE", "label": "2 for 1", "slug": "2for1"},
}

# Event-level priceType values from the edfringe GraphQL API.
PRICE_TYPE_OFFERS = {
    "FRIENDS_TWO_FOR_ONE": {
        "code": "FRIENDS_TWO_FOR_ONE",
        "label": "Fringe Friends",
        "slug": "fringe-friends",
    },
    "TWO_FOR_ONE": {
        "code": "TWO_FOR_ONE",
        "label": "2 for 1",
        "slug": "2for1",
    },
    "PAY_WHAT_YOU_WANT": {
        "code": "PAY_WHAT_YOU_WANT",
        "label": "Pay what you want",
        "slug": "pay-what-you-want",
    },
    "GROUP_DISCOUNTS": {
        "code": "GROUP_DISCOUNTS",
        "label": "Group discounts",
        "slug": "group-discounts",
    },
}


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _day_from_edfest(value: str | None) -> str | None:
    if not value:
        return None
    # "2026-08-12T12:00:00" — date portion is Edinburgh-local on EdFest.
    return value[:10] if len(value) >= 10 else None


def _offer_from_row(row: dict[str, Any]) -> dict[str, str] | None:
    code = (row.get("offer_code") or "").strip()
    label = (row.get("offer_type") or "").strip()
    if not code and not label:
        return None
    slug = ""
    # Derive a stable slug-ish key from the human label when products aren't loaded.
    if label:
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return {
        "code": code or slug or "offer",
        "label": label or code,
        "slug": slug,
    }


def _merge_offer(target: list[dict[str, str]], offer: dict[str, str]) -> None:
    code = offer.get("code") or ""
    label = offer.get("label") or ""
    for existing in target:
        same_code = bool(code) and existing.get("code") == code
        same_label = bool(label) and existing.get("label") == label
        if same_code or same_label:
            if not existing.get("label") and label:
                existing["label"] = label
            if not existing.get("slug") and offer.get("slug"):
                existing["slug"] = offer["slug"]
            return
    target.append(
        {
            "code": code,
            "label": label,
            "slug": offer.get("slug") or "",
        }
    )


async def fetch_edfest_title_slugs(
    client: httpx.AsyncClient,
    *,
    page_limit: int = PAGE_LIMIT,
    max_pages: int = 50,
) -> dict[str, str]:
    """Map normalized show title → EdFest slug for the full published catalogue.

    Used to build per-show EdFest ticket links (EDFEST_SHOW_URL) even for
    shows that have no current offer. Paginates /api/projects until a short
    page; failures should be treated as non-fatal by callers.
    """
    slugs: dict[str, str] = {}
    page = 1
    while page <= max_pages:
        resp = await client.get(
            PROJECTS_URL,
            params={"page": page, "limit": page_limit},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                continue
            key = normalize_title(row.get("name") or "")
            slug = (row.get("slug") or "").strip()
            if key and slug:
                slugs.setdefault(key, slug)
        if len(batch) < page_limit:
            break
        page += 1
    print(f"EdFest catalogue: {len(slugs)} shows ({page} page(s))", flush=True)
    return slugs


async def fetch_offer_products(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """Return product catalogue (Love the Fringe, 2 for 1, …)."""
    resp = await client.get(PRODUCTS_URL, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "code": (item.get("red61_offer_code") or "").strip(),
                "label": (item.get("offer_text") or item.get("name") or "").strip(),
                "slug": (item.get("slug") or "").strip(),
            }
        )
    return out


async def _fetch_offers_page(
    client: httpx.AsyncClient,
    *,
    page: int,
    date_from: str,
    date_to: str,
    offer_types: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": page,
        "limit": PAGE_LIMIT,
        "date_from": date_from,
        "date_to": date_to,
    }
    if offer_types:
        params["offer_types"] = offer_types
    resp = await client.get(
        OFFERS_URL,
        params=params,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


async def fetch_edfest_offer_rows(
    client: httpx.AsyncClient,
    *,
    start: date,
    end: date,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Paginate /api/projects/offers for the date window (one row per show+time+offer)."""
    date_from = start.isoformat()
    date_to = end.isoformat()
    first = await _fetch_offers_page(
        client, page=1, date_from=date_from, date_to=date_to
    )
    meta = first.get("meta") or {}
    total_pages = int(meta.get("totalPages") or 1)
    rows: list[dict[str, Any]] = list(first.get("data") or [])
    print(
        f"EdFest offers: {meta.get('total', len(rows))} listings "
        f"({date_from} → {date_to}), {total_pages} page(s)…",
        flush=True,
    )
    if total_pages <= 1:
        return rows

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    done = 1

    async def one(page: int) -> list[dict[str, Any]]:
        nonlocal done
        async with sem:
            data = await _fetch_offers_page(
                client, page=page, date_from=date_from, date_to=date_to
            )
        batch = list(data.get("data") or [])
        async with lock:
            done += 1
            if done % 10 == 0 or done == total_pages:
                print(f"  EdFest offers page {done}/{total_pages}", flush=True)
        return batch

    batches = await asyncio.gather(
        *(one(p) for p in range(2, total_pages + 1))
    )
    for batch in batches:
        rows.extend(batch)
    return rows


def enrich_offer_slugs(
    offer: dict[str, str],
    products_by_code: dict[str, dict[str, str]],
) -> dict[str, str]:
    product = products_by_code.get(offer.get("code") or "")
    if not product:
        return offer
    return {
        "code": offer.get("code") or product.get("code") or "",
        "label": offer.get("label") or product.get("label") or "",
        "slug": product.get("slug") or offer.get("slug") or "",
    }


def build_offer_lookup(
    offer_rows: list[dict[str, Any]],
    *,
    products: list[dict[str, str]] | None = None,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """
    Map (match_key, YYYY-MM-DD) → offers.

    match_key is either `slug:<edfest-slug>` or `title:<normalized-title>`.
    """
    products_by_code = {
        p["code"]: p for p in (products or []) if p.get("code")
    }
    lookup: dict[tuple[str, str], list[dict[str, str]]] = {}

    for row in offer_rows:
        day = _day_from_edfest(row.get("date"))
        if not day:
            continue
        offer = _offer_from_row(row)
        if not offer:
            continue
        offer = enrich_offer_slugs(offer, products_by_code)
        slug = (row.get("slug") or "").strip()
        title_key = normalize_title(row.get("name") or "")
        keys: list[str] = []
        if slug:
            keys.append(f"slug:{slug}")
        if title_key:
            keys.append(f"title:{title_key}")
        for key in keys:
            bucket = lookup.setdefault((key, day), [])
            _merge_offer(bucket, offer)
    return lookup


def offers_for_performance(
    row: PerformanceRow,
    lookup: dict[tuple[str, str], list[dict[str, str]]],
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    day = row.date_local
    keys = []
    if row.slug:
        keys.append(f"slug:{row.slug}")
    title_key = normalize_title(row.show_title)
    if title_key:
        keys.append(f"title:{title_key}")
    for key in keys:
        for offer in lookup.get((key, day), []):
            _merge_offer(found, offer)

    # Show-level edfringe price types (Fringe Friends 1+1, public 2-for-1, …).
    for price_type in row.price_types or []:
        offer = PRICE_TYPE_OFFERS.get(str(price_type))
        if offer:
            _merge_offer(found, dict(offer))

    status_offer = STATUS_OFFERS.get(row.ticket_status)
    if status_offer:
        _merge_offer(found, dict(status_offer))
    return found


def attach_offers_to_rows(
    rows: list[PerformanceRow],
    lookup: dict[tuple[str, str], list[dict[str, str]]],
) -> dict[str, int]:
    """Mutate rows with offers; return simple match stats."""
    matched_perfs = 0
    for row in rows:
        offers = offers_for_performance(row, lookup)
        row.offers = offers
        if offers:
            matched_perfs += 1
    shows_with = {
        (r.slug, r.show_title) for r in rows if r.offers
    }
    return {
        "performances_with_offers": matched_perfs,
        "shows_with_offers": len(shows_with),
        "lookup_keys": len(lookup),
    }


async def fetch_and_attach_edfest_offers(
    client: httpx.AsyncClient,
    rows: list[PerformanceRow],
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """
    Fetch EdFest offers for the window and attach to rows.

    Failures are non-fatal: returns stats with error and leaves rows unchanged
    aside from native TWO_FOR_ONE status offers.
    """
    # Always surface native ticket-status offers even if EdFest is down.
    native_lookup: dict[tuple[str, str], list[dict[str, str]]] = {}
    try:
        products = await fetch_offer_products(client)
        offer_rows = await fetch_edfest_offer_rows(client, start=start, end=end)
        lookup = build_offer_lookup(offer_rows, products=products)
        stats = attach_offers_to_rows(rows, lookup)
        stats.update(
            {
                "ok": True,
                "offer_listings": len(offer_rows),
                "products": len(products),
                "source": OFFERS_PAGE_URL,
            }
        )
        print(
            f"EdFest offers attached: {stats['performances_with_offers']} performances, "
            f"{stats['shows_with_offers']} shows "
            f"(from {stats['offer_listings']} listings)",
            flush=True,
        )
        return stats
    except Exception as exc:  # noqa: BLE001
        print(f"warn: EdFest offers fetch failed: {exc}", flush=True)
        stats = attach_offers_to_rows(rows, native_lookup)
        stats.update(
            {
                "ok": False,
                "error": str(exc),
                "offer_listings": 0,
                "products": 0,
                "source": OFFERS_PAGE_URL,
            }
        )
        return stats
