"""Database engine and per-request sessions."""

from collections.abc import Iterator

from sqlmodel import Session, create_engine

from app.config import settings

# pool_pre_ping drops connections the database closed while the app was idle
# (Render's free instance sleeps between visits).
# Requests, three fetch jobs and three AI workers share the pool.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
