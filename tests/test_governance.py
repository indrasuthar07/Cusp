"""Deterministic governance engine and endpoint contract tests."""

from __future__ import annotations

import uuid

import pytest
from app.governance.engine import (
    DecisionTokenError,
    DecisionTokenSigner,
    GovernanceContext,
    PolicyResolver,
    ProgressSignal,
)


def _resolver(rules: dict) -> PolicyResolver:
    return PolicyResolver(rules, policy_version=4, signer=DecisionTokenSigner("s" * 32))


def test_signed_decision_token_is_bound_and_tamper_evident() -> None:
    decision = _resolver({"default_action": "allow"}).resolve(
        GovernanceContext(
            action_id="action-1", provider_profile="fake", estimated_cost_micro_usd=0
        )
    )
    signer = DecisionTokenSigner("s" * 32)
    assert (
        signer.verify(decision.decision_token, action_id="action-1")["verdict"]
        == "allow"
    )
    with pytest.raises(DecisionTokenError):
        signer.verify(decision.decision_token, action_id="action-2")
    encoded, signature = decision.decision_token.split(".")
    with pytest.raises(DecisionTokenError):
        signer.verify(f"{encoded}.bad{signature}")


def test_budget_and_hosted_egress_fail_safe_to_pause() -> None:
    resolver = _resolver(
        {
            "default_action": "allow",
            "budgets": {"run_cap_micro_usd": 10, "organization_cap_micro_usd": 100},
            "hosted_egress": {"enabled": True, "allowed_data_classes": ["public"]},
            "fail_safe": {"on_budget_check_failure": "pause"},
        }
    )
    budget = resolver.resolve(
        GovernanceContext(
            action_id="a",
            provider_profile="fake",
            estimated_cost_micro_usd=2,
            run_spent_micro_usd=9,
        )
    )
    consent = resolver.resolve(
        GovernanceContext(
            action_id="b", provider_profile="openai", estimated_cost_micro_usd=0
        )
    )
    assert budget.verdict == "pause"
    assert budget.reason_code == "run_budget_cap_exceeded"
    assert consent.verdict == "pause"
    assert consent.reason_code == "hosted_egress_consent_required"


def test_exact_repeats_and_provider_quota_are_deterministic() -> None:
    resolver = _resolver(
        {
            "default_action": "allow",
            "loop_detection": {"exact_repeat_limit": 3},
            "fail_safe": {"on_provider_quota_failure": "downgrade"},
            "quota_downgrade_profile": "ollama",
        }
    )
    repeat = ProgressSignal(fingerprint="same", diff_bytes=0)
    decision = resolver.resolve(
        GovernanceContext(
            action_id="a",
            provider_profile="fake",
            estimated_cost_micro_usd=0,
            progress=repeat,
            recent_progress=(repeat, repeat),
        )
    )
    quota = resolver.resolve(
        GovernanceContext(
            action_id="b",
            provider_profile="openai",
            estimated_cost_micro_usd=0,
            provider_error_code="provider_quota_exhausted",
        )
    )
    assert decision.verdict == "pause"
    assert decision.reason_code == "exact_repeat_no_progress"
    assert quota.verdict == "downgrade"
    assert quota.approved_profile == "ollama"


def test_cost_provenance_is_virtual_for_local_profiles() -> None:
    local = _resolver({"default_action": "allow"}).resolve(
        GovernanceContext(
            action_id=str(uuid.uuid4()),
            provider_profile="ollama",
            estimated_cost_micro_usd=0,
        )
    )
    hosted = _resolver({"default_action": "allow"}).resolve(
        GovernanceContext(
            action_id=str(uuid.uuid4()),
            provider_profile="openai",
            estimated_cost_micro_usd=0,
        )
    )
    assert local.cost_provenance == "virtual"
    assert hosted.cost_provenance == "provider_reported"
