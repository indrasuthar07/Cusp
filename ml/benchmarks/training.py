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
    metrics = {
        "precision": float(precision_score(y_test, predicted, zero_division=0)),
        "recall": float(recall_score(y_test, predicted, zero_division=0)),
        "accuracy": float(accuracy_score(y_test, predicted)),
        "brier_score": float(brier_score_loss(y_test, probabilities)),
        "confusion_matrix": confusion_matrix(y_test, predicted).tolist(),
        "baseline_precision": float(precision_score(y_test, baseline, zero_division=0)),
        "baseline_recall": float(recall_score(y_test, baseline, zero_division=0)),
        "baseline_confusion_matrix": confusion_matrix(y_test, baseline).tolist(),
        "cost_impact": _impact(test, predicted, baseline),
        "latency_ms": {
            **_latency(test),
            "inference_latency_ms": inference_latency_ms,
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
        "feature_schema_version": "1.0.0",
        "label_schema_version": "1.0.0",
        "model_type": "xgboost_binary_logistic",
        "calibration": "logistic_platt_on_validation",
        "feature_names": feature_names,
        "metrics": metrics,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "test_rows": len(test),
    }
    (output_dir / "evaluation_report.json").write_text(
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
    return np.asarray(matrix, dtype=float), labels


def _impact(
    rows: list[tuple[dict[str, Any], dict[str, Any]]], predicted: Any, baseline: Any
) -> dict[str, float]:
    costs = [float(row[0].get("estimated_cost_micro_usd", 0)) for row in rows]
    return {
        "predicted_stop_cost_micro_usd": sum(
            c for c, stop in zip(costs, predicted, strict=True) if stop
        ),
        "baseline_stop_cost_micro_usd": sum(
            c for c, stop in zip(costs, baseline, strict=True) if stop
        ),
    }


def _latency(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, float]:
    values = [float(row[0].get("latency_ms", 0)) for row in rows]
    return {"test_mean_latency_ms": sum(values) / len(values) if values else 0.0}
