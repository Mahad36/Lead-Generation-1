from collections.abc import Generator
from urllib.parse import unquote

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
database_url = make_url(settings.database_url)
if database_url.password and "%" in database_url.password:
    database_url = database_url.set(password=unquote(database_url.password))
# Supabase session pooler allows ~15 clients. A large SQLAlchemy pool plus
# leftover connections from API restarts exceeds that and every request 500s.
engine = create_engine(
    database_url,
    poolclass=NullPool,
    pool_pre_ping=True,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session() -> Session:
    """Standalone session for Celery workers."""
    return SessionLocal()
