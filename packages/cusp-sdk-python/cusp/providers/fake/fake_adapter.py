from __future__ import annotations

from enum import StrEnum
from typing import Any

from cusp.providers.base import (
    AdapterError,
    AdapterHealth,
    CapabilitySet,
    CostProvenance,
    ProviderRequest,
    ProviderResponse,
    Usage,
)


class FakeScenario(StrEnum):
    """Deterministic outcomes used by tests and offline demos."""

    SUCCESS = "success"
    NO_PROGRESS = "no_progress"
    TRANSIENT_FAILURE = "transient_failure"
    QUOTA_EXHAUSTED = "quota_exhausted"


class DeterministicFakeAdapter:
    """
    A fake provider adapter for testing, demos, and failure scenarios.
    Returns deterministic responses without requiring API keys.
    """
    def __init__(self, scenario: FakeScenario | str = FakeScenario.SUCCESS):
        self.provider_kind = "fake"
        self.is_healthy = True
        self.scenario = FakeScenario(scenario)
        self.call_count = 0
        self.capabilities = CapabilitySet(
            streaming=False,
            tools=False,
            structured_output=True,
            usage_reporting=True,
        )

    def validate_profile(self, profile: dict[str, Any]) -> None:
        if profile.get("provider") != self.provider_kind:
            raise ValueError("Fake adapter requires a fake provider profile")

    def health(self) -> AdapterHealth:
        return AdapterHealth(healthy=self.is_healthy, provider=self.provider_kind)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        if self.scenario is FakeScenario.QUOTA_EXHAUSTED:
            raise AdapterError("provider_quota_exhausted", "fake quota exhausted")
        if self.scenario is FakeScenario.TRANSIENT_FAILURE and self.call_count == 1:
            raise AdapterError("provider_timeout", "fake transient failure", retryable=True)

        content = (
            "No progress was made."
            if self.scenario is FakeScenario.NO_PROGRESS
            else "This is a deterministic fake response."
        )
        return ProviderResponse(
            content=content,
            usage=Usage(
                input_tokens=10,
                output_tokens=8,
                virtual_cost_micro_usd=0,
                cost_provenance=CostProvenance.VIRTUAL,
            ),
            finish_reason="stop",
        )

    def generate_response(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Backward-compatible dictionary response for existing demos."""
        response = self.generate(
            ProviderRequest(prompt=prompt, model=str(kwargs.get("model", "deterministic")))
        )
        return {
            "content": response.content,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "virtual_cost_micro_usd": response.usage.virtual_cost_micro_usd,
            },
        }