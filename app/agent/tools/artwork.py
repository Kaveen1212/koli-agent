import logging

from langchain_core.tools import tool

from app.services.search_service import search_artworks, get_product_by_id
from app.services.backend_client import lookup_products, to_card, BackendUnavailable

# Catalogue facts come from the backend API (see backend_client). Nothing here
# touches the marketplace database directly any more — the agent only owns its
# own tables.

logger = logging.getLogger(__name__)

_UNAVAILABLE = "The catalogue is temporarily unavailable. Tell the customer and ask them to try again shortly."

# A bare [] often left Gemini with nothing to say, and an empty reply surfaces
# as an error in the chat widget. Say explicitly what to do instead.
_NO_MATCHES = {
    "results": [],
    "note": (
        "No artworks matched. Tell the customer honestly — the collection is still "
        "growing — and ask one short question about what they would like."
    ),
}


@tool
def search_art(query: str, category: str = "", price_max: float = 0) -> list | dict:
    """Search the art catalog by style, color, mood, or description.
    Use when the customer describes what kind of art they want.
    Finds art by meaning — 'calm earthy tones' works, not just exact words.
    """
    try:
        rows = search_artworks(query, category=category, price_max=price_max)
    except Exception:
        logger.exception("search_art failed")
        return {"error": _UNAVAILABLE}
    return [to_card(r) for r in rows] or dict(_NO_MATCHES)


@tool
def filter_by_category(category: str, limit: int = 10) -> list | dict:
    """Filter the catalog by an exact category — use when the customer says
    'show me only prints', 'only home décor', 'only paintings', etc.
    Returns products without vector search — faster than search_art for category-only requests.
    """
    try:
        rows = lookup_products(category=category, limit=limit)
    except BackendUnavailable:
        return [{"error": _UNAVAILABLE}]
    return [to_card(r) for r in rows] or dict(_NO_MATCHES)


@tool
def find_artwork_by_title(title: str) -> dict:
    """Find an artwork by its title — use when the customer names a specific artwork
    they saw earlier and you need its ID to call visualize_on_wall.
    Does a direct title match, no vector search needed.
    """
    try:
        rows = lookup_products(title=title, limit=1)
    except BackendUnavailable:
        return {"error": _UNAVAILABLE}
    if not rows:
        return {"error": f"No artwork found with title containing '{title}'"}
    return to_card(rows[0])


@tool
def get_item_details(artwork_id: str) -> dict:
    """Get full details for one artwork by its ID.
    Use when the customer asks for price, size, medium, or wants to buy.
    """
    row = get_product_by_id(artwork_id)
    if not row:
        return {"error": f"Artwork {artwork_id} not found or no longer available."}
    return {
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "description": row.get("description") or "",
        "price": str(row.get("price", "")),
        "medium": row.get("medium") or "",
        "size": row.get("size") or "",
        "images": row.get("images") or [],
    }
