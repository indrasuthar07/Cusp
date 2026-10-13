"""
Redis Streams Worker Consumer.

Consumes events from cusp:v1:events using a consumer group, persists audit trails
to PostgreSQL, handles retries, and forwards poison pills to the dead-letter queue.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

try:
    from .config import WorkerSettings, get_worker_settings
    from .db import get_worker_db
    from .events import CuspEvent, CuspEventType
    from .models import AuditEvent
    from .redaction import redact_payload, redact_stream_fields, redact_text
except ImportError:
    try:
        from app.config import WorkerSettings, get_worker_settings
        from app.db import get_worker_db
        from app.events import CuspEvent, CuspEventType
        from app.models import AuditEvent
        from app.redaction import redact_payload, redact_stream_fields, redact_text
    except ImportError:
        from services.worker.app.config import WorkerSettings, get_worker_settings
        from services.worker.app.db import get_worker_db
        from services.worker.app.events import CuspEvent, CuspEventType
        from services.worker.app.models import AuditEvent
        from services.worker.app.redaction import (
            redact_payload,
            redact_stream_fields,
            redact_text,
        )

logger = logging.getLogger(__name__)


class WorkerConsumer:
    """Long-running consumer group reader for Redis Streams event bus."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
        settings: WorkerSettings | None = None,
    ) -> None:
        self.settings = settings or get_worker_settings()
        self._redis = redis_client
        self._db_factory = db_factory
        self.is_running = False
        self._retry_counts: dict[str, int] = {}
        self._claim_cursor = "0-0"

    async def get_redis(self) -> aioredis.Redis:
        """Resolve Redis client."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                str(self.settings.redis_url),
                decode_responses=False,
            )
        return self._redis

    async def ensure_consumer_group(self) -> None:
        """Create stream and consumer group if they don't already exist."""
        client = await self.get_redis()
        try:
            await client.xgroup_create(
                name=self.settings.stream_name,
                groupname=self.settings.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created consumer group '%s' on stream '%s'",
                self.settings.consumer_group,
                self.settings.stream_name,
            )
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug(
                    "Consumer group '%s' already exists on stream '%s'",
                    self.settings.consumer_group,
                    self.settings.stream_name,
                )
            else:
                raise

    async def handle_event(self, event: CuspEvent, session: AsyncSession) -> None:
        """Process an individual domain event within a database session."""
        logger.info(
            "Processing event %s [%s] (org_id=%s, correlation=%s)",
            event.event_id,
            event.event_type,
            event.org_id,
            event.correlation_id,
        )

        # Parse correlation UUID safely
        try:
            corr_uuid = uuid.UUID(event.correlation_id)
        except (ValueError, TypeError):
            corr_uuid = uuid.uuid4()

        # 1. Always append to audit_events table
        audit_record = AuditEvent(
            event_id=event.event_id,
            org_id=event.org_id,
            correlation_id=corr_uuid,
            event_type=str(event.event_type),
            payload=redact_payload(event.payload),
            created_at=event.timestamp,
        )
        session.add(audit_record)

        # 2. Specific handler dispatches
        if event.event_type == CuspEventType.TELEMETRY_RECORDED:
            tokens_total = event.payload.get("tokens_total", 0)
            cost = event.payload.get("virtual_cost_micro_usd", 0)
            logger.info(
                "Telemetry recorded: tokens=%d, virtual_cost=%d micro-USD for org %d",
                tokens_total,
                cost,
                event.org_id,
            )
        elif event.event_type in (
            CuspEventType.RUN_CREATED,
            CuspEventType.RUN_PAUSED,
            CuspEventType.RUN_TERMINATED,
        ):
            logger.info(
                "Session lifecycle transition: %s for session %s",
                event.event_type,
                event.session_id,
            )

    async def process_message(self, msg_id: str, fields: dict[Any, Any]) -> None:
        """Parse raw Redis entry and execute database persistence."""
        event = CuspEvent.from_stream_entry(fields)
        if not any(
            (key.decode("utf-8") if isinstance(key, bytes) else str(key)) == "event_id"
            for key in fields
        ):
            # Malformed/legacy entries still get a stable ledger identity on replay.
            event.event_id = f"stream:{msg_id}"

        # Save to PostgreSQL
        if self._db_factory is not None:
            async with self._db_factory() as session, session.begin():
                await self._persist_idempotently(event, session)
        else:
            async with get_worker_db() as session:
                await self._persist_idempotently(event, session)

    async def _persist_idempotently(
        self, event: CuspEvent, session: AsyncSession
    ) -> None:
        """Flush inside a savepoint so duplicate event IDs are harmless."""
        nested = session.begin_nested()
        if inspect.isawaitable(nested):
            nested = await nested
        try:
            if hasattr(nested, "__aenter__"):
                async with nested:
                    await self.handle_event(event, session)
                    await session.flush()
            else:
                # Lightweight test/session doubles may not implement savepoints.
                await self.handle_event(event, session)
                await session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(constraint, "constraint_name", "")
            if (
                constraint_name != "uq_audit_events_event_id"
                and "uq_audit_events_event_id" not in str(exc)
            ):
                raise
            logger.info(
                "Audit event %s already persisted; acknowledging duplicate",
                event.event_id,
            )

    async def send_to_dlq(
        self,
        msg_id: str,
        fields: dict[Any, Any],
        error_msg: str,
    ) -> None:
        """Forward a poison-pill message to the dead-letter stream."""
        client = await self.get_redis()
        safe_fields = redact_stream_fields(fields)
        dlq_entry = {
            "failed_msg_id": msg_id,
            "error": redact_text(error_msg),
            "retry_count": str(
                self._retry_counts.get(msg_id, self.settings.max_retries)
            ),
            "raw_fields": json.dumps(safe_fields),
        }
        await client.xadd(self.settings.dead_letter_stream, dlq_entry)
        logger.error(
            "Moved message %s to dead-letter stream '%s' after %d retries: %s",
            msg_id,
            self.settings.dead_letter_stream,
            self.settings.max_retries,
            redact_text(error_msg),
        )

    async def _retry_attempt(self, msg_id: str) -> int:
        """Increment durable retry metadata, falling back safely for test doubles."""
        client = await self.get_redis()
        try:
            attempt = await client.hincrby(self.settings.retry_hash, msg_id, 1)
            if isinstance(attempt, int):
                self._retry_counts[msg_id] = attempt
                return attempt
        except Exception:
            logger.debug(
                "Unable to persist retry metadata for %s", msg_id, exc_info=True
            )
        attempt = self._retry_counts.get(msg_id, 0) + 1
        self._retry_counts[msg_id] = attempt
        return attempt

    async def _clear_retry_attempt(self, msg_id: str) -> None:
        client = await self.get_redis()
        try:
            await client.hdel(self.settings.retry_hash, msg_id)
        except Exception:
            logger.debug("Unable to clear retry metadata for %s", msg_id, exc_info=True)
        self._retry_counts.pop(msg_id, None)

    async def _process_entries(self, stream_name: str, messages: list[Any]) -> int:
        client = await self.get_redis()
        total_processed = 0
        for msg_id_raw, fields in messages:
            msg_id = (
                msg_id_raw.decode("utf-8")
                if isinstance(msg_id_raw, bytes)
                else str(msg_id_raw)
            )
            try:
                await self.process_message(msg_id, fields)
                await client.xack(stream_name, self.settings.consumer_group, msg_id)
                await self._clear_retry_attempt(msg_id)
                total_processed += 1
            except Exception as err:  # noqa: BLE001 - failed handlers are retried/DLQ'd
                retries = await self._retry_attempt(msg_id)
                logger.warning(
                    "Error processing message %s (attempt %d/%d): %s",
                    msg_id,
                    retries,
                    self.settings.max_retries,
                    redact_text(str(err)),
                )
                if retries >= self.settings.max_retries:
                    await self.send_to_dlq(msg_id, fields, str(err))
                    await client.xack(stream_name, self.settings.consumer_group, msg_id)
                    await self._clear_retry_attempt(msg_id)
        return total_processed

    async def reclaim_pending(self) -> int:
        """Reclaim idle pending entries so a restarted worker can finish them."""
        client = await self.get_redis()
        try:
            result = await client.xautoclaim(
                self.settings.stream_name,
                self.settings.consumer_group,
                self.settings.consumer_name,
                self.settings.claim_idle_ms,
                start_id=self._claim_cursor,
                count=self.settings.batch_size,
            )
            if not isinstance(result, (tuple, list)) or len(result) < 2:
                return 0
            self._claim_cursor = (
                result[0].decode("utf-8")
                if isinstance(result[0], bytes)
                else str(result[0])
            )
            messages = result[1] or []
            return await self._process_entries(self.settings.stream_name, messages)
        except (AttributeError, TypeError, ValueError, ResponseError) as exc:
            logger.debug("Pending reclaim unavailable: %s", redact_text(str(exc)))
            return 0

    async def replay_dlq(self, *, limit: int = 100, remove: bool = False) -> int:
        """Replay redacted DLQ entries to the primary stream exactly once per call."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        client = await self.get_redis()
        entries = await client.xrange(self.settings.dead_letter_stream, count=limit)
        replayed = 0
        for dlq_id_raw, fields in entries:
            decoded = redact_stream_fields(fields)
            raw_fields = decoded.get("raw_fields", "{}")
            try:
                event_fields = redact_stream_fields(json.loads(raw_fields))
            except (TypeError, ValueError):
                continue
            await client.xadd(self.settings.stream_name, event_fields)
            if remove:
                await client.xdel(self.settings.dead_letter_stream, dlq_id_raw)
            replayed += 1
        return replayed

    async def consume_batch(self) -> int:
        """
        Read and process a single batch of events from the stream.
        Returns the count of messages processed.
        """
        client = await self.get_redis()
        total_processed = await self.reclaim_pending()
        try:
            entries = await client.xreadgroup(
                groupname=self.settings.consumer_group,
                consumername=self.settings.consumer_name,
                streams={self.settings.stream_name: ">"},
                count=self.settings.batch_size,
                block=self.settings.block_time_ms,
            )
        except Exception as exc:  # noqa: BLE001 - keep polling after Redis failures
            logger.error("Error reading from Redis Stream: %s", exc)
            return total_processed

        if not entries:
            return total_processed

        for stream_name, messages in entries:
            stream_name_str = (
                stream_name.decode("utf-8")
                if isinstance(stream_name, bytes)
                else str(stream_name)
            )
            total_processed += await self._process_entries(stream_name_str, messages)

        return total_processed

    async def run(self) -> None:
        """Main consumer loop."""
        self.is_running = True
        logger.info(
            "Worker consumer started (group='%s', name='%s', stream='%s')",
            self.settings.consumer_group,
            self.settings.consumer_name,
            self.settings.stream_name,
        )
        await self.ensure_consumer_group()

        while self.is_running:
            try:
                processed = await self.consume_batch()
                if processed == 0:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in consumer loop")
                await asyncio.sleep(1.0)

        logger.info("Worker consumer stopped cleanly.")

    def stop(self) -> None:
        """Signal consumer to stop gracefully."""
        self.is_running = False
