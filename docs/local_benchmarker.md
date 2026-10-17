# Local benchmark runner

Task 7.2 provides a reproducible benchmark that never requires a hosted API key.
The default fake-provider mode executes all 22 deterministic scenarios offline:

```powershell
uv run python ml/run_benchmark.py --provider fake
```

The command writes `benchmark_records.jsonl` and a summary manifest under
`ml/benchmarks/results` (or the directory supplied with `--output-dir`). Each
record contains the policy verdict and reason, action outcome, input/output
tokens, measured or fixture latency, energy estimate and quality, virtual cost,
provider-reported cost, provider error, and verified run outcome. Virtual and
provider-reported costs are deliberately separate; a missing provider cost is
`null`, never zero.

To run the same scenarios against a local Ollama instance, start Ollama and
make the model available first, then run:

```powershell
ollama serve
ollama pull llama3.2
uv run python ml/run_benchmark.py --provider ollama --ollama-model llama3.2
```

Ollama is the only network target in this runner. Hosted adapters are never
constructed. If Ollama is unavailable, records contain a bounded provider error
and the deterministic policy result remains visible; no secret is required.

Hosted profiles are intentionally opt-in and require both a key supplied through
an environment variable and an explicit consent flag. For example:

```powershell
$env:GROQ_API_KEY = "..."
uv run python ml/run_benchmark.py --provider groq --hosted-model llama-3.1-8b-instant `
  --api-key-env GROQ_API_KEY --hosted-egress-consent
```

Gemini and Hugging Face use the same pattern with `--provider gemini` or
`--provider huggingface`. The key is read only in memory, excluded from config
representation, and never written to records, manifests, or logs. Hosted
profiles are not used by offline tests.

Every report also exports `feature_vectors.jsonl`, `labels.jsonl`, and
`feature_label_manifest.json`. Feature vectors contain governance and semantic
signals only. Labels include deterministic policy outcomes and verified run
outcomes; agent-declared success is explicitly excluded from ground truth.

Create task-disjoint splits before training:

```powershell
uv run python ml/split_dataset.py `
  --features ml/benchmarks/results/feature_vectors.jsonl `
  --labels ml/benchmarks/results/labels.jsonl `
  --output-dir ml/benchmarks/splits
```

The split manifest records the seed, group assignments, row counts, source
hashes, and pairwise leakage checks. Re-running with the same seed and inputs is
idempotent. Train the optional calibrated XGBoost model with:

```powershell
uv sync --all-packages --all-groups --extra ml
uv run python ml/train_model.py `
  --split-dir ml/benchmarks/splits `
  --output-dir ml/benchmarks/model
```

Training fits XGBoost on train groups and Platt calibration on validation
groups. It then creates both `evaluation_report.json` and the explicit
`held_out_evaluation_report.json`. The held-out report records precision,
recall, Brier score, expected calibration error, confusion matrices,
deterministic-policy comparison, recommendation agreement, and inference
latency against the 10 ms hot-path target. It verifies the split manifest and
fails if a task or repository group appears in both train and test.

Virtual-cost avoidance is always reported as a projection. Actual provider
spend avoidance is reported separately and is `null` when a stopped action has
no provider-reported cost; missing provider charges are never converted to zero.
The ML extra is optional; offline benchmark and gateway tests do not install or
import it.

Register a trained run in the local immutable registry:

```powershell
uv run python ml/register_model.py `
  --registry-dir ml/benchmarks/registry `
  --model-version 1.0.0 `
  --model-artifact ml/benchmarks/model/stopping_model.pkl `
  --evaluation-report ml/benchmarks/model/evaluation_report.json `
  --split-manifest ml/benchmarks/splits/split_manifest.json `
  --feature-label-manifest ml/benchmarks/results/feature_label_manifest.json
```

Each version stores the model, reports, manifests, SHA-256 hashes, Git commit
and dirty-state metadata, runtime/package versions, split configuration, and a
`MODEL_CARD.md`. Versions are immutable: registering different content under an
existing version fails. The generated card marks the model as shadow-only and
documents intended use, metrics, training data, limitations, and reproducibility
information.

To enable shadow recommendations in a development gateway, point the gateway at
the registered artifact and explicitly enable shadow mode:

```powershell
$env:CUSP_SHADOW_MODE_ENABLED = "true"
$env:CUSP_SHADOW_MODEL_ARTIFACT = "C:\path\to\registry\cusp-stopping-risk\1.0.0\artifacts\stopping_model.pkl"
$env:CUSP_SHADOW_MODEL_NAME = "cusp-stopping-risk"
$env:CUSP_SHADOW_MODEL_VERSION = "1.0.0"
```

The gateway writes one recommendation per action to the shadow table and serves
them at `/api/v1/dashboard/shadow-recommendations`. Each row includes the model
recommendation, confidence, and deterministic verdict. `applied` is always
false in this phase; shadow inference failures are ignored so deterministic
preflight behavior remains unchanged.

Energy is an estimate calculated from `--wattage` and action latency. Pass
`--wattage 0` to report energy as unavailable. This estimate must not be
presented as measured hardware telemetry.
