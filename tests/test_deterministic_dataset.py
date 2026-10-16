"""Unit tests for Phase 7 Task 7.1 — Deterministic scenario dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ml.datasets.generator import generate_dataset, run_scenario
from ml.datasets.scenarios import (
    FEATURE_SCHEMA_VERSION,
    LABEL_SCHEMA_VERSION,
    SCENARIOS,
    get_scenario,
    get_scenario_ids,
    get_scenarios_by_task,
    get_task_ids,
)


def test_scenario_counts_and_tasks() -> None:
    assert len(SCENARIOS) == 22
    task_ids = get_task_ids()
    assert len(task_ids) == 8
    assert all(tid.startswith("task-") for tid in task_ids)


def test_unique_scenario_ids() -> None:
    scn_ids = get_scenario_ids()
    assert len(scn_ids) == len(set(scn_ids)), "Scenario IDs must be globally unique"


def test_task_grouping() -> None:
    by_task = get_scenarios_by_task()
    for task_id, scns in by_task.items():
        assert len(scns) >= 2, f"Task {task_id} must have multiple scenarios for meaningful grouping"


def test_run_scenario_all() -> None:
    total_actions = 0
    for scn in SCENARIOS:
        records = run_scenario(scn)
        assert len(records) > 0
        total_actions += len(records)
        for r in records:
            assert r["scenario_id"] == scn.scenario_id
            assert r["task_id"] == scn.task_id
            assert r["verdict"] in {"allow", "downgrade", "pause", "escalate", "kill"}
            assert r["action_outcome"] in {"completed", "failed"}
            assert isinstance(r["verified_success"], bool)
            assert r["feature_schema_version"] == FEATURE_SCHEMA_VERSION
            assert r["label_schema_version"] == LABEL_SCHEMA_VERSION
    assert total_actions == 91


def test_dataset_generation_reproducibility(tmp_path: Path) -> None:
    manifest1 = generate_dataset(tmp_path / "run1")
    manifest2 = generate_dataset(tmp_path / "run2")

    assert manifest1["sha256"] == manifest2["sha256"]
    assert manifest1["total_scenarios"] == 22
    assert manifest1["total_actions"] == 91
    assert manifest1["verdict_distribution"]["allow"] == 70
    assert manifest1["verdict_distribution"]["pause"] == 11
    assert manifest1["verdict_distribution"]["downgrade"] == 10
    assert manifest1["run_outcome_distribution"]["succeeded"] == 11
    assert manifest1["run_outcome_distribution"]["paused"] == 11

    # Check that manifest file and jsonl file exist and are non-empty
    jsonl_path = tmp_path / "run1" / "deterministic_actions.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 91
