-- Emergency reverse of migration a01c5e7d9b21 (DB-6): put the agent's four
-- tables and its alembic_version table back into `public`, exactly where the
-- pre-A01 image expects them.
--
-- A04 (a04d7b3e9c52: feed columns and agent.index_state) may be applied too: its
-- additions are inert for older images and its upgrade is idempotent, so the
-- script just re-stamps past it. `agent.index_state` stays in the `agent` schema.
--
-- Prefer `alembic downgrade b7c1d2e3f4a5` (same effect, run from the new image). Use this
-- script when the new image cannot start. Run it with the agent STOPPED:
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/revert_agent_schema.sql
--
-- Then deploy the pre-A01 image. Idempotent: tables that are already in public
-- are skipped. It never drops or overwrites anything: if the same table exists
-- in BOTH schemas (the old image was started against a migrated database and
-- created empty tables), SET SCHEMA fails and the transaction rolls back. Check
-- the row counts of both copies and drop the empty one by hand, then re-run.
--
-- The `agent` schema itself is left in place (empty) so a re-upgrade works.
BEGIN;

ALTER TABLE IF EXISTS agent.artwork_embeddings SET SCHEMA public;
ALTER TABLE IF EXISTS agent.chat_sessions      SET SCHEMA public;
ALTER TABLE IF EXISTS agent.generations        SET SCHEMA public;
ALTER TABLE IF EXISTS agent.room_uploads       SET SCHEMA public;
ALTER TABLE IF EXISTS agent.alembic_version    SET SCHEMA public;

-- The pre-A01 image only knows revisions up to b7c1d2e3f4a5. Re-stamp, or it
-- stops with "Can't locate revision identified by 'a01c5e7d9b21'", and a later
-- `alembic upgrade head` would think A01 is applied and skip the move.
UPDATE public.alembic_version
SET version_num = 'b7c1d2e3f4a5'
WHERE version_num IN ('a01c5e7d9b21', 'a04d7b3e9c52');

COMMIT;

-- Expected: five rows, all in public.
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_name IN ('artwork_embeddings', 'chat_sessions', 'generations',
                     'room_uploads', 'alembic_version')
ORDER BY table_schema, table_name;
