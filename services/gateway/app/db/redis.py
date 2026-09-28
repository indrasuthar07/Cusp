"""Async Redis connection pool."""
from redis.asyncio import Redis
from app.config.settings import get_settings


def get_redis_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
    )