"""move the agent's tables out of public into the `agent` schema (A01 / DB-6)

Revision ID: a01c5e7d9b21
Revises: b7c1d2e3f4a5
Create Date: 2026-11-03

The agent shared `public` with Prisma's tables. Prisma treats every table it
does not know as drift, so the agent's four tables (and `alembic_version`) now
live in their own schema. `ALTER TABLE ... SET SCHEMA` is a catalogue-only move:
rows, indexes, constraints and owned sequences move with the table and nothing
is rewritten.

* `alembic_version` is moved by migrations/env.py (it must be in place before
  Alembic reads it, and moved back after a downgrade past this revision).
* Fresh databases never have the tables in `public`: env.py puts `agent` first
  on the search_path, so the older revisions (unqualified names) create them
  in `agent` directly, and the moves below are no-ops.
* If `agent.<t>` already exists next to `public.<t>` (the old image was started
  against a migrated database and recreated empty tables), SET SCHEMA fails
  loudly instead of overwriting data. Resolve by hand; see
  scripts/revert_agent_schema.sql.
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'a01c5e7d9b21'
down_revision: Union[str, Sequence[str], None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_TABLES = ("artwork_embeddings", "chat_sessions", "generations", "room_uploads")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
    for table in AGENT_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS public.{table} SET SCHEMA agent")


def downgrade() -> None:
    for table in AGENT_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS agent.{table} SET SCHEMA public")
    # agent.alembic_version is moved back to public by migrations/env.py once
    # Alembic has finished recording this downgrade. The `agent` schema is left
    # in place (empty) so a re-upgrade is idempotent.
