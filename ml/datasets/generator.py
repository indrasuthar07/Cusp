"""
ml/datasets/generator.py

Generates the deterministic scenario dataset by running scenarios through
PolicyResolver and capturing complete state:
- actions, policies, costs, progress, pauses, failures, and verified outcomes
- stable scenario and task identifiers
- sha256 manifest generation
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.governance.engine import (
    DecisionTokenSigner,
    GovernanceContext,
    PolicyResolver,
    ProgressSignal,
)

from ml.datasets.scenarios import (
    FEATURE_SCHEMA_VERSION,
    LABEL_SCHEMA_VERSION,
    SCENARIOS,
    ScenarioDefinition,
)

SIGNER_SECRET = "cusp-deterministic-test-secret-key-32b"


def run_scenario(scenario: ScenarioDefinition) -> list[dict[str, Any]]:
    """Execute scenario steps deterministically against PolicyResolver and yield action records."""
    signer = DecisionTokenSigner(SIGNER_SECRET)
    resolver = PolicyResolver(
        rules=scenario.policy_rules,
        policy_version=1,
        signer=signer,
    )

    records: list[dict[str, Any]] = []
    recent_progress: list[ProgressSignal] = []

    run_spent_micro_usd = 0
    org_spent_micro_usd = 0

    for seq, step in enumerate(scenario.action_sequence, start=1):
        progress = ProgressSignal(
            fingerprint=step.fingerprint,
            diff_bytes=step.diff_bytes,
            tests_passed=step.tests_passed,
            tests_failed=step.tests_failed,
            elapsed_ms=step.elapsed_ms,
            semantic_similarity=step.semantic_similarity,
            embedding_available=step.embedding_available,
        )

        ctx = GovernanceContext(
            action_id=f"{scenario.scenario_id}-act-{seq:03d}",
            provider_profile=step.provider_profile,
            estimated_cost_micro_usd=step.estimated_cost_micro_usd,
            run_spent_micro_usd=run_spent_micro_usd,
            run_reserved_micro_usd=0,
            org_spent_micro_usd=org_spent_micro_usd,
            org_reserved_micro_usd=0,
            data_classification=step.data_classification,
            hosted_egress_allowed=scenario.hosted_egress_allowed,
            provider_error_code=step.provider_error_code,
            progress=progress,
            recent_progress=tuple(recent_progress),
        )

        decision = resolver.resolve(ctx)

        # Assert actual matches expected design
        if decision.verdict != step.expected_verdict:
            raise ValueError(
                f"Scenario {scenario.scenario_id} step {seq}: expected {step.expected_verdict}, got {decision.verdict} (reason: {decision.reason_code})"
            )

        record = {
            "scenario_id": scenario.scenario_id,
            "task_id": scenario.task_id,
            "provider_kind": scenario.provider_kind,
            "sequence": seq,
            "action_type": step.action_type,
            "provider_profile": step.provider_profile,
            "estimated_cost_micro_usd": step.estimated_cost_micro_usd,
            "actual_cost_micro_usd": step.actual_cost_micro_usd,
            "run_spent_micro_usd_before": run_spent_micro_usd,
            "org_spent_micro_usd_before": org_spent_micro_usd,
            "budget_cap_micro_usd": scenario.budget_cap_micro_usd,
            "fingerprint": step.fingerprint,
            "diff_bytes": step.diff_bytes,
            "tests_passed": step.tests_passed,
            "tests_failed": step.tests_failed,
            "elapsed_ms": step.elapsed_ms,
            "semantic_similarity": step.semantic_similarity,
            "embedding_available": step.embedding_available,
            "provider_error_code": step.provider_error_code,
            "data_classification": step.data_classification,
            "hosted_egress_allowed": scenario.hosted_egress_allowed,
            # Policy resolution results
            "decision_id": f"dec-{scenario.scenario_id}-{seq:03d}",
            "verdict": decision.verdict,
            "reason_code": decision.reason_code,
            "cost_provenance": decision.cost_provenance,
            "approved_profile": decision.approved_profile,
            "loop_score": decision.loop_score,
            "action_outcome": step.action_outcome,
            # Verified ground truth (not merely agent self-declared)
            "run_outcome": scenario.run_outcome,
            "verified_success": scenario.verified_success,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "label_schema_version": LABEL_SCHEMA_VERSION,
        }
        records.append(record)

        # Update running state
        if decision.verdict in {"allow", "downgrade"} and step.action_outcome == "completed":
            run_spent_micro_usd += step.actual_cost_micro_usd
            org_spent_micro_usd += step.actual_cost_micro_usd

        recent_progress.append(progress)
        if len(recent_progress) > 20:
            recent_progress.pop(0)

    return records


def generate_dataset(output_dir: Path) -> dict[str, Any]:
    """Generate actions JSONL, dataset summary, and manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    actions_file = output_dir / "deterministic_actions.jsonl"
    manifest_file = output_dir / "dataset_manifest.json"

    all_records: list[dict[str, Any]] = []
    scenarios_summary: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        records = run_scenario(scenario)
        all_records.extend(records)
        scenarios_summary.append(
            {
                "scenario_id": scenario.scenario_id,
                "task_id": scenario.task_id,
                "provider_kind": scenario.provider_kind,
                "description": scenario.description,
                "num_actions": len(records),
                "run_outcome": scenario.run_outcome,
                "verified_success": scenario.verified_success,
            }
        )

    # Write JSONL
    with open(actions_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(rec, sort_keys=True) + "\n" for rec in all_records)

    # Compute hash of data file
    hasher = hashlib.sha256()
    with open(actions_file, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    data_sha256 = hasher.hexdigest()

    manifest = {
        "dataset_name": "cusp-deterministic-scenarios",
        "dataset_version": "1.0.0",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "total_scenarios": len(SCENARIOS),
        "total_actions": len(all_records),
        "unique_tasks": sorted({s["task_id"] for s in scenarios_summary}),
        "data_file": actions_file.name,
        "sha256": data_sha256,
        "verdict_distribution": {
            v: sum(1 for r in all_records if r["verdict"] == v)
            for v in ("allow", "downgrade", "pause", "escalate", "kill")
        },
        "run_outcome_distribution": {
            o: sum(1 for s in scenarios_summary if s["run_outcome"] == o)
            for o in sorted({s["run_outcome"] for s in scenarios_summary})
        },
        "scenarios": scenarios_summary,
    }

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return manifest
