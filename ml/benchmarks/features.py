"""Versioned feature-vector and label exports for Task 7.4."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ml.datasets.scenarios import FEATURE_SCHEMA_VERSION

from .runner import BenchmarkRecord, BenchmarkReport

LABEL_SCHEMA_VERSION = "1.0.0"


def feature_vector(record: BenchmarkRecord) -> dict[str, Any]:
    """Convert a record into model inputs without prompt or credential data."""

    return {
        "scenario_id": record.scenario_id,
        "task_id": record.task_id,
        "sequence": record.sequence,
        "provider_kind": record.provider_kind,
        "action_type": record.action_type,
        "estimated_cost_micro_usd": record.estimated_cost_micro_usd,
        "run_spent_micro_usd_before": record.run_spent_micro_usd_before,
        "diff_bytes": record.diff_bytes,
        "tests_passed": record.tests_passed,
        "tests_failed": record.tests_failed,
        "semantic_similarity": record.semantic_similarity,
        "embedding_available": record.embedding_available,
        "loop_score": record.loop_score,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "latency_ms": record.latency_ms,
        "virtual_cost_micro_usd": record.virtual_cost_micro_usd,
        "provider_cost_micro_usd": record.provider_cost_micro_usd,
        "cost_provenance": record.cost_provenance,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def label_record(record: BenchmarkRecord) -> dict[str, Any]:
    """Create labels using policy and verified outcomes, never self-reporting."""

    return {
        "scenario_id": record.scenario_id,
        "task_id": record.task_id,
        "sequence": record.sequence,
        "policy_verdict": record.policy_verdict,
        "policy_reason_code": record.policy_reason_code,
        "action_outcome": record.action_outcome,
        "run_outcome": record.run_outcome,
        "verified_success": record.verified_success,
        "stop_recommended": record.policy_verdict in {"pause", "kill"},
        "label_schema_version": LABEL_SCHEMA_VERSION,
    }


def export_features_and_labels(
    report: BenchmarkReport, output_dir: Path
) -> dict[str, Any]:
    """Write versioned feature/label JSONL files and an integrity manifest."""

    output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = output_dir / "feature_vectors.jsonl"
    label_path = output_dir / "labels.jsonl"
    _write_jsonl(feature_path, (feature_vector(r) for r in report.records))
    _write_jsonl(label_path, (label_record(r) for r in report.records))
    manifest = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "provider": report.provider,
        "model": report.model,
        "total_rows": len(report.records),
        "feature_file": feature_path.name,
        "label_file": label_path.name,
        "feature_sha256": _sha256(feature_path),
        "label_sha256": _sha256(label_path),
        "ground_truth": [
            "policy_verdict",
            "action_outcome",
            "run_outcome",
            "verified_success",
        ],
        "agent_declared_success_used": False,
    }
    (output_dir / "feature_label_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
