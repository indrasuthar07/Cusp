"""Offline and local-Ollama benchmark runner.

The runner intentionally depends only on the standard library and the Cusp SDK.
It never constructs a hosted adapter.  A benchmark record keeps virtual/local
accounting separate from provider-reported billing and records missing values
as ``null`` rather than inventing a zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cusp.providers.base import AdapterError, ProviderAdapter, ProviderRequest
from cusp.providers.fake import DeterministicFakeAdapter, FakeScenario
from cusp.providers.ollama import OllamaAdapter, OllamaConfig

from ml.datasets.generator import run_scenario
from ml.datasets.scenarios import SCENARIOS, ActionStep, ScenarioDefinition


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Execution settings safe for offline CI and local development."""

    provider: str = "fake"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    timeout_seconds: float = 30.0
    host_wattage_watts: float = 65.0

    def __post_init__(self) -> None:
        if self.provider not in {"fake", "ollama"}:
            raise ValueError("benchmark provider must be fake or ollama")
        if self.timeout_seconds <= 0:
            raise ValueError("benchmark timeout_seconds must be positive")
        if self.host_wattage_watts < 0:
            raise ValueError("host_wattage_watts cannot be negative")
        if not self.ollama_model.strip():
            raise ValueError("ollama_model must not be empty")


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    """One measured action, suitable for JSONL export."""

    scenario_id: str
    task_id: str
    sequence: int
    provider_kind: str
    model: str
    action_type: str
    policy_verdict: str
    policy_reason_code: str
    action_outcome: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    energy_wh: float | None
    energy_quality: str
    virtual_cost_micro_usd: int
    provider_cost_micro_usd: int | None
    cost_provenance: str
    provider_error_code: str | None
    run_outcome: str
    verified_success: bool
    benchmark_mode: str


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Aggregate report and records from one benchmark invocation."""

    benchmark_version: str
    provider: str
    model: str
    total_actions: int
    completed_actions: int
    failed_actions: int
    total_input_tokens: int
    total_output_tokens: int
    total_latency_ms: int
    total_energy_wh: float | None
    total_virtual_cost_micro_usd: int
    total_provider_cost_micro_usd: int | None
    records: tuple[BenchmarkRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["records"] = [asdict(record) for record in self.records]
        return value


def _fake_adapter_for(scenario: ScenarioDefinition) -> ProviderAdapter:
    """Return a deterministic success adapter; fixture errors are step-scoped."""

    # Scenario provider errors are intentionally not configured on the adapter:
    # configuring QUOTA_EXHAUSTED would make *preceding* successful actions fail
    # too.  The action fixture supplies the error only at its declared sequence.
    return DeterministicFakeAdapter(FakeScenario.SUCCESS)


def _adapter_for(
    scenario: ScenarioDefinition, config: BenchmarkConfig
) -> ProviderAdapter:
    if config.provider == "fake":
        return _fake_adapter_for(scenario)
    return OllamaAdapter(
        OllamaConfig(
            base_url=config.ollama_base_url,
            model=config.ollama_model,
            timeout_seconds=config.timeout_seconds,
        )
    )


def _prompt(scenario: ScenarioDefinition, step: ActionStep, sequence: int) -> str:
    """Use a stable, non-sensitive prompt for benchmark reproducibility."""

    return (
        f"Cusp benchmark scenario {scenario.scenario_id}; action {sequence}; "
        f"type {step.action_type}; fingerprint {step.fingerprint}. "
        "Return a concise deterministic result."
    )


def _energy(
    latency_ms: int, config: BenchmarkConfig, provider: str
) -> tuple[float | None, str]:
    if config.host_wattage_watts == 0:
        return None, "unavailable"
    # A wattage estimate is explicitly marked estimated; it is not telemetry.
    return config.host_wattage_watts * latency_ms / 3_600_000, "estimated"


def _requires_provider(step: ActionStep) -> bool:
    """Only model/tool/context actions consume provider tokens."""

    return step.action_type in {"model_call", "tool_call", "context_read"}


def run_scenario_benchmark(
    scenario: ScenarioDefinition, config: BenchmarkConfig | None = None
) -> list[BenchmarkRecord]:
    """Execute one deterministic scenario through fake or local Ollama."""

    settings = config or BenchmarkConfig()
    adapter = _adapter_for(scenario, settings)
    policy_records = {record["sequence"]: record for record in run_scenario(scenario)}
    records: list[BenchmarkRecord] = []

    for sequence, step in enumerate(scenario.action_sequence, start=1):
        policy = policy_records[sequence]
        started = time.perf_counter()
        error_code: str | None = step.provider_error_code
        input_tokens = output_tokens = 0
        provider_cost: int | None = None
        virtual_cost = 0
        provenance = "unknown"
        outcome = step.action_outcome
        try:
            # Scenario errors are recorded by the deterministic policy fixture;
            # don't turn a synthetic error into a real network request.
            if error_code is None and _requires_provider(step):
                response = adapter.generate(
                    ProviderRequest(
                        prompt=_prompt(scenario, step, sequence),
                        model=(
                            settings.ollama_model
                            if settings.provider == "ollama"
                            else "deterministic"
                        ),
                    )
                )
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                provider_cost = response.usage.provider_cost_micro_usd
                virtual_cost = response.usage.virtual_cost_micro_usd
                provenance = response.usage.cost_provenance.value
        except AdapterError as exc:
            error_code = error_code or exc.code
            outcome = "failed"
        elapsed = max(0, round((time.perf_counter() - started) * 1000))
        # The fixture's elapsed time is the stable offline measurement. Real
        # Ollama runs use measured wall time from the adapter invocation.
        latency_ms = step.elapsed_ms if settings.provider == "fake" else elapsed
        energy_wh, energy_quality = _energy(latency_ms, settings, settings.provider)
        records.append(
            BenchmarkRecord(
                scenario_id=scenario.scenario_id,
                task_id=scenario.task_id,
                sequence=sequence,
                provider_kind=settings.provider,
                model=settings.ollama_model
                if settings.provider == "ollama"
                else "deterministic",
                action_type=step.action_type,
                policy_verdict=policy["verdict"],
                policy_reason_code=policy["reason_code"],
                action_outcome=outcome,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                energy_wh=energy_wh,
                energy_quality=energy_quality,
                virtual_cost_micro_usd=virtual_cost,
                provider_cost_micro_usd=provider_cost,
                cost_provenance=provenance,
                provider_error_code=error_code,
                run_outcome=scenario.run_outcome,
                verified_success=scenario.verified_success,
                benchmark_mode="offline"
                if settings.provider == "fake"
                else "ollama_local",
            )
        )
    return records


def run_benchmark(
    scenarios: list[ScenarioDefinition] | None = None,
    config: BenchmarkConfig | None = None,
) -> BenchmarkReport:
    """Run all (or supplied) scenarios and return an aggregate report."""

    settings = config or BenchmarkConfig()
    all_records = [
        record
        for scenario in scenarios or SCENARIOS
        for record in run_scenario_benchmark(scenario, settings)
    ]
    provider_costs = [
        record.provider_cost_micro_usd
        for record in all_records
        if record.provider_cost_micro_usd is not None
    ]
    energies = [
        record.energy_wh for record in all_records if record.energy_wh is not None
    ]
    return BenchmarkReport(
        benchmark_version="1.0.0",
        provider=settings.provider,
        model=settings.ollama_model
        if settings.provider == "ollama"
        else "deterministic",
        total_actions=len(all_records),
        completed_actions=sum(
            record.action_outcome == "completed" for record in all_records
        ),
        failed_actions=sum(record.action_outcome == "failed" for record in all_records),
        total_input_tokens=sum(record.input_tokens for record in all_records),
        total_output_tokens=sum(record.output_tokens for record in all_records),
        total_latency_ms=sum(record.latency_ms for record in all_records),
        total_energy_wh=sum(energies) if energies else None,
        total_virtual_cost_micro_usd=sum(
            record.virtual_cost_micro_usd for record in all_records
        ),
        total_provider_cost_micro_usd=sum(provider_costs) if provider_costs else None,
        records=tuple(all_records),
    )


def write_report(report: BenchmarkReport, output_dir: Path) -> dict[str, Any]:
    """Write JSONL records and a hashed report manifest."""

    output_dir.mkdir(parents=True, exist_ok=True)
    records_path = output_dir / "benchmark_records.jsonl"
    report_path = output_dir / "benchmark_report.json"
    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in report.records:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")
    digest = hashlib.sha256(records_path.read_bytes()).hexdigest()
    summary = report.to_dict()
    summary.pop("records", None)
    summary["records_file"] = records_path.name
    summary["records_sha256"] = digest
    report_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Cusp offline or local-Ollama benchmarks"
    )
    parser.add_argument("--provider", choices=("fake", "ollama"), default="fake")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--ollama-model", default="llama3.2")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("ml/benchmarks/results")
    )
    parser.add_argument("--wattage", type=float, default=65.0)
    args = parser.parse_args()
    config = BenchmarkConfig(
        provider=args.provider,
        ollama_base_url=args.ollama_url,
        ollama_model=args.ollama_model,
        host_wattage_watts=args.wattage,
    )
    summary = write_report(run_benchmark(config=config), args.output_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
