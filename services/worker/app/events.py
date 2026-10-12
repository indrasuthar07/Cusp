"""
Worker event models and stream definitions matching Gateway contract.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


EVENTS_STREAM_NAME: str = "cusp:v1:events"
DEAD_LETTER_STREAM_NAME: str = "cusp:v1:events:dead"
DEFAULT_CONSUMER_GROUP: str = "cusp-workers"


class CuspEventType(str, enum.Enum):
    RUN_CREATED = "RUN_CREATED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_TERMINATED = "RUN_TERMINATED"
    RUN_COMPLETED = "RUN_COMPLETED"
    ACTION_PREFLIGHTED = "ACTION_PREFLIGHTED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    TELEMETRY_RECORDED = "TELEMETRY_RECORDED"
    AUDIT_LOG = "AUDIT_LOG"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    AUDIT_BACKLOG_WARNING = "AUDIT_BACKLOG_WARNING"


class CuspEvent(BaseModel):
    """Event envelope parsed from Redis Streams."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    org_id: int
    session_id: int | None = None
    agent_id: int | None = None
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_stream_entry(self) -> dict[str, str]:
        """Convert event envelope to Redis XADD string dictionary."""
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
            "payload": json.dumps(self.payload, default=_json_serial),
        }

    @classmethod
    def from_stream_entry(cls, entry: dict[Any, Any]) -> CuspEvent:
        """Parse raw Redis Stream key/values (handling bytes or str)."""
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