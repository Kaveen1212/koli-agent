"""The agent must not expose its schema/Swagger UI unless explicitly enabled."""
import importlib
import sys

from fastapi.testclient import TestClient


def _load_app(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    else:
        monkeypatch.setenv("ENABLE_API_DOCS", flag)
    for mod in ("app.main", "app.config"):
        sys.modules.pop(mod, None)
    monkeypatch.setattr("app.services.indexer.start_background_indexer", lambda: None, raising=False)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "start_background_indexer", lambda: None)
    return main.app


def test_docs_disabled_by_default(monkeypatch):
    app = _load_app(monkeypatch, None)
    with TestClient(app) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404, path
        assert c.get("/health").json() == {"status": "ok"}


def test_docs_enabled_with_flag(monkeypatch):
    app = _load_app(monkeypatch, "true")
    with TestClient(app) as c:
        assert c.get("/openapi.json").status_code == 200


def test_backend_url_default_is_not_public(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)
    sys.modules.pop("app.config", None)
    cfg = importlib.import_module("app.config")
    assert "artnuss.com" not in cfg.BACKEND_URL
