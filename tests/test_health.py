"""
Unit tests for infrastructure health check endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.health import health_check, liveness
from starlette.responses import Response


def test_liveness():
    res = liveness()
    assert res == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_check_all_healthy():
    mock_resp = Response()
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True

    with (
        patch("app.api.health.get_engine", return_value=mock_engine),
        patch("app.api.health.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
    ):
        result = await health_check(mock_resp)

        assert result.status == "ok"
        assert result.database.status == "ok"
        assert result.database.latency_ms is not None
        assert result.redis.status == "ok"
        assert result.redis.latency_ms is not None
        assert mock_resp.status_code == 200


@pytest.mark.asyncio
async def test_health_check_db_failure():
    mock_resp = Response()
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError("Postgres offline")

    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True

    with (
        patch("app.api.health.get_engine", return_value=mock_engine),
        patch("app.api.health.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
    ):
        result = await health_check(mock_resp)

        assert result.status == "degraded"
        assert result.database.status == "down"
        assert result.database.error == "database_unavailable"
        assert "Postgres offline" not in str(result.database.error)
        assert result.redis.status == "ok"
        assert mock_resp.status_code == 503


@pytest.mark.asyncio
async def test_health_check_redis_failure():
    mock_resp = Response()
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    mock_redis = AsyncMock()
    mock_redis.ping.side_effect = ConnectionError("Redis unreachable")

    with (
        patch("app.api.health.get_engine", return_value=mock_engine),
        patch("app.api.health.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
    ):
        result = await health_check(mock_resp)

        assert result.status == "degraded"
        assert result.database.status == "ok"
        assert result.redis.status == "down"
        assert result.redis.error == "redis_unavailable"
        assert "Redis unreachable" not in str(result.redis.error)
        assert mock_resp.status_code == 503


@pytest.mark.asyncio
async def test_health_check_does_not_expose_dependency_exception(caplog):
    mock_resp = Response()
    mock_engine = MagicMock()
    secret = "postgres-password-that-must-not-leak"
    mock_engine.connect.side_effect = RuntimeError(secret)
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True

    with (
        patch("app.api.health.get_engine", return_value=mock_engine),
        patch("app.api.health.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
    ):
        result = await health_check(mock_resp)

    assert secret not in result.model_dump_json()
    assert secret not in caplog.text