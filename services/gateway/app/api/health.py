"""
Health check endpoint for Cusp Gateway.
"""

from __future__ import annotations

import time
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_engine
from app.db.redis import get_redis_client

router = APIRouter(tags=["health"])


class ComponentHealth(BaseModel):
    status: str
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" or "degraded"
    version: str = "0.1.0"
    database: ComponentHealth
    redis: ComponentHealth
    timestamp: float


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Comprehensive infrastructure connectivity check",
)
async def health_check(response: Response) -> HealthResponse:
    # 1. Database connectivity check
    db_status = "ok"
    db_latency = None
    db_error = None
    t0 = time.perf_counter()
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as exc:
        db_status = "down"
        db_error = str(exc)

    # 2. Redis connectivity check
    redis_status = "ok"
    redis_latency = None
    redis_error = None
    t0 = time.perf_counter()
    try:
        client = await get_redis_client()
        pong = await client.ping()
        if not pong:
            redis_status = "down"
            redis_error = "Ping failed"
        else:
            redis_latency = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as exc:
        redis_status = "down"
        redis_error = str(exc)

    is_healthy = db_status == "ok" and redis_status == "ok"
    overall_status = "ok" if is_healthy else "degraded"

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        database=ComponentHealth(
            status=db_status,
            latency_ms=db_latency,
            error=db_error,
        ),
        redis=ComponentHealth(
            status=redis_status,
            latency_ms=redis_latency,
            error=redis_error,
        ),
        timestamp=time.time(),
    )


@router.get(
    "/health/liveness",
    summary="Fast liveness probe for container orchestrators",
)
def liveness():
    """Lightweight probe that returns 200 without external I/O."""
    return {"status": "ok"}