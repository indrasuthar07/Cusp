# Cusp

When you let AI coding agents run autonomously, they can sometimes get stuck in loops, trying the same things over and over, or silently burning through your API budget without making any real progress. 

Cusp is a smart proxy that sits between your coding agent and LLM providers (like Ollama, Gemini, or OpenAI). Before your agent takes its next action, Cusp steps in and asks: *"Is this move actually worth the cost?"* 

By analyzing the agent's recent actions, code diffs, and test results, Cusp can instantly detect when an agent is spinning its wheels. It will intercept the call, pause the agent, or safely downgrade the request to a cheaper local model. It ensures you only pay for compute when it's actively solving your problem.

## Quick Start

We prioritize a free-first local workflow, meaning you can run Cusp completely locally without needing paid API keys.   

### 1. Prerequisites

- Git
- Docker Desktop (running, with the Compose plugin)
- Optional for local checks and benchmarks: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22, and pnpm (`corepack enable`)

### 2. Clone the repo and prepare development settings

```powershell
git clone <your-repository-url> Cusp
Set-Location Cusp
Copy-Item .env.example .env
```

The checked-in Compose file uses safe local-only defaults and does not need a
hosted provider key. Keep `CUSP_HOSTED_EGRESS_ALLOWED=false` unless you have
explicitly configured and approved a hosted provider.

### 3. Start the local services

```powershell
docker compose up --build -d
```  

This starts PostgreSQL, Redis, the gateway (including Alembic migrations), the
background worker, and the React dashboard.

### 4. Verify the stack

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health/liveness
Invoke-RestMethod http://localhost:8000/health
Invoke-WebRequest http://localhost:5173 -UseBasicParsing | Select-Object StatusCode
docker compose exec gateway alembic -c alembic.ini current
```

Expected results are healthy PostgreSQL and Redis containers, a `200` dashboard
response, gateway responses with `status: ok`, and the latest Alembic revision.
For troubleshooting, use `docker compose logs -f gateway worker`.

### 5. Run local quality checks and the offline ML benchmark (optional)

```powershell
uv sync --all-packages --all-groups --extra ml
$env:CUSP_ENV = "test"
$env:CUSP_SESSION_SECRET = "test-session-secret"
$env:CUSP_TOKEN_PEPPER = "test-token-pepper"
uv run ruff check .
uv run pyright
uv run pytest -q

Set-Location apps/dashboard
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm build
Set-Location ../..

uv run python ml/run_benchmark.py --provider fake
uv run python ml/split_dataset.py `
  --features ml/benchmarks/results/feature_vectors.jsonl `
  --labels ml/benchmarks/results/labels.jsonl `
  --output-dir ml/benchmarks/splits
uv run python ml/train_model.py `
  --split-dir ml/benchmarks/splits `
  --output-dir ml/benchmarks/model
```

The fake benchmark is offline and needs no hosted API key. The resulting
`ml/benchmarks/model/held_out_evaluation_report.json` records held-out metrics,
split leakage checks, cost evidence, and hot-path inference latency. See
[`docs/local_benchmarker.md`](docs/local_benchmarker.md) for optional local
Ollama and shadow-mode setup.

Stop services with `docker compose down`. This preserves the local database and
Redis volumes. Add `-v` only when you intentionally want to delete local data.
