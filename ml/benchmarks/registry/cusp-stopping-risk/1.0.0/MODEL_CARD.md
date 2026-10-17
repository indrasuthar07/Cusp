# Model card: cusp-stopping-risk 1.0.0

## Intended use

Shadow-mode stopping-risk recommendations only

## Training and evaluation data

- Feature schema: `1.0.0`
- Label schema: `1.0.0`
- Split schema: `1.0.0`
- Group field: `task_id`
- Split seed: `20261017`
- Row counts: `{"test": 29, "train": 47, "validation": 15}`

## Model and metrics

- Type: `xgboost_binary_logistic`
- Calibration: `logistic_platt_on_validation`
- Metrics: `{"accuracy": 0.896551724137931, "baseline_confusion_matrix": [[26, 0], [0, 3]], "baseline_precision": 1.0, "baseline_recall": 1.0, "brier_score": 0.09384380280971527, "confusion_matrix": [[26, 0], [3, 0]], "cost_impact": {"baseline_stop_cost_micro_usd": 7000.0, "predicted_stop_cost_micro_usd": 0}, "latency_ms": {"inference_latency_ms": 0.8762999786995351, "test_mean_latency_ms": 351.7241379310345}, "precision": 0.0, "recall": 0.0}`

## Limitations and safety

- Not approved to change execution outcomes.
- Evaluation is limited to task-disjoint deterministic scenarios.
- Provider and repository distributions may not represent production traffic.
- Shadow-only: learned predictions must not change action outcomes.

## Reproducibility

- Git commit: `68de9bf883dfceb1684f9fd6e720d747d83b1478`
- Git dirty: `True`
- Runtime: `3.13.13` on `Windows-11-10.0.26300-SP0`
- All registered artifacts are listed with SHA-256 hashes in `registration.json`.
