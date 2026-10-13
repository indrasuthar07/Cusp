"""Pure, deterministic governance policy resolution and decision signing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

VERDICTS = {"allow", "downgrade", "pause", "escalate", "kill"}


@dataclass(frozen=True, slots=True)
class ProgressSignal:
    """Bounded progress evidence supplied by the runtime."""

    fingerprint: str = ""
    diff_bytes: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        if min(self.diff_bytes, self.tests_passed, self.tests_failed, self.elapsed_ms) < 0:
            raise ValueError("progress values cannot be negative")

    @property
    def made_progress(self) -> bool:
        return self.diff_bytes > 0 or self.tests_passed > 0


@dataclass(frozen=True, slots=True)
class GovernanceContext:
    action_id: str
    provider_profile: str
    estimated_cost_micro_usd: int
    run_spent_micro_usd: int = 0
    run_reserved_micro_usd: int = 0
    org_spent_micro_usd: int = 0
    org_reserved_micro_usd: int = 0
    data_classification: str = "internal"
    hosted_egress_allowed: bool = False
    provider_error_code: str | None = None
    progress: ProgressSignal = field(default_factory=ProgressSignal)
    recent_progress: tuple[ProgressSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    decision_id: str
    action_id: str
    verdict: str
    reason_code: str
    cost_provenance: str
    policy_version: int
    approved_profile: str | None
    loop_score: float | None
    expires_at: int
    decision_token: str


class DecisionTokenError(ValueError):
    """Raised for a malformed, expired, or tampered decision token."""


class DecisionTokenSigner:
    """Compact HMAC-signed decision tokens with expiry and audience binding."""

    def __init__(self, secret: str, *, issuer: str = "cusp-gateway") -> None:
        if len(secret) < 16:
            raise ValueError("decision signing secret must be at least 16 characters")
        self._secret = secret.encode("utf-8")
        self._issuer = issuer

    def sign(self, decision: GovernanceDecision) -> str:
        payload = {
            "iss": self._issuer,
            "decision_id": decision.decision_id,
            "action_id": decision.action_id,
            "verdict": decision.verdict,
            "policy_version": decision.policy_version,
            "exp": decision.expires_at,
        }
        encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        return f"{encoded}.{_b64url(signature)}"

    def verify(self, token: str, *, action_id: str | None = None, now: int | None = None) -> dict[str, Any]:
        try:
            encoded, signature = token.split(".", 1)
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(_b64decode(signature), expected):
                raise DecisionTokenError("invalid decision signature")
            payload = json.loads(_b64decode(encoded))
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise DecisionTokenError("invalid decision token") from exc
        try:
            expires_at = int(payload.get("exp", 0))
        except (TypeError, ValueError) as exc:
            raise DecisionTokenError("invalid decision expiry") from exc
        current_time = int(time.time()) if now is None else now
        if payload.get("iss") != self._issuer or expires_at <= current_time:
            raise DecisionTokenError("expired or foreign decision token")
        if action_id is not None and payload.get("action_id") != action_id:
            raise DecisionTokenError("decision token action mismatch")
        return payload


class PolicyResolver:
    """Resolve one decision using stable rule ordering and no model calls."""

    def __init__(self, rules: dict[str, Any], *, policy_version: int, signer: DecisionTokenSigner, ttl_seconds: int = 300) -> None:
        self.rules = rules
        self.policy_version = policy_version
        self.signer = signer
        self.ttl_seconds = ttl_seconds

    def resolve(self, context: GovernanceContext) -> GovernanceDecision:
        action = self.rules.get("default_action", "pause")
        if action not in VERDICTS:
            return self._decision(context, "pause", "policy_resolution_failed", None)
        budgets = self.rules.get("budgets", {})
        fail_safe = self.rules.get("fail_safe", {})
        if context.estimated_cost_micro_usd < 0:
            return self._decision(context, "pause", "invalid_cost", None)
        run_cap = budgets.get("run_cap_micro_usd")
        org_cap = budgets.get("organization_cap_micro_usd")
        if isinstance(run_cap, int) and context.run_spent_micro_usd + context.run_reserved_micro_usd + context.estimated_cost_micro_usd > run_cap:
            return self._decision(context, fail_safe.get("on_budget_check_failure", "pause"), "run_budget_cap_exceeded", None)
        if isinstance(org_cap, int) and context.org_spent_micro_usd + context.org_reserved_micro_usd + context.estimated_cost_micro_usd > org_cap:
            return self._decision(context, fail_safe.get("on_budget_check_failure", "pause"), "organization_budget_cap_exceeded", None)

        hosted = self.rules.get("hosted_egress", {})
        if hosted.get("enabled") and not context.hosted_egress_allowed:
            return self._decision(context, "pause", "hosted_egress_consent_required", None)
        if context.data_classification not in set(hosted.get("allowed_data_classes", ["public", "internal", "confidential", "restricted"])):
            return self._decision(context, "pause", "data_classification_not_allowed", None)

        if context.provider_error_code in {"provider_quota_exhausted", "quota_exhausted", "rate_limited"}:
            verdict = fail_safe.get("on_provider_quota_failure", "pause")
            profile = self.rules.get("quota_downgrade_profile") if verdict == "downgrade" else None
            return self._decision(context, verdict, "provider_quota_exhausted", profile)
        if context.provider_error_code:
            return self._decision(context, fail_safe.get("on_provider_quota_failure", "pause"), "provider_error", None)

        loop = self.rules.get("loop_detection", {})
        repeat_limit = int(loop.get("exact_repeat_limit", 3))
        repeats = sum(1 for item in context.recent_progress if item.fingerprint and item.fingerprint == context.progress.fingerprint)
        if context.progress.fingerprint and repeats + 1 >= repeat_limit and not context.progress.made_progress:
            score = min(1.0, (repeats + 1) / max(1, repeat_limit))
            return self._decision(context, "pause", "exact_repeat_no_progress", None, score)

        profile = self.rules.get("downgrade_profile") if action == "downgrade" else None
        return self._decision(context, action, "policy_allow" if action == "allow" else f"policy_{action}", profile)

    def _decision(self, context: GovernanceContext, verdict: str, reason: str, profile: str | None, loop_score: float | None = None) -> GovernanceDecision:
        if verdict not in VERDICTS:
            verdict = "pause"
            reason = "policy_resolution_failed"
        decision = GovernanceDecision(
            decision_id=str(uuid.uuid4()),
            action_id=context.action_id,
            verdict=verdict,
            reason_code=reason,
            cost_provenance=str(
                self.rules.get(
                    "cost_provenance",
                    "virtual"
                    if context.provider_profile in {"fake", "ollama"}
                    else "provider_reported",
                )
            ),
            policy_version=self.policy_version,
            approved_profile=profile,
            loop_score=loop_score,
            expires_at=int(time.time()) + self.ttl_seconds,
            decision_token="",
        )
        return replace(decision, decision_token=self.signer.sign(decision))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))