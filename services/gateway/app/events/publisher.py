"""
Redis Streams event publisher for the Cusp Gateway.

Publishes structured governance, telemetry, and audit events to the
cusp:v1:events Redis Stream with stream capping to prevent unbounded memory usage.
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as aioredis
from app.db.redis import get_redis_client
from app.events.schemas import (
    EVENTS_STREAM_NAME,
    ActionPreflightPayload,
    AuditLogPayload,
    CuspEvent,
    CuspEventType,
    SessionEventPayload,
    TelemetryPayload,
)

logger = logging.getLogger(__name__)


class EventPublisher:
    """Async publisher that appends structured events to Redis Streams."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        stream_name: str = EVENTS_STREAM_NAME,
        max_stream_len: int = 100_000,
    ) -> None:
        self._redis = redis_client
        self._stream_name = stream_name
        self._max_stream_len = max_stream_len

    async def _get_client(self) -> aioredis.Redis:
        """Resolve Redis client instance."""
        if self._redis is not None:
            return self._redis
        return await get_redis_client()

    async def publish(
        self,
        event: CuspEvent,
        stream_name: str | None = None,
        strict: bool = False,
    ) -> str | None:
        """
        Publish an event envelope to Redis Stream via XADD.

        Args:
            event: The validated CuspEvent object.
            stream_name: Target stream override (default: cusp:v1:events).
            strict: If True, raise exception on failure; if False, log and return None.

        Returns:
            The Redis stream message ID (e.g. '1697123456789-0') or None on failure.
        """
        target_stream = stream_name or self._stream_name
        entry = event.to_stream_entry()

        try:
            client = await self._get_client()
            msg_id = await client.xadd(
                target_stream,
                entry,
                maxlen=self._max_stream_len,
                approximate=True,
            )
            msg_id_str = (
                msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
            )
            logger.debug(
                "Published event %s [%s] to %s (msg_id=%s)",
                event.event_id,
                event.event_type,
                target_stream,
                msg_id_str,
            )
            return msg_id_str
        except Exception:
            logger.exception(
                "Failed to publish event %s [%s] to stream %s",
                event.event_id,
                event.event_type,
                target_stream,
            )
            if strict:
                raise
            return None

    # Convenience helper methods

    async def publish_session_event(
        self,
        event_type: CuspEventType,
        org_id: int,
        session_id: int | None,
        payload: SessionEventPayload,
        agent_id: int | None = None,
        correlation_id: str | None = None,
    ) -> str | None:
        """Publish a run/session lifecycle event."""
        event = CuspEvent(
            event_type=event_type,
            org_id=org_id,
            session_id=session_id,
            agent_id=agent_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            payload=payload.model_dump(),
        )
        return await self.publish(event)

    async def publish_action_preflight(
        self,
        org_id: int,
        session_id: int | None,
        agent_id: int | None,
        payload: ActionPreflightPayload,
        correlation_id: str | None = None,
    ) -> str | None:
        """Publish an action preflight governance event."""
        event = CuspEvent(
            event_type=CuspEventType.ACTION_PREFLIGHTED,
            org_id=org_id,
            session_id=session_id,
            agent_id=agent_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            payload=payload.model_dump(),
        )
        return await self.publish(event)

    async def publish_telemetry(
        self,
        org_id: int,
        session_id: int | None,
        agent_id: int | None,
        payload: TelemetryPayload,
        correlation_id: str | None = None,
    ) -> str | None:
        """Publish token, cost, and energy telemetry metrics."""
        event = CuspEvent(
            event_type=CuspEventType.TELEMETRY_RECORDED,
            org_id=org_id,
            session_id=session_id,
            agent_id=agent_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            payload=payload.model_dump(),
        )
        return await self.publish(event)

    async def publish_audit_log(
        self,
        org_id: int,
        payload: AuditLogPayload,
        session_id: int | None = None,
        agent_id: int | None = None,
        correlation_id: str | None = None,
    ) -> str | None:
        """Publish an administrative audit event."""
        event = CuspEvent(
            event_type=CuspEventType.AUDIT_LOG,
            org_id=org_id,
            session_id=session_id,
            agent_id=agent_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            payload=payload.model_dump(),
        )
        return await self.publish(event)


# Global publisher singleton
_event_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Return the global event publisher instance."""
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = EventPublisher()
    return _event_publisher
