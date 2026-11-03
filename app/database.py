from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pgvector.psycopg2 import register_vector
from app.config import DATABASE_URL

# The agent's tables live in the `agent` schema (migration a01c5e7d9b21); Prisma
# owns `public`. `public` stays on the path for the pgvector `vector` type. Every
# connection the app opens (sessions, the indexer) goes through this engine.
SEARCH_PATH_OPTIONS = "-csearch_path=agent,public"

engine = create_engine(DATABASE_URL, connect_args={"options": SEARCH_PATH_OPTIONS})

@event.listens_for(engine, "connect")
def on_connect(dbapi_connection, connection_record):
    register_vector(dbapi_connection)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
