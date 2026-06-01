import io
import os
import httpx
from PIL import Image
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY, IMAGE_MODEL, VISION_MODEL
from app.services.gemini_retry import with_retry

client = genai.Client(api_key=GEMINI_API_KEY)


def _fetch(url: str) -> bytes:
    if "localhost" in url or "127.0.0.1" in url:
        local_path = url.split("/static/")[1].split("?")[0]
        with open(os.path.join("static", local_path), "rb") as f:
            return f.read()
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _compress(image_bytes: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def analyze_room_image(image_url: str) -> dict:
    """Download a room photo and ask Gemini to describe it for art recommendations.
    Returns wall color, lighting, style, and what art would suit the space.
    """
    image_bytes = _compress(_fetch(image_url))

    response = with_retry(
        client.models.generate_content,
        model=VISION_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            (
                "Analyze this room for art recommendations. Return exactly these four things: "
                "1. Wall color (e.g. warm white, light grey, deep navy). "
                "2. Lighting style (warm, cool, or natural). "
                "3. Room style (minimalist, modern, traditional, eclectic, industrial, etc). "
                "4. One sentence: what kind of art would complement this room."
            ),
        ],
    )
    return {"room_analysis": response.text}


def generate_wall_visualization(
    room_image_url: str,
    artwork_image_url: str,
    placement_hint: str = "centered on the main wall",
) -> dict:
    """Download both images and ask Gemini to composite the artwork onto the wall.
    Returns the generated image bytes, mime type, and a preview label.
    """
    room_bytes    = _compress(_fetch(room_image_url))
    artwork_bytes = _compress(_fetch(artwork_image_url))

    response = with_retry(
        client.models.generate_content,
        model=IMAGE_MODEL,
        contents=[
            types.Part.from_bytes(data=room_bytes,    mime_type="image/jpeg"),
            types.Part.from_bytes(data=artwork_bytes, mime_type="image/jpeg"),
            (
                f"Place the artwork from image 2 {placement_hint} in image 1. "
                "Match the room lighting and perspective. "
                "Do not change the artwork's colors or composition. "
                "Keep all other room contents unchanged."
            ),
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data:
            return {
                "image_bytes": part.inline_data.data,
                "mime_type":   part.inline_data.mime_type,
                "label":       "AI visualization preview — actual artwork may vary slightly.",
            }

    return {"error": "Gemini did not return an image."}
