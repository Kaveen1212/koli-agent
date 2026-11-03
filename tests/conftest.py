import os

import pytest

# App modules build their clients at import time (SQLAlchemy engine, Gemini
# clients). Placeholders let the unit tests import them without a database or
# key; the integration tests still pick up real values from the environment.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-service-token")


def _has_real_gemini_key() -> bool:
    # conftest sets the "test-key" placeholder above, so an unset variable and
    # the placeholder both mean "no real key".
    key = os.environ.get("GEMINI_API_KEY", "")
    return bool(key) and key != "test-key"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_gemini: calls the live Gemini API; skipped unless GEMINI_API_KEY is a real key",
    )


def pytest_collection_modifyitems(config, items):
    if _has_real_gemini_key():
        return
    skip = pytest.mark.skip(reason="needs a real GEMINI_API_KEY (live Gemini call); none set")
    for item in items:
        if "needs_gemini" in item.keywords:
            item.add_marker(skip)
