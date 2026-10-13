"""
Cusp Worker — Async SQLAlchemy engine and session factory.

Uses lazy initialization so module imports do not trigger connection
attempts during test collection or static analysis.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

try:
    from .config import get_worker_settings
except ImportError:
    try:
        from app.config import get_worker_settings
    except ImportError:
        from services.worker.app.config import get_worker_settings


@lru_cache(maxsize=1)
def get_worker_engine() -> AsyncEngine:
    """Return a cached async SQLAlchemy engine."""
    settings = get_worker_settings()
    return create_async_engine(
        str(settings.database_url),
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache(maxsize=1)
def get_worker_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a cached async session factory bound to the worker engine."""
    return async_sessionmaker(
        bind=get_worker_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


@asynccontextmanager
async def get_worker_db() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager providing a database session for worker tasks."""
    factory = get_worker_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
