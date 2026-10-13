"""
Event schemas and contracts for Redis Streams event bus.

Stream contracts:
  - Primary stream: cusp:v1:events
  - Dead letter stream: cusp:v1:events:dead
  - Default consumer group: cusp-workers

Payloads are strictly structured and redacted — raw prompts, tool outputs,
or sensitive API keys must NEVER be placed in event payloads.
"""

from __future__ import annotations

import enum
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

EVENTS_STREAM_NAME: str = "cusp:v1:events"
DEAD_LETTER_STREAM_NAME: str = "cusp:v1:events:dead"
DEFAULT_CONSUMER_GROUP: str = "cusp-workers"
REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|api[_-]?key|authorization|credential|private[_-]?key|"
    r"prompt|tool[_-]?output|source[_-]?code|raw[_-]?input|cookie)",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    """Remove common credential/token forms from diagnostic text."""
    return re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+",
        r"\1" + REDACTED_VALUE,
        value,
    )


def redact_payload(value: Any, *, key: str = "") -> Any:
    """Recursively redact sensitive event fields before serialization."""
    if _SENSITIVE_KEY.search(key):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(child_key): redact_payload(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [redact_payload(child, key=key) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class CuspEventType(str, enum.Enum):
    """Categorized event types supported on the event bus."""
    # Session / Run lifecycle
    RUN_CREATED = "RUN_CREATED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_TERMINATED = "RUN_TERMINATED"
    RUN_COMPLETED = "RUN_COMPLETED"

    # Action preflight and execution
    ACTION_PREFLIGHTED = "ACTION_PREFLIGHTED"
    ACTION_EXECUTED = "ACTION_EXECUTED"

    # Telemetry streaming
    TELEMETRY_RECORDED = "TELEMETRY_RECORDED"

    # Audit logging & governance
    AUDIT_LOG = "AUDIT_LOG"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    AUDIT_BACKLOG_WARNING = "AUDIT_BACKLOG_WARNING"


class CuspEvent(BaseModel):
    """Universal envelope for all events traversing Redis Streams."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: CuspEventType | str
    org_id: int
    session_id: int | None = None
    agent_id: int | None = None
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_stream_entry(self) -> dict[str, str]:
        """Convert event envelope to Redis XADD string dictionary."""
        import json

        def _json_serial(obj):
            if isinstance(obj, (datetime,)):
                return obj.isoformat()
            if isinstance(obj, uuid.UUID):
                return str(obj)
            if isinstance(obj, enum.Enum):
                return obj.value
            raise TypeError(f"Type {type(obj)} not serializable")

        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value if isinstance(self.event_type, enum.Enum) else str(self.event_type),
            "org_id": str(self.org_id),
            "session_id": str(self.session_id) if self.session_id is not None else "",
            "agent_id": str(self.agent_id) if self.agent_id is not None else "",
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": json.dumps(redact_payload(self.payload), default=_json_serial),
        }

    @classmethod
    def from_stream_entry(cls, entry: dict[str, Any]) -> CuspEvent:
        """Parse Redis Stream raw entry back into typed CuspEvent."""
        import json

        # Handle byte keys and values if redis client returned raw bytes
        decoded: dict[str, str] = {}
        for k, v in entry.items():
            key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            val_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            decoded[key_str] = val_str

        payload_raw = decoded.get("payload", "{}")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            payload = {"raw": payload_raw}

        session_id_raw = decoded.get("session_id", "").strip()
        agent_id_raw = decoded.get("agent_id", "").strip()

        ts_raw = decoded.get("timestamp")
        if ts_raw:
            try:
                timestamp = datetime.fromisoformat(ts_raw)
            except ValueError:
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        return cls(
            event_id=decoded.get("event_id", str(uuid.uuid4())),
            event_type=decoded.get("event_type", "UNKNOWN"),
            org_id=int(decoded.get("org_id", 0)),
            session_id=int(session_id_raw) if session_id_raw else None,
            agent_id=int(agent_id_raw) if agent_id_raw else None,
            correlation_id=decoded.get("correlation_id", str(uuid.uuid4())),
            timestamp=timestamp,
            payload=payload,
        )


# Typed Payload Models

class SessionEventPayload(BaseModel):
    """Payload for RUN_CREATED / RUN_PAUSED / RUN_TERMINATED events."""
    session_public_id: str
    status: str
    budget_cap_micro_usd: int = 0
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionPreflightPayload(BaseModel):
    """Payload for ACTION_PREFLIGHTED / ACTION_EXECUTED events."""
    action_public_id: str
    action_type: str
    verdict: str
    estimated_micro_usd: int = 0
    reason: str = ""
    is_allowed: bool = True


class TelemetryPayload(BaseModel):
    """Payload for TELEMETRY_RECORDED events."""
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    provider: str = ""
    model: str = ""
    virtual_cost_micro_usd: int = 0
    provider_cost_micro_usd: int = 0
    latency_ms: float = 0.0
    estimated_watt_hours: float = 0.0


class AuditLogPayload(BaseModel):
    """Payload for AUDIT_LOG events."""
    actor_type: str = "system"  # agent | user | system
    actor_id: str = ""
    action: str
    resource_type: str
    resource_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)