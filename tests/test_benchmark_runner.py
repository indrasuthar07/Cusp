"""Phase 7.2 benchmark runner tests (all network-free)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self
from unittest.mock import patch

from ml.benchmarks.features import export_features_and_labels
from ml.benchmarks.runner import (
    BenchmarkConfig,
    run_benchmark,
    run_scenario_benchmark,
    write_report,
)
from ml.datasets.scenarios import SCENARIOS


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_offline_benchmark_is_deterministic_and_keeps_costs_separate() -> None:
    config = BenchmarkConfig(provider="fake", host_wattage_watts=65)
    first = run_benchmark(config=config)
    second = run_benchmark(config=config)
    assert first.to_dict() == second.to_dict()
    assert first.total_actions == 91
    assert first.total_latency_ms > 0
    assert first.total_energy_wh is not None
    assert first.total_provider_cost_micro_usd is None
    assert all(record.benchmark_mode == "offline" for record in first.records)


def test_offline_benchmark_preserves_step_policy_and_errors() -> None:
    records = run_scenario_benchmark(SCENARIOS[7], BenchmarkConfig(provider="fake"))
    assert records[0].action_outcome == "completed"
    assert records[1].provider_error_code == "quota_exhausted"
    assert records[1].policy_verdict == "pause"
    assert records[1].action_outcome == "failed"


def test_ollama_benchmark_uses_local_adapter_without_hosted_key() -> None:
    payload = {
        "message": {"content": "local result"},
        "prompt_eval_count": 4,
        "eval_count": 6,
        "done_reason": "stop",
    }
    with patch(
        "cusp.providers.ollama.adapter.urlopen", return_value=_Response(payload)
    ):
        records = run_scenario_benchmark(
            SCENARIOS[0],
            BenchmarkConfig(provider="ollama", ollama_model="llama3.2"),
        )
    assert records[0].input_tokens == 4
    assert records[0].output_tokens == 6
    assert records[0].provider_cost_micro_usd is None
    assert records[0].cost_provenance == "virtual"
    assert records[0].benchmark_mode == "ollama_local"


def test_report_writer_hashes_jsonl(tmp_path: Path) -> None:
    report = run_benchmark(SCENARIOS[:1], BenchmarkConfig(provider="fake"))
    summary = write_report(report, tmp_path)
    records = tmp_path / "benchmark_records.jsonl"
    assert (tmp_path / "benchmark_report.json").exists()
    assert summary["records_sha256"]
    assert len(records.read_text(encoding="utf-8").splitlines()) == len(report.records)
    assert (tmp_path / "feature_vectors.jsonl").exists()
    assert (tmp_path / "labels.jsonl").exists()


def test_hosted_profiles_require_consent_and_key() -> None:
    import pytest

    with pytest.raises(ValueError, match="hosted_egress_consent"):
        BenchmarkConfig(provider="groq", hosted_api_key="secret")
    with pytest.raises(ValueError, match="API key"):
        BenchmarkConfig(provider="groq", hosted_egress_consent=True)


def test_opted_in_groq_profile_does_not_export_api_key(tmp_path: Path) -> None:
    payload = {
        "choices": [{"message": {"content": "hosted result"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3},
    }
    config = BenchmarkConfig(
        provider="groq",
        hosted_api_key="super-secret",
        hosted_model="llama-3.1",
        hosted_egress_consent=True,
    )
    with patch(
        "cusp.providers.hosted.adapter.urlopen", return_value=_Response(payload)
    ):
        report = run_benchmark(SCENARIOS[:1], config)
    summary = export_features_and_labels(report, tmp_path)
    assert report.records[0].input_tokens == 2
    assert report.records[0].benchmark_mode == "hosted_opt_in"
    exported = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.iterdir()
    )
    assert "super-secret" not in exported
    assert summary["agent_declared_success_used"] is False
