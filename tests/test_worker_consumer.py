"""
Unit tests for Worker Redis Streams Consumer.

Tests verify:
  1. Consumer group creation and idempotent BUSYGROUP handling
  2. Audit event database persistence
  3. Batch processing and message ACKing
  4. Retry backoff and Dead-Letter Queue (DLQ) routing for poison pills
  5. Clean consumer shutdown
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError

from services.worker.app.config import WorkerSettings
from services.worker.app.consumer import WorkerConsumer
from services.worker.app.events import CuspEvent, CuspEventType
from services.worker.app.models import AuditEvent


# 1. Consumer Group Creation 
@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_stream():
    mock_redis = AsyncMock()
    settings = WorkerSettings(
        stream_name="test:events",
        consumer_group="test-workers",
    )
    consumer = WorkerConsumer(redis_client=mock_redis, settings=settings)

    await consumer.ensure_consumer_group()

    mock_redis.xgroup_create.assert_called_once_with(
        name="test:events",
        groupname="test-workers",
        id="0",
        mkstream=True,
    )


@pytest.mark.asyncio
async def test_ensure_consumer_group_busygroup_handled():
    mock_redis = AsyncMock()
    mock_redis.xgroup_create.side_effect = ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )

    settings = WorkerSettings(
        stream_name="test:events",
        consumer_group="test-workers",
    )
    consumer = WorkerConsumer(redis_client=mock_redis, settings=settings)

    # Should not raise exception
    await consumer.ensure_consumer_group()


# 2. Audit Event Database Persistence 
@pytest.mark.asyncio
async def test_handle_event_persists_to_audit_events():
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    consumer = WorkerConsumer(redis_client=AsyncMock())

    corr_id = str(uuid.uuid4())
    event = CuspEvent(
        event_type=CuspEventType.ACTION_PREFLIGHTED,
        org_id=99,
        correlation_id=corr_id,
        payload={"action": "run_command", "status": "approved"},
    )

    await consumer.handle_event(event, mock_session)

    mock_session.add.assert_called_once()
    saved_record = mock_session.add.call_args[0][0]

    assert isinstance(saved_record, AuditEvent)
    assert saved_record.org_id == 99
    assert str(saved_record.correlation_id) == corr_id
    assert saved_record.event_type == "ACTION_PREFLIGHTED"
    assert saved_record.payload == {"action": "run_command", "status": "approved"}


# 3. Batch Consumption & Message ACKs 
@pytest.mark.asyncio
async def test_consume_batch_success_and_ack():
    mock_redis = AsyncMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_begin_cm = AsyncMock()
    mock_session.begin = MagicMock(return_value=mock_begin_cm)

    mock_factory_cm = AsyncMock()
    mock_factory_cm.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_factory_cm)

    raw_event = CuspEvent(
        event_type=CuspEventType.TELEMETRY_RECORDED,
        org_id=1,
        payload={"tokens_total": 42},
    )

    # Redis returns 1 message in stream
    mock_redis.xreadgroup.return_value = [
        (
            b"cusp:v1:events",
            [(b"1000-0", raw_event.to_stream_entry())],
        )
    ]

    consumer = WorkerConsumer(
        redis_client=mock_redis,
        db_factory=mock_factory,
    )

    processed_count = await consumer.consume_batch()
    assert processed_count == 1

    # Verify message was ACKed
    mock_redis.xack.assert_called_once_with(
        "cusp:v1:events",
        consumer.settings.consumer_group,
        "1000-0",
    )


# 4. Retry Backoff & DLQ on Poison Pills 
@pytest.mark.asyncio
async def test_consume_batch_retries_and_dlq():
    mock_redis = AsyncMock()
    settings = WorkerSettings(max_retries=2)
    consumer = WorkerConsumer(redis_client=mock_redis, settings=settings)

    # Force process_message to fail
    consumer.process_message = AsyncMock(side_effect=RuntimeError("Database down"))

    bad_fields = {"invalid": "data"}
    mock_redis.xreadgroup.return_value = [
        (b"cusp:v1:events", [(b"poison-msg-1", bad_fields)])
    ]

    # Attempt 1 — should NOT ACK and should NOT write to DLQ yet
    processed1 = await consumer.consume_batch()
    assert processed1 == 0
    assert consumer._retry_counts["poison-msg-1"] == 1
    mock_redis.xack.assert_not_called()
    mock_redis.xadd.assert_not_called()

    # Attempt 2 — reaches max_retries (2) -> forwards to DLQ and ACKs original
    processed2 = await consumer.consume_batch()
    assert processed2 == 0
    assert "poison-msg-1" not in consumer._retry_counts

    # DLQ xadd must be called
    mock_redis.xadd.assert_called_once()
    dlq_args, dlq_kwargs = mock_redis.xadd.call_args
    assert dlq_args[0] == settings.dead_letter_stream
    assert dlq_args[1]["failed_msg_id"] == "poison-msg-1"
    assert "invalid" in dlq_args[1]["raw_fields"]

    # Original stream must be ACKed so consumer group doesn't deadlock
    mock_redis.xack.assert_called_once_with(
        "cusp:v1:events",
        settings.consumer_group,
        "poison-msg-1",
    )


# 5. Clean Shutdown 
def test_consumer_stop():
    consumer = WorkerConsumer()
    consumer.is_running = True
    consumer.stop()
    assert consumer.is_running is False

@pytest.mark.asyncio
async def test_pending_messages_are_reclaimed_after_restart():
    mock_redis = AsyncMock()
    settings = WorkerSettings(claim_idle_ms=1234)
    fields = CuspEvent(
        event_type=CuspEventType.AUDIT_LOG,
        org_id=1,
        payload={"action": "replay"},
    ).to_stream_entry()
    mock_redis.xautoclaim.return_value = (b"0-0", [(b"9-0", fields)], [])
    consumer = WorkerConsumer(redis_client=mock_redis, settings=settings)
    consumer.process_message = AsyncMock()

    assert await consumer.reclaim_pending() == 1
    mock_redis.xautoclaim.assert_awaited_once_with(
        settings.stream_name,
        settings.consumer_group,
        settings.consumer_name,
        1234,
        start_id="0-0",
        count=settings.batch_size,
    )
    mock_redis.xack.assert_awaited_once_with(settings.stream_name, settings.consumer_group, "9-0")


@pytest.mark.asyncio
async def test_retry_attempts_survive_consumer_restart():
    mock_redis = AsyncMock()
    mock_redis.hincrby.side_effect = [1, 2]
    settings = WorkerSettings()
    first = WorkerConsumer(redis_client=mock_redis, settings=settings)
    second = WorkerConsumer(redis_client=mock_redis, settings=settings)

    assert await first._retry_attempt("restart-1") == 1
    assert await second._retry_attempt("restart-1") == 2
    assert mock_redis.hincrby.await_count == 2

@pytest.mark.asyncio
async def test_dlq_redacts_payload_and_replay_redacts_again():
    mock_redis = AsyncMock()
    settings = WorkerSettings(max_retries=1)
    consumer = WorkerConsumer(redis_client=mock_redis, settings=settings)
    fields = {
        "event_id": "event-1",
        "payload": json.dumps({"prompt": "do not store", "safe": "ok"}),
    }

    await consumer.send_to_dlq("bad-1", fields, "token=super-secret")
    dlq_entry = mock_redis.xadd.call_args.args[1]
    assert "do not store" not in dlq_entry["raw_fields"]
    assert "super-secret" not in dlq_entry["error"]

    mock_redis.xrange.return_value = [(b"dlq-1", dlq_entry)]
    assert await consumer.replay_dlq(remove=True) == 1
    replayed_fields = mock_redis.xadd.call_args_list[-1].args[1]
    assert replayed_fields["event_id"] == "event-1"
    assert "do not store" not in replayed_fields["payload"]
    mock_redis.xdel.assert_awaited_once_with(settings.dead_letter_stream, b"dlq-1")