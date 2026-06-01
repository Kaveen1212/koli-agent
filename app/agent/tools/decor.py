from langchain_core.tools import tool
from app.services.search_service import get_product_by_id
from app.services.visualization_service import visualize_now, RateLimitExceeded


@tool
def stage_decor(user_id: str, surface_image_url: str, decor_item_id: str,
                placement: str = "on the surface visible in the image") -> dict:
    """Place a real catalog décor item onto a customer's table or shelf photo.
    Only call this after the customer has chosen a specific décor item.
    Use for tables, shelves, mantels — not walls (use visualize_on_wall for walls).
    Returns a preview_url the frontend can display in an <img> tag.
    """
    row = get_product_by_id(decor_item_id)
    if not row:
        return {"error": f"Décor item {decor_item_id} not found."}

    images = row.get("images", [])
    item_url = images[0] if images else None
    if not item_url:
        return {"error": "This décor item has no images."}

    try:
        result = visualize_now(
            user_id=user_id,
            room_url=surface_image_url,
            artwork_url=item_url,
            placement=f"Place the décor item {placement}",
            gen_type="decor",
        )
    except RateLimitExceeded as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Staging failed: {e}"}

    result["original_image"] = item_url
    return result
