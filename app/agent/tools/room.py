from langchain_core.tools import tool
from app.services.image_service import analyze_room_image


@tool
def analyze_room(room_image_url: str) -> dict:
    """Analyze a customer's room photo for wall color, lighting, and style.
    Always call this first when the customer shares a room photo.
    """
    try:
        return analyze_room_image(room_image_url)
    except Exception as e:
        return {"error": f"Could not analyze the room photo: {e}"}
