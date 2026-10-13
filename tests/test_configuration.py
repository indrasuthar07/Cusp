"""Configuration validation and secret-safety tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.config.settings import Settings
from pydantic import SecretStr, ValidationError

from services.worker.app.config import WorkerSettings


def catalog_path(tmp_path: Path) -> Path:
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps({"version": 1, "profiles": {"fake": {}}}), encoding="utf-8"
    )
    return path


def base_settings(tmp_path: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cusp_env": "development",
        "database_url": "postgresql+asyncpg://cusp:password@localhost:5432/cusp",
        "redis_url": "redis://localhost:6379/0",
        "cusp_session_secret": "development-session-secret",
        "cusp_token_pepper": "development-token-pepper",
        "cusp_pricing_catalog_path": catalog_path(tmp_path),
    }
    values.update(overrides)
    return values


def test_development_configuration_accepts_local_defaults(tmp_path: Path) -> None:
    settings = Settings(**base_settings(tmp_path))
    assert settings.cusp_env.value == "development"
    assert settings.gemini_api_key.get_secret_value() == ""
    assert "development-session-secret" not in repr(settings)


def test_production_requires_strong_secrets_and_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(
            **base_settings(
                tmp_path,
                cusp_env="production",
                database_url="postgresql+asyncpg://cusp:password@postgres:5432/cusp",
                redis_url="redis://redis:6379/0",
            )
        )

    settings = Settings(
        **base_settings(
            tmp_path,
            cusp_env="production",
            cusp_session_secret="s" * 40,
            cusp_token_pepper="p" * 40,
            database_url="postgresql+asyncpg://cusp:password@postgres:5432/cusp",
            redis_url="redis://redis:6379/0",
            cusp_mcp_upstream_allowlist="filesystem",
            cusp_pricing_catalog_path=catalog_path(tmp_path).absolute(),
            oidc_issuer_url="https://issuer.example",
            oidc_audience="cusp-dashboard",
            oidc_jwks_url="https://issuer.example/.well-known/jwks.json",
        )
    )
    assert settings.cusp_env.value == "production"


def test_invalid_pricing_catalog_and_energy_mode_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="CUSP_PRICING_CATALOG_PATH"):
        Settings(
            **base_settings(
                tmp_path, cusp_pricing_catalog_path=tmp_path / "missing.json"
            )
        )

    with pytest.raises(ValidationError, match="estimated"):
        Settings(**base_settings(tmp_path, cusp_energy_telemetry_mode="invalid"))

    with pytest.raises(ValidationError, match="WATTAGE"):
        Settings(**base_settings(tmp_path, cusp_host_wattage_estimate=None))


def test_hosted_credentials_require_explicit_consent(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="hosted provider credentials"):
        Settings(**base_settings(tmp_path, gemini_api_key="not-a-real-key"))

    with pytest.raises(ValidationError, match="ACK_VERSION"):
        Settings(
            **base_settings(
                tmp_path,
                gemini_api_key="not-a-real-key",
                cusp_hosted_egress_allowed=True,
            )
        )

    settings = Settings(
        **base_settings(
            tmp_path,
            gemini_api_key="not-a-real-key",
            cusp_hosted_egress_allowed=True,
            cusp_hosted_egress_ack_version="v1",
        )
    )
    assert settings.gemini_api_key == SecretStr("not-a-real-key")
    assert "not-a-real-key" not in repr(settings)


def test_worker_production_requires_explicit_infrastructure() -> None:
    with pytest.raises(ValidationError, match="explicit DATABASE_URL"):
        WorkerSettings(cusp_env="production", _env_file=None)

    settings = WorkerSettings(
        cusp_env="production",
        database_url="postgresql+asyncpg://worker:password@postgres:5432/cusp",
        redis_url="redis://redis:6379/0",
    )
    assert settings.cusp_env.value == "production"
