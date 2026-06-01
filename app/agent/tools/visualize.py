from langchain_core.tools import tool
from app.services.search_service import get_product_by_id
from app.services.visualization_service import visualize_now, RateLimitExceeded


@tool
def visualize_on_wall(user_id: str, room_image_url: str, artwork_id: str,
                      placement: str = "centered on the main wall") -> dict:
    """Place a real catalog artwork onto a customer's wall photo using Gemini.
    Only call this after the customer has chosen a specific artwork.
    Returns a preview_url the frontend can display in an <img> tag.
    """
    row = get_product_by_id(artwork_id)
    if not row:
        return {"error": f"Artwork {artwork_id} not found."}

    images = row.get("images", [])
    artwork_url = images[0] if images else None
    if not artwork_url:
        return {"error": "This artwork has no images."}

    try:
        result = visualize_now(
            user_id=user_id,
            room_url=room_image_url,
            artwork_url=artwork_url,
            placement=placement,
            gen_type="wall",
        )
    except RateLimitExceeded as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Visualization failed: {e}"}

    result["original_image"] = artwork_url
    return result
