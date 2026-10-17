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

Energy is an estimate calculated from `--wattage` and action latency. Pass
`--wattage 0` to report energy as unavailable. This estimate must not be
presented as measured hardware telemetry.
