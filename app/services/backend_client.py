"""HTTP client for the koli-ART backend.

The agent used to read the marketplace catalogue straight out of Postgres with
raw SQL against Prisma-owned tables ("Product", p."isDeleted", the ProductStatus
enum). That made every backend migration a silent breaking change here, with no
contract and no versioning between the two services.

Catalogue data now comes through the backend's /v1/internal/products routes.
The agent still owns its own tables (artwork_embeddings, chat_sessions,
generations, room_uploads) and still does its own vector search — only the
product lookups moved behind the API.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import BACKEND_URL, INTERNAL_SERVICE_TOKEN, BACKEND_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class BackendUnavailable(RuntimeError):
    """The backend could not be reached or returned an error."""


def _headers() -> dict[str, str]:
    return {
        "X-Service-Token": INTERNAL_SERVICE_TOKEN,
        "Accept": "application/json",
    }


def _get(path: str, params: dict[str, Any]) -> Any:
    if not BACKEND_URL:
        raise BackendUnavailable("BACKEND_URL is not configured")

    url = f"{BACKEND_URL.rstrip('/')}{path}"
    try:
        res = httpx.get(
            url, params=params, headers=_headers(), timeout=BACKEND_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        logger.error("Backend request to %s failed: %s", path, exc)
        raise BackendUnavailable(str(exc)) from exc

    if res.status_code == 401:
        raise BackendUnavailable(
            "Backend rejected the service token — check INTERNAL_SERVICE_TOKEN "
            "matches on both sides"
        )
    if res.is_error:
        logger.error("Backend %s returned %s: %s", path, res.status_code, res.text[:300])
        raise BackendUnavailable(f"Backend returned {res.status_code}")

    body = res.json()
    # The backend wraps every response as { success, data, timestamp }.
    return body.get("data", body) if isinstance(body, dict) else body


def products_by_ids(ids: list[str]) -> list[dict]:
    """Hydrate product ids, preserving the order given.

    Order matters: the caller's sequence is the similarity ranking from the
    vector index, and the backend honours it. Ids that are no longer sellable
    (delisted, deleted, out of stock) are dropped rather than returned stale.
    """
    if not ids:
        return []
    return _get("/internal/products/by-ids", {"ids": ",".join(ids)}) or []


def lookup_products(
    title: str = "", category: str = "", limit: int = 10
) -> list[dict]:
    """Non-semantic lookup — exact-ish title match or category filter."""
    params: dict[str, Any] = {"limit": limit}
    if title:
        params["title"] = title
    if category:
        params["category"] = category
    return _get("/internal/products/lookup", params) or []


def to_card(row: dict) -> dict:
    """Shape a backend product into the compact form the LLM tools return."""
    images = row.get("images") or []
    return {
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "price": str(row.get("price", "")),
        "medium": row.get("medium") or "",
        "size": row.get("size") or "",
        "category": row.get("category") or "",
        "image_url": images[0] if images else "",
    }
