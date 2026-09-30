import os

# App modules build their clients at import time (SQLAlchemy engine, Gemini
# clients). Placeholders let the unit tests import them without a database or
# key; the integration tests still pick up real values from the environment.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-service-token")
