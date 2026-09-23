"""Cusp Gateway — typed environment configuration."""

from enum import Enum

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class CuspEnvironment(str, Enum):
    development = "development"
    test = "test"
    production = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core ---
    cusp_env: CuspEnvironment = CuspEnvironment.development
    database_url: PostgresDsn
    redis_url: RedisDsn
    cusp_session_secret: str = Field(min_length=8)
    cusp_token_pepper: str = Field(min_length=8)
    cusp_log_level: str = "info"

    # --- provider keys (all optional — free-first) ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_model_profile: str = "llama3.2"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    hf_token: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # --- governance ---
    cusp_pricing_catalog_path: str = "./docs/pricing.json"
    cusp_energy_telemetry_mode: str = "estimated"
    cusp_host_wattage_estimate: int = 150
    cusp_mcp_upstream_allowlist: str = ""
    cusp_hosted_egress_allowed: bool = False

    # --- observability (optional) ---
    otel_exporter_otlp_endpoint: str = ""
    sentry_dsn: str = ""


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()