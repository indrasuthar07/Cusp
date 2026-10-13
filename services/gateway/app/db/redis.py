"""Async Redis connection pool."""

from app.config.settings import get_settings
from redis.asyncio import Redis


def get_redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
    )
