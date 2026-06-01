"""Artnuss watermark compositor — shared by every image generation path."""
import io
import os
import cairosvg
from PIL import Image

WATERMARK_PATH = os.path.join("static", "watermark", "Artnuss.svg")

_watermark_cache: Image.Image | None = None


def _load_watermark() -> Image.Image | None:
    global _watermark_cache
    if _watermark_cache is None:
        if not os.path.exists(WATERMARK_PATH):
            return None
        png_bytes = cairosvg.svg2png(url=WATERMARK_PATH)
        _watermark_cache = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return _watermark_cache


def apply_watermark(image_bytes: bytes, opacity: float = 0.45,
                    scale: float = 0.18, padding: int = 18) -> bytes:
    """Composite the Artnuss logo onto the bottom-right corner.
    Returns JPEG bytes. If the watermark file is missing, returns the image unchanged.
    """
    base = _load_watermark()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    if base is not None:
        wm = base.copy()
        w = max(120, int(img.width * scale))
        h = int(wm.height * w / wm.width)
        wm = wm.resize((w, h), Image.LANCZOS)
        r, g, b, a = wm.split()
        a = a.point(lambda x: int(x * opacity))
        wm.putalpha(a)
        img.paste(wm, (img.width - wm.width - padding, img.height - wm.height - padding), wm)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()
