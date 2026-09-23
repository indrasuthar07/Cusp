"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.gateway.app.config.settings import get_settings


def _build_async_url(dsn: str) -> str:
    """Convert a postgresql:// DSN to postgresql+asyncpg://."""
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


_settings = get_settings()
_async_url = _build_async_url(str(_settings.database_url))

engine = create_async_engine(
    _async_url,
    echo=(_settings.cusp_env.value == "development"),
    pool_size=10,
    max_overflow=5,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency — yields an async session."""
    async with async_session_factory() as session:
        yield session
