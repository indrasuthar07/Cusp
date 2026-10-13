"""Defensive event redaction used at every worker persistence boundary."""

from __future__ import annotations

import json
import re
from typing import Any

REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|api[_-]?key|authorization|credential|private[_-]?key|"
    r"prompt|tool[_-]?output|source[_-]?code|raw[_-]?input|cookie)",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    return re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+",
        r"\1" + REDACTED_VALUE,
        value,
    )


def redact_payload(value: Any, *, key: str = "") -> Any:
    """Recursively redact sensitive keys and credential-shaped text."""
    if _SENSITIVE_KEY.search(key):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(child_key): redact_payload(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(child, key=key) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_stream_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Normalize and redact raw Redis fields for DLQ storage/replay."""
    result: dict[str, str] = {}
    for key, value in fields.items():
        key_str = (
            key.decode("utf-8", errors="replace")
            if isinstance(key, bytes)
            else str(key)
        )
        value_str = (
            value.decode("utf-8", errors="replace")
            if isinstance(value, bytes)
            else str(value)
        )
        if key_str == "payload":
            try:
                value_str = json.dumps(
                    redact_payload(json.loads(value_str)), separators=(",", ":")
                )
            except (TypeError, ValueError):
                value_str = redact_text(value_str)
        else:
            value_str = str(redact_payload(value_str, key=key_str))
        result[key_str] = value_str
    return result
