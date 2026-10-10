"""
Redis Streams Worker Consumer.

Consumes events from cusp:v1:events using a consumer group, persists audit trails
to PostgreSQL, handles retries, and forwards poison pills to the dead-letter queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

try:
    from .config import WorkerSettings, get_worker_settings
    from .db import get_worker_db, get_worker_session_factory
    from .events import CuspEvent, CuspEventType
    from .models import AuditEvent
except ImportError:
    try:
        from app.config import WorkerSettings, get_worker_settings
        from app.db import get_worker_db, get_worker_session_factory
        from app.events import CuspEvent, CuspEventType
        from app.models import AuditEvent
    except ImportError:
        from services.worker.app.config import WorkerSettings, get_worker_settings
        from services.worker.app.db import get_worker_db, get_worker_session_factory
        from services.worker.app.events import CuspEvent, CuspEventType
        from services.worker.app.models import AuditEvent

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
            org_id=event.org_id,
            correlation_id=corr_uuid,
            event_type=str(event.event_type),
            payload=event.payload,
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

        # Save to PostgreSQL
        if self._db_factory is not None:
            async with self._db_factory() as session:
                async with session.begin():
                    await self.handle_event(event, session)
        else:
            async with get_worker_db() as session:
                await self.handle_event(event, session)

    async def send_to_dlq(
        self,
        msg_id: str,
        fields: dict[Any, Any],
        error_msg: str,
    ) -> None:
        """Forward a poison-pill message to the dead-letter stream."""
        client = await self.get_redis()
        dlq_entry = {
            "failed_msg_id": msg_id,
            "error": error_msg,
            "retry_count": str(self._retry_counts.get(msg_id, self.settings.max_retries)),
            "raw_fields": json.dumps(
                {
                    (k.decode("utf-8") if isinstance(k, bytes) else str(k)): (
                        v.decode("utf-8") if isinstance(v, bytes) else str(v)
                    )
                    for k, v in fields.items()
                }
            ),
        }
        await client.xadd(self.settings.dead_letter_stream, dlq_entry)
        logger.error(
            "Moved message %s to dead-letter stream '%s' after %d retries: %s",
            msg_id,
            self.settings.dead_letter_stream,
            self.settings.max_retries,
            error_msg,
        )

    async def consume_batch(self) -> int:
        """
        Read and process a single batch of events from the stream.
        Returns the count of messages processed.
        """
        client = await self.get_redis()
        try:
            entries = await client.xreadgroup(
                groupname=self.settings.consumer_group,
                consumername=self.settings.consumer_name,
                streams={self.settings.stream_name: ">"},
                count=self.settings.batch_size,
                block=self.settings.block_time_ms,
            )
        except Exception as exc:
            logger.error("Error reading from Redis Stream: %s", exc)
            return 0

        if not entries:
            return 0

        total_processed = 0
        for stream_name, messages in entries:
            stream_name_str = (
                stream_name.decode("utf-8") if isinstance(stream_name, bytes) else str(stream_name)
            )
            for msg_id_raw, fields in messages:
                msg_id = (
                    msg_id_raw.decode("utf-8") if isinstance(msg_id_raw, bytes) else str(msg_id_raw)
                )
                try:
                    await self.process_message(msg_id, fields)
                    await client.xack(stream_name_str, self.settings.consumer_group, msg_id)
                    self._retry_counts.pop(msg_id, None)
                    total_processed += 1
                except Exception as err:
                    retries = self._retry_counts.get(msg_id, 0) + 1
                    self._retry_counts[msg_id] = retries
                    logger.warning(
                        "Error processing message %s (attempt %d/%d): %s",
                        msg_id,
                        retries,
                        self.settings.max_retries,
                        err,
                    )
                    if retries >= self.settings.max_retries:
                        await self.send_to_dlq(msg_id, fields, str(err))
                        await client.xack(stream_name_str, self.settings.consumer_group, msg_id)
                        self._retry_counts.pop(msg_id, None)

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
            except Exception as exc:
                logger.error("Unexpected error in consumer loop: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

        logger.info("Worker consumer stopped cleanly.")

    def stop(self) -> None:
        """Signal consumer to stop gracefully."""
        self.is_running = False