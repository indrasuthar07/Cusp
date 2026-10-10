"""
Cusp Worker - typed environment configuration.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core infra connections
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://cusp:cusp_dev@localhost:5432/cusp"  # type: ignore[assignment]
    )
    redis_url: RedisDsn = Field(
        default="redis://localhost:6379/0"  # type: ignore[assignment]
    )

    # Redis Streams config
    stream_name: str = "cusp:v1:events"
    dead_letter_stream: str = "cusp:v1:events:dead"
    consumer_group: str = "cusp-workers"
    consumer_name: str = Field(
        default_factory=lambda: f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    )

    # Polling & Processing tuning
    batch_size: int = 10
    block_time_ms: int = 2000
    max_retries: int = 3
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return a cached WorkerSettings instance."""
    return WorkerSettings()