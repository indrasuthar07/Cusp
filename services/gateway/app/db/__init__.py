"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _build_async_url(dsn: str) -> str:
    """Convert a postgresql:// DSN to postgresql+asyncpg://."""
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@lru_cache(maxsize=1)
def _get_engine():
    """Lazily create the async engine (only when first needed)."""
    from app.config.settings import get_settings
    settings = get_settings()
    async_url = _build_async_url(str(settings.database_url))
    return create_async_engine(
        async_url,
        echo=(settings.cusp_env.value == "development"),
        pool_size=10,
        max_overflow=5,
    )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return an async session factory bound to the engine."""
    return async_sessionmaker(
        _get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session