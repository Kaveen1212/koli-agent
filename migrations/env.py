import logging
import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool, text

from alembic import context

# Load .env so DATABASE_URL is available here just as it is for the app.
load_dotenv()
from app.database import Base
from app.models import room_upload, artwork_embedding, chat_session, generation, index_state

AGENT_TABLES = {"artwork_embeddings", "chat_sessions", "room_uploads", "generations", "index_state"}

def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name not in AGENT_TABLES:
        return False
    return True

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
# The agent owns this schema (DB-6); Prisma's tables stay in `public`. `public`
# remains on the search_path for the pgvector `vector` type.
AGENT_SCHEMA = "agent"
SEARCH_PATH_OPTIONS = f"-csearch_path={AGENT_SCHEMA},public"
# The revision that moved the tables (migrations/versions/a01c5e7d9b21_*.py).
A01_REVISION = "a01c5e7d9b21"

config = context.config

# The DB URL is supplied by the environment, not alembic.ini, so credentials
# stay out of source control.
#
# Required rather than optional: `sqlalchemy.url` in alembic.ini is empty, so
# there is nothing to fall back to. Treating the variable as optional means a
# missing one surfaces as an obscure SQLAlchemy parse error further down instead
# of saying which setting is absent.
#
# '%' is doubled because alembic passes this value through ConfigParser
# interpolation — a password containing one (a URL-encoded '%40', say) otherwise
# blows up with InterpolationSyntaxError.
_database_url = os.getenv("DATABASE_URL")
if not _database_url:
    raise RuntimeError(
        "DATABASE_URL is not set — export it (or add it to .env) before running migrations."
    )
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        version_table_schema=AGENT_SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def _prepare_agent_schema(connection) -> None:
    """Idempotent. Runs before Alembic reads its version table.

    * `agent` must exist before the version table can live in it.
    * pgvector is installed in `public` explicitly: with `agent` first on the
      search_path, a bare CREATE EXTENSION (revision b7c1d2e3f4a5) would
      otherwise install it into `agent` on a fresh database.
    * An existing deployment still has `public.alembic_version`; move it so
      Alembic sees the revision history instead of re-running everything.
    """
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {AGENT_SCHEMA}"))
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"))
    connection.execute(text(f"""
        DO $$
        BEGIN
            IF to_regclass('public.alembic_version') IS NOT NULL
               AND to_regclass('{AGENT_SCHEMA}.alembic_version') IS NULL THEN
                ALTER TABLE public.alembic_version SET SCHEMA {AGENT_SCHEMA};
            END IF;
        END
        $$
    """))
    stray = [r[0] for r in connection.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:t)"
    ), {"t": ["artwork_embeddings", "chat_sessions", "generations", "room_uploads"]})]
    if stray and connection.execute(text(
            f"SELECT to_regclass('{AGENT_SCHEMA}.chat_sessions')")).scalar() is not None:
        logging.getLogger("alembic.env").warning(
            "public.%s exist next to the agent.* tables: a pre-A01 image has run against "
            "this database. The copies in public are not used by the agent and are not "
            "migrated; check they are empty and drop them (Prisma reports them as drift).",
            ", public.".join(stray))
    connection.commit()


def _applied_versions(connection) -> set[str]:
    if connection.execute(text(
            f"SELECT to_regclass('{AGENT_SCHEMA}.alembic_version')")).scalar() is None:
        return set()
    return {r[0] for r in connection.execute(
        text(f"SELECT version_num FROM {AGENT_SCHEMA}.alembic_version"))}


def _includes_a01(versions: set[str]) -> bool:
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(config)
    return any(
        A01_REVISION in {r.revision for r in script.iterate_revisions(v, "base")}
        for v in versions
    )


def _return_version_table_after_downgrade(connection, was_at_a01: bool) -> None:
    """A downgrade past A01 must leave the old image a working database.

    The old image looks for `public.alembic_version`. Alembic can only record the
    downgrade in the table it started with (`agent.alembic_version`), so the
    table is moved back to `public` afterwards.
    """
    if not was_at_a01 or _includes_a01(_applied_versions(connection)):
        return
    connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    connection.execute(text(f"ALTER TABLE {AGENT_SCHEMA}.alembic_version SET SCHEMA public"))
    connection.commit()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"options": SEARCH_PATH_OPTIONS},
    )

    with connectable.connect() as connection:
        _prepare_agent_schema(connection)
        was_at_a01 = _includes_a01(_applied_versions(connection))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            version_table_schema=AGENT_SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()
        connection.commit()

        _return_version_table_after_downgrade(connection, was_at_a01)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
