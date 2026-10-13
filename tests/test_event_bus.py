"""
Unit tests for Redis Streams event bus schemas and publisher.

Tests verify:
  1. Envelope serialization & round-trip deserialization
  2. Byte vs string handling in stream entries
  3. JSON payload serialization with UUIDs, dates, and enums
  4. EventPublisher XADD execution with stream capping
  5. Helper publishing methods (session, action, telemetry, audit)
  6. Resilient failure handling (strict vs non-strict)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from app.events.publisher import EventPublisher
from app.events.schemas import (
    EVENTS_STREAM_NAME,
    ActionPreflightPayload,
    AuditLogPayload,
    CuspEvent,
    CuspEventType,
    SessionEventPayload,
    TelemetryPayload,
)


# 1. Envelope serialization & round-trip
def test_cusp_event_round_trip():
    org_id = 42
    session_id = 101
    agent_id = 7
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    event = CuspEvent(
        event_id=event_id,
        event_type=CuspEventType.ACTION_PREFLIGHTED,
        org_id=org_id,
        session_id=session_id,
        agent_id=agent_id,
        correlation_id=correlation_id,
        timestamp=now,
        payload={"action": "tool.invoke", "allowed": True},
    )

    entry = event.to_stream_entry()
    assert entry["event_id"] == event_id
    assert entry["event_type"] == "ACTION_PREFLIGHTED"
    assert entry["org_id"] == "42"
    assert entry["session_id"] == "101"
    assert entry["agent_id"] == "7"
    assert entry["correlation_id"] == correlation_id
    assert json.loads(entry["payload"]) == {"action": "tool.invoke", "allowed": True}

    # Deserialization
    reconstructed = CuspEvent.from_stream_entry(entry)
    assert reconstructed.event_id == event_id
    assert reconstructed.event_type == "ACTION_PREFLIGHTED"
    assert reconstructed.org_id == org_id
    assert reconstructed.session_id == session_id
    assert reconstructed.agent_id == agent_id
    assert reconstructed.correlation_id == correlation_id
    assert reconstructed.payload == {"action": "tool.invoke", "allowed": True}


def test_cusp_event_from_raw_bytes():
    """Verify parsing when Redis client returns dictionary with bytes keys and values."""
    raw_redis_entry = {
        b"event_id": b"ev-12345",
        b"event_type": b"TELEMETRY_RECORDED",
        b"org_id": b"12",
        b"session_id": b"99",
        b"agent_id": b"3",
        b"correlation_id": b"corr-abc-xyz",
        b"timestamp": b"2026-10-12T12:00:00+00:00",
        b"payload": b'{"tokens": 1500, "cost": 300}',
    }

    event = CuspEvent.from_stream_entry(raw_redis_entry)
    assert event.event_id == "ev-12345"
    assert event.event_type == "TELEMETRY_RECORDED"
    assert event.org_id == 12
    assert event.session_id == 99
    assert event.agent_id == 3
    assert event.correlation_id == "corr-abc-xyz"
    assert event.payload == {"tokens": 1500, "cost": 300}


def test_event_serialization_redacts_sensitive_payload_fields():
    event = CuspEvent(
        event_type=CuspEventType.AUDIT_LOG,
        org_id=1,
        payload={
            "prompt": "private prompt",
            "api_key": "secret-key",
            "nested": {"authorization": "Bearer private-token", "safe": "ok"},
        },
    )

    serialized = json.loads(event.to_stream_entry()["payload"])
    assert serialized["prompt"] == "[REDACTED]"
    assert serialized["api_key"] == "[REDACTED]"
    assert serialized["nested"]["authorization"] == "[REDACTED]"
    assert serialized["nested"]["safe"] == "ok"


# 2. Specialized Payloads
def test_specialized_payload_models():
    session_payload = SessionEventPayload(
        session_public_id="sess-001",
        status="running",
        budget_cap_micro_usd=5_000_000,
    )
    assert session_payload.status == "running"
    assert session_payload.budget_cap_micro_usd == 5_000_000

    telemetry_payload = TelemetryPayload(
        tokens_input=100,
        tokens_output=50,
        tokens_total=150,
        provider="ollama",
        model="llama3.2",
        virtual_cost_micro_usd=150,
    )
    assert telemetry_payload.tokens_total == 150

    audit_payload = AuditLogPayload(
        actor_type="user",
        actor_id="usr-123",
        action="policy.created",
        resource_type="policy_set",
        resource_id="pol-456",
    )
    assert audit_payload.action == "policy.created"


# 3. EventPublisher unit tests
@pytest.mark.asyncio
async def test_publisher_xadd_success():
    mock_redis = AsyncMock()
    mock_redis.xadd.return_value = b"1697123456789-0"

    publisher = EventPublisher(redis_client=mock_redis, max_stream_len=50_000)
    event = CuspEvent(
        event_type=CuspEventType.RUN_CREATED,
        org_id=1,
        session_id=10,
        payload={"env": "test"},
    )

    msg_id = await publisher.publish(event)
    assert msg_id == "1697123456789-0"

    mock_redis.xadd.assert_called_once()
    args, kwargs = mock_redis.xadd.call_args
    assert args[0] == EVENTS_STREAM_NAME
    assert kwargs["maxlen"] == 50_000
    assert kwargs["approximate"] is True


@pytest.mark.asyncio
async def test_publisher_helper_methods():
    mock_redis = AsyncMock()
    mock_redis.xadd.return_value = "1697123456789-1"
    publisher = EventPublisher(redis_client=mock_redis)

    # 1. Session event helper
    msg1 = await publisher.publish_session_event(
        event_type=CuspEventType.RUN_CREATED,
        org_id=2,
        session_id=5,
        payload=SessionEventPayload(session_public_id="p-1", status="running"),
    )
    assert msg1 == "1697123456789-1"

    # 2. Action preflight helper
    msg2 = await publisher.publish_action_preflight(
        org_id=2,
        session_id=5,
        agent_id=1,
        payload=ActionPreflightPayload(
            action_public_id="act-1",
            action_type="mcp.query",
            verdict="allow",
        ),
    )
    assert msg2 == "1697123456789-1"

    # 3. Telemetry helper
    msg3 = await publisher.publish_telemetry(
        org_id=2,
        session_id=5,
        agent_id=1,
        payload=TelemetryPayload(tokens_total=500),
    )
    assert msg3 == "1697123456789-1"

    # 4. Audit log helper
    msg4 = await publisher.publish_audit_log(
        org_id=2,
        payload=AuditLogPayload(action="test.action", resource_type="test"),
    )
    assert msg4 == "1697123456789-1"
    assert mock_redis.xadd.call_count == 4


@pytest.mark.asyncio
async def test_publisher_resilience_non_strict():
    mock_redis = AsyncMock()
    mock_redis.xadd.side_effect = ConnectionError("Redis connection lost")

    publisher = EventPublisher(redis_client=mock_redis)
    event = CuspEvent(
        event_type=CuspEventType.AUDIT_LOG,
        org_id=1,
        payload={"note": "failing"},
    )

    # Non-strict must log and return None without raising
    result = await publisher.publish(event, strict=False)
    assert result is None


@pytest.mark.asyncio
async def test_publisher_resilience_strict_raises():
    mock_redis = AsyncMock()
    mock_redis.xadd.side_effect = ConnectionError("Redis down")

    publisher = EventPublisher(redis_client=mock_redis)
    event = CuspEvent(
        event_type=CuspEventType.AUDIT_LOG,
        org_id=1,
        payload={"note": "strict failure"},
    )

    with pytest.raises(ConnectionError):
        await publisher.publish(event, strict=True)
