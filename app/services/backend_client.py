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
from typing import Any, Literal

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import BACKEND_URL, INTERNAL_SERVICE_TOKEN, BACKEND_TIMEOUT_SECONDS
from app.services.net import media_public_url

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

    try:
        body = res.json()
    except ValueError as exc:
        # A 200 that is not JSON (a proxy's HTML page, a truncated body).
        logger.error("Backend %s returned a non-JSON body: %s", path, res.text[:200])
        raise BackendUnavailable(f"Backend returned a non-JSON body for {path}") from exc
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


def catalogue_page(after: str | None = None, limit: int = 100) -> list[dict]:
    """One page of the sellable catalogue, ordered by id, for the search indexer.

    Pass the last id of a page as `after` to fetch the next; an empty or short
    page means the end.
    """
    params: dict[str, Any] = {"limit": limit}
    if after:
        params["after"] = after
    return _get("/internal/products/catalogue", params) or []


# The reasons the backend writes (product-change.ts UPSERT_REASONS / DELETE_REASONS;
# docs/CATALOGUE_FEED.md). `reason` is a free VARCHAR on its side, so a newer
# backend may send one this agent has not heard of: the row is still valid
# (`op` decides what to do), it is just counted as OTHER.
UPSERT_REASONS = frozenset({
    "PUBLISHED", "RESTORED", "RELISTED", "RESTOCKED", "CONTENT", "SELLER_RESTORED",
})
DELETE_REASONS = frozenset({
    "SOLD", "ARCHIVED", "REJECTED", "UNPUBLISHED", "REVIEW", "SUBMITTED",
    "OUT_OF_STOCK", "DELETED", "SELLER_BANNED", "SELLER_DEACTIVATED", "SELLER_OFFLINE",
})
CHANGE_REASONS = UPSERT_REASONS | DELETE_REASONS
OTHER_REASON = "OTHER"

_FEED_MAX_ITEMS = 500   # the backend's maximum `limit`


class ChangeItem(BaseModel):
    """One ProductChange row as GET /internal/products/changes returns it."""
    # Unknown extra keys are ignored (a backend may add one); wrong types are not.
    model_config = ConfigDict(extra="ignore")

    seq: StrictInt = Field(ge=0)
    productId: StrictStr = Field(min_length=1, max_length=64)
    op: Literal["UPSERT", "DELETE"]
    reason: StrictStr = Field(min_length=1, max_length=40)
    contentUpdatedAt: AwareDatetime | None = None

    @field_validator("contentUpdatedAt", mode="before")
    @classmethod
    def _iso_string_only(cls, v):
        # pydantic would also take a number as a unix time; the feed sends ISO text.
        if v is not None and not isinstance(v, str):
            raise ValueError("contentUpdatedAt must be an ISO 8601 string")
        return v

    @field_validator("reason")
    @classmethod
    def _bounded_reason(cls, v: str) -> str:
        return v if v in CHANGE_REASONS else OTHER_REASON

    @model_validator(mode="after")
    def _reason_matches_op(self):
        # A known reason says which op it belongs to; a mismatch is a corrupt row.
        wrong = DELETE_REASONS if self.op == "UPSERT" else UPSERT_REASONS
        if self.reason in wrong:
            raise ValueError(f"reason {self.reason} does not belong to op {self.op}")
        return self


class FeedLag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seconds: StrictInt = Field(ge=0)
    withheld: StrictInt = Field(ge=0)


class ChangesPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ChangeItem] = Field(max_length=_FEED_MAX_ITEMS)
    nextSince: StrictInt = Field(ge=0)
    hasMore: StrictBool
    lag: FeedLag | None = None


def _check_changes_page(page: ChangesPage, since: int, limit: int) -> None:
    """The contract between the items and the cursor (CATALOGUE_FEED.md).

    A page holds rows with seq > since in ascending order, and nextSince is the
    seq of its last row; with nothing ready it equals `since`. Anything that
    would move the cursor past what was actually delivered skips changes, so it
    is refused. A nextSince BELOW `since` is left for the indexer, which treats
    it as a feed that was reset and starts a full resync.
    """
    seqs = [it.seq for it in page.items]
    if len(seqs) > limit:
        raise ValueError(f"{len(seqs)} items for limit {limit}")
    if seqs and seqs[0] <= since:
        raise ValueError(f"item seq {seqs[0]} is not after since {since}")
    if any(b <= a for a, b in zip(seqs, seqs[1:])):
        raise ValueError("item seq values are not strictly ascending")
    if seqs and page.nextSince > seqs[-1]:
        raise ValueError(f"nextSince {page.nextSince} is beyond the last item seq {seqs[-1]}")
    if not seqs and page.nextSince > since:
        raise ValueError(f"empty page but nextSince {page.nextSince} is beyond since {since}")


def changes_page(since: int = 0, limit: int = 200) -> dict:
    """One page of the catalogue change feed (docs/CATALOGUE_FEED.md in the backend).

    Returns {items: [{seq, productId, op: UPSERT|DELETE, reason, contentUpdatedAt}],
    nextSince, hasMore, lag}, validated: types, op, `reason` (bounded to the known
    set, else OTHER) and the cursor rules in _check_changes_page. A malformed page
    raises BackendUnavailable, so the caller's cursor stays where it was and the
    page is read again on the next pass. Store nextSince only after the page is
    processed.
    """
    body = _get("/internal/products/changes", {"since": since, "limit": limit})
    try:
        page = ChangesPage.model_validate(body)
        _check_changes_page(page, since, limit)
    except ValidationError as exc:
        # Locations and messages only: the error's `input` would echo the body.
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        logger.error("Malformed /changes page (since=%d): %s: %s", since, where, first["msg"])
        raise BackendUnavailable(
            f"Backend returned a malformed /changes page ({where}: {first['msg']})") from exc
    except ValueError as exc:
        logger.error("Inconsistent /changes page (since=%d): %s", since, exc)
        raise BackendUnavailable(f"Backend returned an inconsistent /changes page: {exc}") from exc
    return page.model_dump(mode="json")


def absolute_media_url(path: str) -> str:
    """The backend stores uploads as "/uploads/..."; resolve against its origin.

    Uses the internal BACKEND_URL (e.g. http://backend:3000), so image fetches
    stay on the private network instead of going out through Cloudflare. When
    MEDIA_PUBLIC_BASE_URL is set (storage moved to R2) the backend only redirects
    "/uploads/<key>" there, and the agent never follows redirects, so the path is
    mapped straight to "<MEDIA_PUBLIC_BASE_URL>/<key>". Absolute URLs (rows
    written after the move) pass through unchanged.
    """
    if path.startswith(("http://", "https://")):
        return path
    public = media_public_url("/" + path.lstrip("/"))
    if public is not None:
        return public
    origin = BACKEND_URL.rstrip("/").removesuffix("/v1")
    return f"{origin}/{path.lstrip('/')}"


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
