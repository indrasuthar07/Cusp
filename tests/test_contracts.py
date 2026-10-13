"""Phase 0 JSON-schema and provider-contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from cusp.providers.base import ProviderRequest
from cusp.providers.fake import DeterministicFakeAdapter
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT / "contracts" / "v1"
FORMAT_CHECKER = FormatChecker()


def load_schema(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def assert_valid(instance: dict, schema_name: str) -> None:
    schema = load_schema(schema_name)
    Draft202012Validator(schema, format_checker=FORMAT_CHECKER).validate(instance)


def test_every_capability_matrix_profile_matches_provider_schema() -> None:
    matrix = json.loads(
        (CONTRACTS / "provider-capability-matrix.json").read_text(encoding="utf-8")
    )
    assert matrix["schema_version"] == 1
    assert {profile["profile_id"] for profile in matrix["profiles"]} == {
        "fake",
        "ollama",
        "gemini",
        "groq",
        "huggingface",
        "openai",
        "anthropic",
    }
    for profile in matrix["profiles"]:
        assert_valid(profile, "provider-profile.schema.json")


def test_fake_adapter_matches_offline_profile_and_normalizes_usage() -> None:
    matrix = json.loads(
        (CONTRACTS / "provider-capability-matrix.json").read_text(encoding="utf-8")
    )
    fake_profile = next(p for p in matrix["profiles"] if p["profile_id"] == "fake")
    adapter = DeterministicFakeAdapter()
    adapter.validate_profile(fake_profile)
    assert adapter.health().healthy is True
    response = adapter.generate(ProviderRequest(prompt="hello", model="deterministic"))
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 8
    assert response.usage.cost_provenance.value == "virtual"


def test_runtime_action_policy_decision_and_event_examples_validate() -> None:
    action_id = str(uuid4())
    run_id = str(uuid4())
    assert_valid(
        {
            "schema_version": 1,
            "action_id": action_id,
            "run_id": run_id,
            "sequence": 1,
            "action_type": "model_call",
            "provider_profile": "fake",
            "estimated_cost_micro_usd": 0,
            "idempotency_key": "action-00000000000001",
            "data_classification": "public",
            "summary": "deterministic contract test",
            "progress": {"diff_bytes": 0, "tests_passed": 1, "tests_failed": 0},
            "metadata": {},
        },
        "runtime-action.schema.json",
    )
    assert_valid(
        {
            "schema_version": 1,
            "policy_id": str(uuid4()),
            "version": 1,
            "default_action": "pause",
            "budgets": {"run_cap_micro_usd": 1000, "organization_cap_micro_usd": 10000},
            "hosted_egress": {
                "enabled": False,
                "acknowledgement_version": None,
                "allowed_data_classes": ["public"],
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "pause",
                "on_policy_resolution_failure": "pause",
            },
        },
        "policy.schema.json",
    )
    assert_valid(
        {
            "schema_version": 1,
            "decision_id": str(uuid4()),
            "action_id": action_id,
            "verdict": "allow",
            "reason_code": "policy.allow",
            "cost_provenance": "virtual",
            "policy_version": 1,
            "ev_score": 1.0,
            "loop_score": 0.0,
            "approved_profile": "fake",
            "decision_token": None,
            "expires_at": "2026-10-14T12:00:00Z",
        },
        "decision.schema.json",
    )
    assert_valid(
        {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "event_type": "action.preflighted",
            "organization_id": str(uuid4()),
            "run_id": run_id,
            "occurred_at": "2026-10-14T12:00:00Z",
            "payload": {"verdict": "allow"},
        },
        "event.schema.json",
    )


def test_hosted_profile_requires_explicit_egress_ack() -> None:
    matrix = json.loads(
        (CONTRACTS / "provider-capability-matrix.json").read_text(encoding="utf-8")
    )
    for profile in matrix["profiles"]:
        if profile["provider"] not in {"fake", "ollama"}:
            assert profile["privacy_class"] == "hosted_consent_required"
            assert profile["requires_hosted_egress_ack"] is True


def test_invalid_hosted_profile_is_rejected() -> None:
    schema = load_schema("provider-profile.schema.json")
    invalid = {
        "schema_version": 1,
        "profile_id": "gemini",
        "provider": "gemini",
        "model": "configured-model",
        "privacy_class": "hosted_consent_required",
        "route_group": "hosted-general",
        "requires_hosted_egress_ack": False,
        "capabilities": {"streaming": True, "tools": True, "structured_output": True, "usage_reporting": True},
        "cost_policy": {"cost_source": "provider_reported", "input_micro_usd_per_1k_tokens": None, "output_micro_usd_per_1k_tokens": None},
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FORMAT_CHECKER).validate(invalid)
