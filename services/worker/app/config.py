"""
Cusp Worker - typed environment configuration.
"""

from __future__ import annotations

import os
import uuid
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerEnvironment(StrEnum):
    development = "development"
    test = "test"
    production = "production"


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Local defaults are intentionally development-only.
    cusp_env: WorkerEnvironment = WorkerEnvironment.development
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://cusp:cusp_dev_only@localhost:5432/cusp",
        repr=False,  # type: ignore[call-overload]
    )
    redis_url: RedisDsn = Field(
        default="redis://localhost:6379/0",
        repr=False,  # type: ignore[call-overload]
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
    claim_idle_ms: int = 30_000
    retry_hash: str = "cusp:v1:events:retries"
    log_level: str = "INFO"
    embedding_stream_name: str = "cusp:v1:embeddings"
    embedding_cache_ttl_seconds: int = Field(default=86_400, ge=1)
    embedding_dimension: int = Field(default=384, ge=8)

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> WorkerSettings:
        if self.batch_size < 1:
            raise ValueError("BATCH_SIZE must be at least 1")
        if self.block_time_ms < 0:
            raise ValueError("BLOCK_TIME_MS cannot be negative")
        if self.max_retries < 1:
            raise ValueError("MAX_RETRIES must be at least 1")
        if self.claim_idle_ms < 0:
            raise ValueError("CLAIM_IDLE_MS cannot be negative")
        if not self.retry_hash.strip():
            raise ValueError("RETRY_HASH cannot be empty")
        if self.log_level.upper() not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ValueError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
        if self.cusp_env is WorkerEnvironment.production and (
            "database_url" not in self.model_fields_set
            or "redis_url" not in self.model_fields_set
        ):
            raise ValueError(
                "production worker requires explicit DATABASE_URL and REDIS_URL"
            )
        return self


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    """Return a cached WorkerSettings instance."""
    return WorkerSettings()
