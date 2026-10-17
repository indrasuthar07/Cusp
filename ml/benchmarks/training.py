"""XGBoost stopping-risk training and calibrated evaluation."""

# Optional dependencies are imported lazily so offline and gateway installs do
# not need the ML stack.  The runtime raises a clear install instruction.
# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import json
import pickle
import time
from pathlib import Path
from typing import Any

from ml.benchmarks.splitting import validate_split_manifest

HOT_PATH_TARGET_MS = 10.0


class TrainingDependencyError(RuntimeError):
    """Raised when the optional ML dependencies are not installed."""


def train_stopping_model(split_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Train XGBoost, calibrate on validation data, and report held-out metrics."""

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            accuracy_score,
            brier_score_loss,
            confusion_matrix,
            precision_score,
            recall_score,
        )
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise TrainingDependencyError(
            "Task 7.6 requires optional ML dependencies; run uv sync --extra ml"
        ) from exc

    train = _load(split_dir, "train")
    validation = _load(split_dir, "validation")
    test = _load(split_dir, "test")
    leakage = _verify_held_out_split(split_dir, train, test)
    feature_names = _feature_names(train[0][0])
    vocab = _build_vocab(train + validation + test, feature_names)
    x_train, y_train = _matrix(train, feature_names, vocab)
    x_validation, y_validation = _matrix(validation, feature_names, vocab)
    x_test, y_test = _matrix(test, feature_names, vocab)
    if len(set(y_train)) < 2 or len(set(y_validation)) < 2:
        raise ValueError("train and validation splits must contain both label classes")

    model = XGBClassifier(
        n_estimators=80,
        max_depth=3,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=20261017,
        n_jobs=1,
    )
    model.fit(x_train, y_train)
    validation_raw = model.predict_proba(x_validation)[:, 1]
    calibrator = LogisticRegression(random_state=20261017)
    calibrator.fit(validation_raw.reshape(-1, 1), y_validation)
    inference_started = time.perf_counter()
    probabilities = calibrator.predict_proba(
        model.predict_proba(x_test)[:, 1].reshape(-1, 1)
    )[:, 1]
    inference_latency_ms = (time.perf_counter() - inference_started) * 1000
    predicted = (probabilities >= 0.5).astype(int)
    baseline = np.array(
        [int(row[1]["policy_verdict"] in {"pause", "kill"}) for row in test]
    )
    learned_metrics = _classification_metrics(
        y_test,
        predicted,
        probabilities,
        precision_score,
        recall_score,
        accuracy_score,
        brier_score_loss,
        confusion_matrix,
    )
    deterministic_metrics = _classification_metrics(
        y_test,
        baseline,
        baseline.astype(float),
        precision_score,
        recall_score,
        accuracy_score,
        brier_score_loss,
        confusion_matrix,
    )
    comparison = {
        "learned": learned_metrics,
        "deterministic": deterministic_metrics,
        "recommendation_agreement_rate": float((predicted == baseline).mean()),
        "recommendation_disagreement_count": int((predicted != baseline).sum()),
        "cost_impact": _impact(test, predicted, baseline),
        "latency": {
            **_latency(test),
            "inference_latency_ms": inference_latency_ms,
            "hot_path_target_ms": HOT_PATH_TARGET_MS,
            "meets_hot_path_target": inference_latency_ms <= HOT_PATH_TARGET_MS,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / "stopping_model.pkl"
    artifact.write_bytes(
        pickle.dumps(
            {
                "model": model,
                "calibrator": calibrator,
                "vocab": vocab,
                "feature_names": feature_names,
            }
        )
    )
    report = {
        "training_schema_version": "1.0.0",
        "feature_schema_version": str(train[0][0]["feature_schema_version"]),
        "label_schema_version": "1.0.0",
        "model_type": "xgboost_binary_logistic",
        "calibration": "logistic_platt_on_validation",
        "feature_names": feature_names,
        "held_out": True,
        "selection": {
            "used_for_model_selection": False,
            "note": "The held-out test split is used only for final evaluation.",
        },
        "split_integrity": leakage,
        "comparison": comparison,
        # Retained for registry compatibility with existing registrations.
        "metrics": comparison,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "test_rows": len(test),
    }
    (output_dir / "evaluation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "held_out_evaluation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _load(directory: Path, split: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    features = _read(directory / f"{split}_features.jsonl")
    labels = _read(directory / f"{split}_labels.jsonl")
    if len(features) != len(labels):
        raise ValueError(f"{split} feature/label rows differ")
    return list(zip(features, labels, strict=True))


def _read(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _feature_names(row: dict[str, Any]) -> list[str]:
    return sorted(
        k
        for k, v in row.items()
        if k not in {"scenario_id", "task_id", "sequence", "feature_schema_version"}
        and isinstance(v, (bool, int, float, type(None), str))
    )


def _build_vocab(
    rows: list[tuple[dict[str, Any], dict[str, Any]]], names: list[str]
) -> dict[str, dict[str, int]]:
    return {
        name: {
            value: index
            for index, value in enumerate(
                sorted({str(row[0].get(name, "")) for row in rows})
            )
        }
        for name in names
    }


def _matrix(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    names: list[str],
    vocab: dict[str, dict[str, int]],
) -> tuple[Any, list[int]]:
    import numpy as np

    matrix = [
        [float(vocab[name][str(row.get(name, ""))]) for name in names]
        for row, _ in rows
    ]
    labels = [int(label["stop_recommended"]) for _, label in rows]
    return np.asarray(matrix, dtype=float), np.asarray(labels, dtype=int)


def _classification_metrics(
    labels: Any,
    predicted: Any,
    probabilities: Any,
    precision_score: Any,
    recall_score: Any,
    accuracy_score: Any,
    brier_score_loss: Any,
    confusion_matrix: Any,
) -> dict[str, Any]:
    matrix = confusion_matrix(labels, predicted).tolist()
    false_stops = matrix[0][1] if len(matrix) == 2 and len(matrix[0]) == 2 else 0
    negative_count = int((labels == 0).sum())
    return {
        "precision": float(precision_score(labels, predicted, zero_division=0)),
        "recall": float(recall_score(labels, predicted, zero_division=0)),
        "accuracy": float(accuracy_score(labels, predicted)),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "expected_calibration_error": _expected_calibration_error(labels, probabilities),
        "false_stop_rate": false_stops / negative_count if negative_count else 0.0,
        "confusion_matrix": matrix,
    }


def _expected_calibration_error(labels: Any, probabilities: Any, bins: int = 10) -> float:
    total = len(labels)
    if not total:
        return 0.0
    error = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (probabilities >= lower) & (
            probabilities < upper if index < bins - 1 else probabilities <= upper
        )
        count = int(mask.sum())
        if count:
            error += abs(float(labels[mask].mean()) - float(probabilities[mask].mean())) * count / total
    return float(error)


def _impact(
    rows: list[tuple[dict[str, Any], dict[str, Any]]], predicted: Any, baseline: Any
) -> dict[str, Any]:
    projected_virtual = [
        float(row[0].get("estimated_cost_micro_usd", 0)) for row in rows
    ]
    provider_costs = [row[0].get("provider_cost_micro_usd") for row in rows]
    has_provider_cost = any(value is not None for value in provider_costs)

    def avoided_provider_spend(stops: Any) -> int | None:
        # Provider spend is evidence only when every proposed stopped action has
        # a provider-reported cost.  Treating an unknown charge as zero would
        # overstate a real spend reduction.
        if not has_provider_cost:
            return None
        selected = [
            cost for cost, stop in zip(provider_costs, stops, strict=True) if stop
        ]
        if not selected:
            return 0
        if any(cost is None for cost in selected):
            return None
        return sum(int(cost) for cost in selected)

    learned_provider_spend = avoided_provider_spend(predicted)
    deterministic_provider_spend = avoided_provider_spend(baseline)
    return {
        "learned_projected_virtual_cost_avoided_micro_usd": sum(
            cost for cost, stop in zip(projected_virtual, predicted, strict=True) if stop
        ),
        "deterministic_projected_virtual_cost_avoided_micro_usd": sum(
            cost for cost, stop in zip(projected_virtual, baseline, strict=True) if stop
        ),
        "actual_provider_spend_reduction_available": has_provider_cost,
        "learned_actual_provider_spend_avoided_micro_usd": learned_provider_spend,
        "deterministic_actual_provider_spend_avoided_micro_usd": deterministic_provider_spend,
        "actual_provider_spend_reduction_complete": {
            "learned": learned_provider_spend is not None,
            "deterministic": deterministic_provider_spend is not None,
        },
    }


def _latency(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, float]:
    values = [float(row[0].get("latency_ms", 0)) for row in rows]
    return {"test_mean_latency_ms": sum(values) / len(values) if values else 0.0}


def _verify_held_out_split(
    split_dir: Path,
    train: list[tuple[dict[str, Any], dict[str, Any]]],
    test: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    manifest_path = split_dir / "split_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("split_manifest.json is required for held-out evaluation")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_split_manifest(manifest)
    group_field = manifest.get("group_field")
    if group_field not in {"task_id", "repository_id"}:
        raise ValueError("held-out split must be grouped by task_id or repository_id")
    train_groups = {str(feature[group_field]) for feature, _ in train}
    test_groups = {str(feature[group_field]) for feature, _ in test}
    overlap = sorted(train_groups & test_groups)
    if overlap:
        raise ValueError(f"held-out leakage detected: {overlap}")
    return {
        "passed": True,
        "group_field": group_field,
        "train_test_overlap": overlap,
        "train_groups": sorted(train_groups),
        "test_groups": sorted(test_groups),
    }
