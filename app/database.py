from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pgvector.psycopg2 import register_vector
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL)

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
