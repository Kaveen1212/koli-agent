"""Input-validation guard for the agent's HTTP API.

Every JSON body is a Pydantic model that refuses unknown fields and bounds
every string; uploads are checked by content, not by the client's word.
The first test walks the live route table, so a new endpoint with a loose
model fails here, not in production.
"""
import typing

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app
from app.routers import uploads

TOKEN = {"X-Service-Token": "test-service-token"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _is_str(annotation) -> bool:
    return annotation is str or str in typing.get_args(annotation)


def _api_routes(routes):
    for route in routes:
        # FastAPI >= 0.140 wraps each include_router() in an object holding it.
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _api_routes(included.routes)
        elif hasattr(route, "dependant"):
            yield route


def _body_models():
    for route in _api_routes(app.routes):
        for param in route.dependant.body_params:
            model = param.field_info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel):
                yield route.path, model


def test_every_json_body_forbids_extra_fields_and_bounds_strings():
    models = list(_body_models())
    assert {path for path, _ in models} >= {"/agent/message", "/visualize/wall"}
    for path, model in models:
        assert model.model_config.get("extra") == "forbid", f"{path}: {model.__name__} allows unknown fields"
        for name, field in model.model_fields.items():
            if _is_str(field.annotation):
                assert any(getattr(m, "max_length", None) for m in field.metadata), (
                    f"{path}: {model.__name__}.{name} is an unbounded string"
                )


def test_agent_message_rejects_unknown_fields_and_oversized_text():
    client = TestClient(app)
    ok = {"user_id": "u1", "message": "hi"}
    extra = client.post("/agent/message", headers=TOKEN, json={**ok, "role": "admin"})
    assert extra.status_code == 422
    huge = client.post("/agent/message", headers=TOKEN, json={**ok, "message": "x" * 4001})
    assert huge.status_code == 422


def test_visualize_rejects_unknown_fields(monkeypatch):
    monkeypatch.setattr("app.routers.visualize.check_daily_limits", lambda _uid: None)
    res = TestClient(app).post(
        "/visualize/wall", headers=TOKEN,
        json={"room_image_url": "r", "artwork_image_url": "a", "artwork_id": "1",
              "user_id": "u1", "cost": 0})
    assert res.status_code == 422


def _upload(monkeypatch, content: bytes, filename: str, content_type: str):
    saved = {}

    def fake_save(data, prefix, filename):
        saved["filename"] = filename
        return f"{prefix}/{filename}", f"/uploads/{filename}"

    monkeypatch.setattr(uploads, "save_image", fake_save)
    res = TestClient(app).post(
        "/images/upload", headers=TOKEN,
        files={"file": (filename, content, content_type)})
    return res, saved


def test_upload_names_the_file_by_its_checked_type(monkeypatch):
    res, saved = _upload(monkeypatch, PNG, "evil.html", "image/png")
    assert res.status_code == 200
    assert saved["filename"].endswith(".png")


def test_upload_rejects_content_that_is_not_the_claimed_image(monkeypatch):
    res, saved = _upload(monkeypatch, b"<html><script>alert(1)</script>", "a.png", "image/png")
    assert res.status_code == 400 and saved == {}


def test_upload_rejects_files_over_the_size_cap(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 64)
    res, saved = _upload(monkeypatch, PNG + b"\x00" * 64, "a.png", "image/png")
    assert res.status_code == 413 and saved == {}
