"""Offline SDK lifecycle and Ollama adapter reference tests."""

from __future__ import annotations

import json
from typing import Self
from unittest.mock import patch

import pytest
from cusp import (
    ControlAction,
    ControlCommand,
    CuspClient,
    GovernanceBlocked,
    InMemoryGatewayTransport,
    RunState,
)
from cusp.providers.base import AdapterError, ProviderRequest
from cusp.providers.fake import DeterministicFakeAdapter, FakeScenario
from cusp.providers.ollama import OllamaAdapter, OllamaConfig


@pytest.mark.asyncio
async def test_offline_fake_reference_run_completes_without_hosted_provider() -> None:
    transport = InMemoryGatewayTransport()
    client = CuspClient(transport)
    run = await client.create_run(budget_cap_micro_usd=100, metadata={"mode": "offline"})
    action, decision = await run.preflight(
        action_type="model_call",
        provider_profile="fake",
        estimated_cost_micro_usd=0,
        data_classification="public",
    )

    assert decision.verdict.value == "allow"
    response = await run.invoke(
        action,
        DeterministicFakeAdapter(),
        prompt="hello",
        model="deterministic",
    )
    await run.finish()

    assert response.content == "This is a deterministic fake response."
    assert response.usage.virtual_cost_micro_usd == 0
    assert transport.spent[run.run.run_id] == 0
    assert run.run.state is RunState.COMPLETED


@pytest.mark.asyncio
async def test_fake_scenarios_are_deterministic_and_typed() -> None:
    no_progress = DeterministicFakeAdapter(FakeScenario.NO_PROGRESS)
    assert no_progress.generate(ProviderRequest("x", "deterministic")).content == "No progress was made."

    transient = DeterministicFakeAdapter(FakeScenario.TRANSIENT_FAILURE)
    with pytest.raises(AdapterError) as first_error:
        transient.generate(ProviderRequest("x", "deterministic"))
    assert first_error.value.code == "provider_timeout"
    assert transient.generate(ProviderRequest("x", "deterministic")).content


@pytest.mark.asyncio
async def test_budget_pause_cannot_invoke_provider() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=0)
    action, decision = await run.preflight(
        action_type="model_call",
        provider_profile="fake",
        estimated_cost_micro_usd=1,
    )
    assert decision.reason_code == "budget_cap_exceeded"
    with pytest.raises(GovernanceBlocked):
        await run.invoke(action, DeterministicFakeAdapter(), prompt="x", model="deterministic")


@pytest.mark.asyncio
async def test_control_poll_applies_pause_and_terminate() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=10)
    transport.controls[run.run.run_id] = ControlCommand(ControlAction.PAUSE)
    assert (await run.poll_control()).action is ControlAction.PAUSE
    assert run.run.state is RunState.PAUSED
    transport.controls[run.run.run_id] = ControlCommand(ControlAction.TERMINATE)
    assert (await run.poll_control()).action is ControlAction.TERMINATE
    assert run.run.state is RunState.TERMINATED


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_ollama_health_and_generation_normalize_local_api() -> None:
    adapter = OllamaAdapter(OllamaConfig(model="llama3.2"))
    adapter.validate_profile(
        {
            "provider": "ollama",
            "model": "llama3.2",
            "privacy_class": "offline_private",
            "capabilities": {"streaming": True, "tools": True, "structured_output": True},
        }
    )
    responses = iter(
        [
            FakeHTTPResponse({"models": [{"name": "llama3.2"}]}),
            FakeHTTPResponse(
                {
                    "message": {"content": "local response"},
                    "prompt_eval_count": 12,
                    "eval_count": 7,
                    "done_reason": "stop",
                }
            ),
        ]
    )
    with patch("cusp.providers.ollama.adapter.urlopen", side_effect=lambda *args, **kwargs: next(responses)):
        assert adapter.health().healthy is True
        result = adapter.generate(ProviderRequest("hello", "llama3.2"))

    assert result.content == "local response"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.usage.cost_provenance.value == "virtual"


def test_ollama_unavailable_is_safe_and_non_secret() -> None:
    adapter = OllamaAdapter(OllamaConfig(base_url="http://localhost:11434"))
    with patch("cusp.providers.ollama.adapter.urlopen", side_effect=OSError("connection secret")):
        health = adapter.health()
    assert health.healthy is False
    assert health.message == "ollama_unavailable"
    assert "connection secret" not in health.message