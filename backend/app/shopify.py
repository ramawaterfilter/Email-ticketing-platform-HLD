"""Per-store Shopify order lookups for reply drafting (HLD 4.4).

Orders are fetched filtered by the customer's email, so the result only ever contains
that customer's own orders - this is the privacy guard against fishing for other people's
data (HLD 7). Wrapped behind a small interface so tests inject a fake.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .models import Store

log = logging.getLogger("shopify")

API_VERSION = "2024-10"
MAX_ORDERS = 5


class ShopifyProvider(Protocol):
    def orders_for_email(self, email: str) -> list[dict]: ...


class RealShopify:
    def __init__(self, domain: str, token: str):
        self._domain = domain
        self._token = token

    def orders_for_email(self, email: str) -> list[dict]:
        import httpx

        url = f"https://{self._domain}/admin/api/{API_VERSION}/orders.json"
        params = {"email": email, "status": "any", "limit": MAX_ORDERS, "order": "created_at desc"}
        resp = httpx.get(url, params=params, headers={"X-Shopify-Access-Token": self._token}, timeout=15)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
        # Defence in depth: only keep orders that actually belong to this email.
        return [o for o in orders if (o.get("email") or "").lower() == email.lower()]


def shopify_for(store: Store) -> ShopifyProvider | None:
    """Build a client from the store's stored credentials, or None if not connected."""
    if not store.shopify_domain or not store.shopify_token_enc:
        return None
    from .crypto import decrypt

    return RealShopify(store.shopify_domain, decrypt(store.shopify_token_enc))


def summarize_orders(orders: list[dict]) -> str:
    """Compact, model-friendly summary of a customer's recent orders."""
    if not orders:
        return ""
    lines = ["CUSTOMER'S RECENT ORDERS (from Shopify):"]
    for o in orders[:MAX_ORDERS]:
        name = o.get("name", "?")
        created = (o.get("created_at") or "")[:10]
        financial = o.get("financial_status", "")
        fulfillment = o.get("fulfillment_status") or "unfulfilled"
        total = o.get("total_price", "")
        currency = o.get("currency", "")
        items = ", ".join(
            f"{li.get('quantity', 1)}x {li.get('title', '?')}" for li in o.get("line_items", [])[:10]
        )
        tracking = _tracking(o)
        line = f"- {name} ({created}): {financial}, {fulfillment}; {total} {currency}; items: {items}"
        if tracking:
            line += f"; tracking: {tracking}"
        lines.append(line)
    return "\n".join(lines)


def _tracking(order: dict) -> str:
    out = []
    for f in order.get("fulfillments", []) or []:
        num = f.get("tracking_number")
        url = f.get("tracking_url")
        if num:
            out.append(f"{num}{f' ({url})' if url else ''}")
    return "; ".join(out)
