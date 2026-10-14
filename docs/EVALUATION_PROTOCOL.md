# Evaluation protocol

Before ML is allowed to affect execution, compare four modes on task-disjoint runs: no governance, fixed budget, deterministic exact-repeat/progress governance, and ML shadow recommendations. Keep benchmark tasks and provider profiles separated between training and evaluation.

Report completion rate, false-stop rate, virtual-cost reduction, provider-reported cost, latency, quota/error behavior, energy quality, calibration, and provider/task composition. Include successful, no-progress, repeated-test-after-diff, provider-timeout, quota-exhaustion, and Ollama-unavailable scenarios. A shadow model must never alter the SDK decision until calibration and rollback criteria are reviewed.
