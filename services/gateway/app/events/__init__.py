"""
Cusp Gateway - Events package.

Provides Redis Streams publisher and schemas for event sourcing, telemetry,
session lifecycle notifications, and audit logging.
"""

from app.events.publisher import EventPublisher, get_event_publisher
from app.events.schemas import (
    DEAD_LETTER_STREAM_NAME,
    DEFAULT_CONSUMER_GROUP,
    EVENTS_STREAM_NAME,
    ActionPreflightPayload,
    AuditLogPayload,
    CuspEvent,
    CuspEventType,
    SessionEventPayload,
    TelemetryPayload,
)

__all__ = [
    "DEAD_LETTER_STREAM_NAME",
    "DEFAULT_CONSUMER_GROUP",
    "EVENTS_STREAM_NAME",
    "ActionPreflightPayload",
    "AuditLogPayload",
    "CuspEvent",
    "CuspEventType",
    "EventPublisher",
    "SessionEventPayload",
    "TelemetryPayload",
    "get_event_publisher",
]
