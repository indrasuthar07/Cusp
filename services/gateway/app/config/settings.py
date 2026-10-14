"""Validated, secret-safe gateway configuration."""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class CuspEnvironment(StrEnum):
    development = "development"
    test = "test"
    production = "production"


class EnergyTelemetryMode(StrEnum):
    off = "off"
    estimated = "estimated"
    measured = "measured"


class Settings(BaseSettings):
    """Gateway settings with fail-fast cross-field validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    hosted_egress_acknowledgement_version: ClassVar[str] = "v1"

    # Core infrastructure has no unsafe production defaults.
    cusp_env: CuspEnvironment = CuspEnvironment.development
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://cusp:cusp_dev_only@localhost:5432/cusp",
        repr=False,
    )
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0", repr=False)
    cusp_session_secret: SecretStr = Field(min_length=8)
    cusp_token_pepper: SecretStr = Field(min_length=8)
    cusp_log_level: str = "INFO"

    # Dashboard authentication is always OIDC; there is no user-ID header fallback.
    oidc_issuer_url: AnyHttpUrl | None = None
    oidc_audience: str = ""
    oidc_jwks_url: AnyHttpUrl | None = None

    # Provider keys remain masked in model representations.
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    ollama_api_key: SecretStr = SecretStr("")
    ollama_model_profile: str = Field(default="llama3.2", min_length=1, max_length=255)
    gemini_api_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    hf_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")

    # Governance and telemetry.
    cusp_pricing_catalog_path: Path = Path("./docs/pricing.json")
    cusp_energy_telemetry_mode: EnergyTelemetryMode = EnergyTelemetryMode.estimated
    cusp_host_wattage_estimate: int | None = Field(default=150, ge=1)
    cusp_mcp_upstream_allowlist: str = ""
    cusp_mcp_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    cusp_hosted_egress_allowed: bool = False
    cusp_hosted_egress_ack_version: str | None = None

    # Optional observability endpoints.
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    sentry_dsn: AnyHttpUrl | None = None

    @staticmethod
    def _secret_value(secret: SecretStr) -> str:
        return secret.get_secret_value()

    @staticmethod
    def _validate_catalog(path: Path) -> None:
        if not path.is_file():
            raise ValueError(
                "CUSP_PRICING_CATALOG_PATH must point to a readable JSON file"
            )
        try:
            catalog: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "CUSP_PRICING_CATALOG_PATH must contain valid JSON"
            ) from exc
        if not isinstance(catalog, dict) or catalog.get("version") != 1:
            raise ValueError("pricing catalogue must be an object with version 1")
        if not isinstance(catalog.get("profiles"), dict) or not catalog["profiles"]:
            raise ValueError(
                "pricing catalogue must define at least one provider profile"
            )

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> Settings:
        self._validate_catalog(self.cusp_pricing_catalog_path)

        if self.cusp_log_level.upper() not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ValueError(
                "CUSP_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )

        if (
            self.cusp_energy_telemetry_mode is EnergyTelemetryMode.estimated
            and self.cusp_host_wattage_estimate is None
        ):
            raise ValueError(
                "CUSP_HOST_WATTAGE_ESTIMATE is required when energy mode is estimated"
            )
        if self.cusp_energy_telemetry_mode is EnergyTelemetryMode.off:
            self.cusp_host_wattage_estimate = None

        hosted_keys = {
            "GEMINI_API_KEY": self._secret_value(self.gemini_api_key),
            "GROQ_API_KEY": self._secret_value(self.groq_api_key),
            "HF_TOKEN": self._secret_value(self.hf_token),
            "OPENAI_API_KEY": self._secret_value(self.openai_api_key),
            "ANTHROPIC_API_KEY": self._secret_value(self.anthropic_api_key),
        }
        configured_hosted_keys = [name for name, value in hosted_keys.items() if value]
        if configured_hosted_keys and not self.cusp_hosted_egress_allowed:
            raise ValueError(
                "hosted provider credentials require CUSP_HOSTED_EGRESS_ALLOWED=true"
            )
        if self.cusp_hosted_egress_allowed:
            if (
                self.cusp_hosted_egress_ack_version
                != self.hosted_egress_acknowledgement_version
            ):
                raise ValueError(
                    "CUSP_HOSTED_EGRESS_ACK_VERSION must be v1 when hosted egress is enabled"
                )
        elif self.cusp_hosted_egress_ack_version is not None:
            raise ValueError(
                "CUSP_HOSTED_EGRESS_ACK_VERSION requires hosted egress to be enabled"
            )

        if self.cusp_env is CuspEnvironment.production:
            if (
                "database_url" not in self.model_fields_set
                or "redis_url" not in self.model_fields_set
            ):
                raise ValueError(
                    "production gateway requires explicit DATABASE_URL and REDIS_URL"
                )
            if (
                str(self.database_url)
                == "postgresql+asyncpg://cusp:cusp_dev_only@localhost:5432/cusp"
            ):
                raise ValueError(
                    "production DATABASE_URL cannot use the development default"
                )
            if str(self.redis_url) == "redis://localhost:6379/0":
                raise ValueError(
                    "production REDIS_URL cannot use the development default"
                )
            session_secret = self._secret_value(self.cusp_session_secret)
            token_pepper = self._secret_value(self.cusp_token_pepper)
            if len(session_secret) < 32 or len(token_pepper) < 32:
                raise ValueError(
                    "production session secret and token pepper must each be at least 32 characters"
                )
            if session_secret.lower() in {"changeme", "secret", "development"}:
                raise ValueError("CUSP_SESSION_SECRET is a known development value")
            if token_pepper.lower() in {"changeme", "secret", "development"}:
                raise ValueError("CUSP_TOKEN_PEPPER is a known development value")
            if not self.cusp_mcp_upstream_allowlist.strip():
                raise ValueError(
                    "CUSP_MCP_UPSTREAM_ALLOWLIST is required in production"
                )
            if not self.cusp_pricing_catalog_path.is_absolute():
                raise ValueError("production pricing catalogue path must be absolute")
            if (
                self.oidc_issuer_url is None
                or not self.oidc_audience.strip()
                or self.oidc_jwks_url is None
            ):
                raise ValueError(
                    "production dashboard authentication requires OIDC issuer, audience, and JWKS URL"
                )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return validated, cached settings without exposing secret values."""
    return Settings()
