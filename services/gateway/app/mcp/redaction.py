"""Deterministic MCP payload redaction and canonicalization."""

from __future__ import annotations

import json
import re
from typing import Any

REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|secret|token|api[_-]?key|"
    r"private[_-]?key|prompt|raw[_-]?input|source[_-]?code|tool[_-]?output|"
    r"arguments|content)",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact_text(value: str) -> str:
    """Redact credential-shaped text and remove unsafe control characters."""

    return _CONTROL_CHARS.sub("", _SECRET_TEXT.sub(r"\1" + REDACTED_VALUE, value))


def redact_payload(value: Any, *, key: str = "") -> Any:
    """Recursively remove secrets and user content from an MCP payload."""

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
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def canonicalize_payload(value: Any) -> Any:
    """Return a stable, JSON-safe representation for fingerprints and events."""

    if isinstance(value, dict):
        return {
            str(key): canonicalize_payload(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [canonicalize_payload(item) for item in value]
    return value


def redact_and_canonicalize(value: Any) -> Any:
    return canonicalize_payload(redact_payload(value))


def canonical_json(value: Any) -> str:
    """Serialize a redacted canonical payload deterministically."""

    return json.dumps(
        redact_and_canonicalize(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
