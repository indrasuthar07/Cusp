"""Offline SDK lifecycle and Ollama adapter reference tests."""

from __future__ import annotations

import asyncio
import json
from typing import Self
from unittest.mock import patch

import pytest
from cusp import (
    ActionRequest,
    BudgetOverrun,
    CompletionReport,
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
    run = await client.create_run(
        budget_cap_micro_usd=100, metadata={"mode": "offline"}
    )
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
async def test_sdk_forwards_semantic_progress_to_preflight() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=10)
    action, decision = await run.preflight(
        action_type="tool_call",
        provider_profile="fake",
        estimated_cost_micro_usd=0,
        progress={"semantic_similarity": 0.99, "embedding_available": True},
    )
    assert decision.verdict.value == "allow"
    assert transport.actions[action.action_id].progress["semantic_similarity"] == 0.99


@pytest.mark.asyncio
async def test_fake_scenarios_are_deterministic_and_typed() -> None:
    no_progress = DeterministicFakeAdapter(FakeScenario.NO_PROGRESS)
    assert (
        no_progress.generate(ProviderRequest("x", "deterministic")).content
        == "No progress was made."
    )

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
    assert run.run.state is RunState.RUNNING
    with pytest.raises(GovernanceBlocked):
        await run.invoke(
            action, DeterministicFakeAdapter(), prompt="x", model="deterministic"
        )


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


@pytest.mark.asyncio
async def test_duplicate_preflight_returns_original_decision_and_reserves_once() -> (
    None
):
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=10)
    action = ActionRequest(
        "action-1", run.run.run_id, 1, "model_call", "fake", 6, "idem-1"
    )

    first = await transport.preflight(action)
    duplicate = await transport.preflight(action)

    assert duplicate == first
    assert len(transport.idempotency) == 1
    assert transport.reserved[run.run.run_id] == 6


@pytest.mark.asyncio
async def test_rejected_preflight_key_can_retry_after_budget_release() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=5)
    accepted = ActionRequest(
        "accepted", run.run.run_id, 1, "model_call", "fake", 5, "accepted"
    )
    rejected = ActionRequest(
        "rejected", run.run.run_id, 2, "model_call", "fake", 5, "retryable"
    )

    assert (await transport.preflight(accepted)).verdict.value == "allow"
    assert (await transport.preflight(rejected)).reason_code == "budget_cap_exceeded"
    await transport.report_completion(CompletionReport("accepted", "completed", 0))
    retried = await transport.preflight(rejected)

    assert retried.verdict.value == "allow"
    assert transport.reserved[run.run.run_id] == 5
    assert "retryable" in transport.idempotency


@pytest.mark.asyncio
async def test_concurrent_preflight_never_exceeds_budget() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=3)
    actions = [
        ActionRequest(
            f"action-{index}",
            run.run.run_id,
            index,
            "model_call",
            "fake",
            1,
            f"idem-{index}",
        )
        for index in range(8)
    ]
    decisions = await asyncio.gather(
        *(transport.preflight(action) for action in actions)
    )

    assert sum(decision.verdict.value == "allow" for decision in decisions) == 3
    assert transport.reserved[run.run.run_id] == 3
    for action, decision in zip(actions, decisions, strict=True):
        if decision.verdict.value == "allow":
            await transport.report_completion(
                CompletionReport(action.action_id, "completed", 1)
            )
    assert transport.reserved[run.run.run_id] == 0
    assert transport.reserved[run.run.run_id] >= 0


@pytest.mark.asyncio
async def test_duplicate_completion_releases_reservation_only_once() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=10)
    action = ActionRequest(
        "action-1", run.run.run_id, 1, "model_call", "fake", 4, "idem-1"
    )
    await transport.preflight(action)
    report = CompletionReport(action.action_id, "completed", 3)

    await transport.report_completion(report)
    await transport.report_completion(report)

    assert transport.reserved[run.run.run_id] == 0
    assert transport.spent[run.run.run_id] == 3


@pytest.mark.asyncio
async def test_actual_cost_overrun_is_reconciled_and_fails_run() -> None:
    transport = InMemoryGatewayTransport()
    run = await CuspClient(transport).create_run(budget_cap_micro_usd=5)
    action = ActionRequest(
        "action-1", run.run.run_id, 1, "model_call", "fake", 2, "idem-1"
    )
    await transport.preflight(action)

    report = CompletionReport(action.action_id, "completed", 8)
    with pytest.raises(BudgetOverrun):
        await transport.report_completion(report)
    await transport.report_completion(report)

    assert transport.spent[run.run.run_id] == 8
    assert transport.reserved[run.run.run_id] == 0
    assert transport.runs[run.run.run_id].state is RunState.FAILED
    assert transport.completions[action.action_id].status == "overrun"


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
            "capabilities": {
                "streaming": True,
                "tools": True,
                "structured_output": True,
            },
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
    with patch(
        "cusp.providers.ollama.adapter.urlopen",
        side_effect=lambda *args, **kwargs: next(responses),
    ):
        assert adapter.health().healthy is True
        result = adapter.generate(ProviderRequest("hello", "llama3.2"))

    assert result.content == "local response"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.usage.cost_provenance.value == "virtual"


def test_ollama_unavailable_is_safe_and_non_secret() -> None:
    adapter = OllamaAdapter(OllamaConfig(base_url="http://localhost:11434"))
    with patch(
        "cusp.providers.ollama.adapter.urlopen",
        side_effect=OSError("connection secret"),
    ):
        health = adapter.health()
    assert health.healthy is False
    assert health.message == "ollama_unavailable"
    assert "connection secret" not in health.message
