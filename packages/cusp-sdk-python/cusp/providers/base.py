"""Provider-neutral adapter contract used by the Cusp SDK.

Adapters own provider-specific authentication and wire formats. Governance
consumes only these normalized types, so a provider can never silently change
cost, capability, or error semantics in the hot path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class CostProvenance(StrEnum):
    """Source quality for a normalized cost value."""

    VIRTUAL = "virtual"
    PROVIDER_REPORTED = "provider_reported"
    CATALOG_ESTIMATE = "catalog_estimate"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """Capabilities advertised by a configured provider profile."""

    streaming: bool
    tools: bool
    structured_output: bool
    usage_reporting: bool


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Normalized request supplied by the SDK runtime."""

    prompt: str
    model: str
    stream: bool = False
    tools: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("provider model must not be empty")


@dataclass(frozen=True, slots=True)
class Usage:
    """Normalized provider usage and cost metadata."""

    input_tokens: int = 0
    output_tokens: int = 0
    provider_cost_micro_usd: int | None = None
    virtual_cost_micro_usd: int = 0
    cost_provenance: CostProvenance = CostProvenance.UNKNOWN
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if (
            self.provider_cost_micro_usd is not None
            and self.provider_cost_micro_usd < 0
        ):
            raise ValueError("provider cost cannot be negative")
        if self.virtual_cost_micro_usd < 0:
            raise ValueError("virtual cost cannot be negative")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency cannot be negative")


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Provider-independent response returned to the SDK."""

    content: str
    usage: Usage
    finish_reason: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    """Non-sensitive health result for a provider adapter."""

    healthy: bool
    provider: str
    message: str = ""


class AdapterError(RuntimeError):
    """Stable adapter failure with a policy-safe error code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ProviderAdapter(Protocol):
    """Interface every provider adapter must implement."""

    provider_kind: str
    capabilities: CapabilitySet

    def validate_profile(self, profile: dict[str, Any]) -> None:
        """Reject unsupported or incompatible profile configuration."""

    def health(self) -> AdapterHealth:
        """Return a bounded, non-secret health result."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a normalized response for an approved request."""
