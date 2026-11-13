from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine_kwargs = {"connect_args": connect_args}
if not settings.database_url.startswith("sqlite"):
    engine_kwargs.update(pool_size=30, max_overflow=30, pool_timeout=30.0)

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
