"""Offline contract tests for hosted provider adapters."""

from __future__ import annotations

import json
from typing import Any, Self
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from cusp.providers.base import AdapterError, ProviderRequest
from cusp.providers.hosted import (
    AnthropicAdapter,
    GeminiAdapter,
    GroqAdapter,
    HostedProviderConfig,
    OpenAIAdapter,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _config(**kwargs: Any) -> HostedProviderConfig:
    return HostedProviderConfig(
        api_key="test-key",
        model="test-model",
        base_url="https://provider.example/v1",
        **kwargs,
    )


def test_hosted_config_rejects_missing_or_negative_values() -> None:
    with pytest.raises(ValueError, match="API key"):
        HostedProviderConfig(" ", "model", "https://provider.example")
    with pytest.raises(ValueError, match="cannot be negative"):
        _config(input_micro_usd_per_1k_tokens=-1)


def test_openai_compatible_health_and_usage_are_normalized() -> None:
    adapter = OpenAIAdapter(
        _config(input_micro_usd_per_1k_tokens=100, output_micro_usd_per_1k_tokens=200)
    )
    profile = {
        "provider": "openai",
        "privacy_class": "hosted_consent_required",
        "capabilities": {"streaming": True, "tools": True, "structured_output": True},
    }
    adapter.validate_profile(profile)
    responses = iter(
        [
            _Response({"data": [{"id": "test-model"}]}),
            _Response(
                {
                    "choices": [
                        {"message": {"content": "hello"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            ),
        ]
    )
    with patch(
        "cusp.providers.hosted.adapter.urlopen",
        side_effect=lambda *a, **k: next(responses),
    ):
        assert adapter.health().healthy
        result = adapter.generate(ProviderRequest("prompt", "test-model"))
    assert result.content == "hello"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.virtual_cost_micro_usd == 2
    assert result.usage.cost_provenance.value == "catalog_estimate"


def test_hosted_http_429_is_a_retryable_quota_error() -> None:
    adapter = GroqAdapter(_config())
    error = HTTPError("https://provider.example", 429, "quota", {}, None)
    with (
        patch("cusp.providers.hosted.adapter.urlopen", side_effect=error),
        pytest.raises(AdapterError) as raised,
    ):
        adapter.generate(ProviderRequest("prompt", "test-model"))
    assert raised.value.code == "provider_quota_exhausted"
    assert raised.value.retryable


def test_gemini_requires_consent_and_parses_official_response() -> None:
    adapter = GeminiAdapter(_config())
    with pytest.raises(ValueError, match="hosted egress"):
        adapter.validate_profile(
            {"provider": "gemini", "privacy_class": "hosted_consent_required"}
        )
    adapter.validate_profile(
        {
            "provider": "gemini",
            "privacy_class": "hosted_consent_required",
            "requires_hosted_egress_ack": True,
        }
    )
    with patch(
        "cusp.providers.hosted.adapter.urlopen",
        return_value=_Response(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "gemini response"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
            }
        ),
    ):
        result = adapter.generate(ProviderRequest("prompt", "test-model"))
    assert result.content == "gemini response"
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 4
    assert result.usage.cost_provenance.value == "unknown"


def test_anthropic_health_does_not_leak_or_call_unsupported_models_endpoint() -> None:
    health = AnthropicAdapter(_config()).health()
    assert health.healthy
    assert health.message == "configured"
