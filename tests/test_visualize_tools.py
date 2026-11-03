"""visualize_on_wall / stage_decor with a real catalogue row.

With local storage (production has no S3 keys) a product's images[0] is a
relative "/uploads/..." path. The render step downloads it, so the tools must
hand it over as an address the agent can fetch. Hermetic: no Gemini, no DB.
"""
import pytest

from app.agent.tools import decor, visualize
from app.services import backend_client, image_service, net

SIGNED_IN = {"configurable": {"user_id": "cmuau44mz0000yxqmyjgtup90"}}
ROW = {"images": ["/uploads/products/abc.webp"]}


@pytest.fixture
def renders(monkeypatch):
    calls = []

    def fake_visualize_now(**kwargs):
        calls.append(kwargs)
        return {"preview_url": "https://agent.example/static/generations/x.jpg"}

    monkeypatch.setattr(backend_client, "BACKEND_URL", "http://backend:3000/v1")
    monkeypatch.setattr(net, "BACKEND_URL", "http://backend:3000/v1")
    for mod in (visualize, decor):
        monkeypatch.setattr(mod, "visualize_now", fake_visualize_now)
        monkeypatch.setattr(mod, "get_product_by_id", lambda _id: ROW)
    return calls


@pytest.mark.parametrize("tool,args", [
    (visualize.visualize_on_wall, {"room_image_url": "/uploads/rooms/r.webp", "artwork_id": "a1"}),
    (decor.stage_decor, {"surface_image_url": "/uploads/rooms/s.webp", "decor_item_id": "d1"}),
])
def test_relative_catalogue_image_reaches_the_render_as_a_fetchable_url(
        tool, args, renders, monkeypatch):
    result = tool.invoke(args, config=SIGNED_IN)

    artwork_url = renders[0]["artwork_url"]
    assert artwork_url == "http://backend:3000/uploads/products/abc.webp"
    # The customer still sees the stored path, not the internal address.
    assert result["original_image"] == "/uploads/products/abc.webp"

    # And the render's own download of it goes to the internal backend.
    fetched = []
    monkeypatch.setattr(net, "_download",
                        lambda url, **k: fetched.append(url) or net.FetchedImage(b"x", "image/webp"))
    assert image_service._fetch(artwork_url) == b"x"
    assert fetched == [artwork_url]
