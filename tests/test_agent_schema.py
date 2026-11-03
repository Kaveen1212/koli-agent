"""The agent's tables live in the `agent` schema (migration A01 / DB-6)."""
import importlib
import importlib.util
import pathlib

import pytest
from sqlalchemy import create_engine, text

from app import database

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLES = {"artwork_embeddings", "chat_sessions", "generations", "room_uploads"}


def test_engine_puts_agent_first_on_the_search_path():
    # Every connection the app opens (sessions, the indexer) uses this engine.
    # `public` stays on the path for the pgvector `vector` type.
    assert database.SEARCH_PATH_OPTIONS == "-csearch_path=agent,public"


def test_engine_is_created_with_the_search_path_option(monkeypatch):
    seen = {}
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine",
                        lambda url, **kw: seen.update(kw) or create_engine("sqlite://"))
    importlib.reload(database)
    try:
        assert seen["connect_args"] == {"options": "-csearch_path=agent,public"}
    finally:
        monkeypatch.undo()
        importlib.reload(database)


def _load_migration():
    path = next((ROOT / "migrations" / "versions").glob("a01c5e7d9b21_*.py"))
    spec = importlib.util.spec_from_file_location("a01_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_moves_exactly_the_agent_tables_after_the_old_head():
    mod = _load_migration()
    assert mod.down_revision == "b7c1d2e3f4a5"
    assert set(mod.AGENT_TABLES) == TABLES


def test_migration_is_the_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_heads() == ["a01c5e7d9b21"]


def test_emergency_script_covers_every_table_and_restamps_the_version():
    sql = (ROOT / "scripts" / "revert_agent_schema.sql").read_text()
    for table in TABLES | {"alembic_version"}:
        assert f"agent.{table}" in sql
    assert "SET version_num = 'b7c1d2e3f4a5'" in sql


def _live_engine():
    try:
        eng = create_engine(database.DATABASE_URL, connect_args={"connect_timeout": 2})
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        return eng
    except Exception:
        return None


def test_after_alembic_upgrade_the_tables_are_in_agent_and_not_public():
    """Runs in CI (after `alembic upgrade head`); skipped without a database."""
    eng = _live_engine()
    if eng is None:
        pytest.skip("no reachable database")
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE tablename = ANY(:t)"), {"t": sorted(TABLES | {"alembic_version"})}).all()
    if not rows:
        pytest.skip("database not migrated")
    assert {(s, t) for s, t in rows} == {("agent", t) for t in TABLES | {"alembic_version"}}
