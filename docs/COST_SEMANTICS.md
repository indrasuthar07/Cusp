# Cost and energy semantics

All money is integer micro-USD (`1 USD = 1,000,000 micro-USD`). Floating point values are not used for reservations or reconciliation.

| Source | Meaning | Applies to |
|---|---|---|
| `virtual` | Local-model accounting estimate; no provider bill exists | Fake and Ollama |
| `catalog_estimate` | Estimate calculated from the configured pricing catalogue | Profiles without provider-reported billing |
| `provider_reported` | Cost returned by a hosted provider, when available | Gemini, Groq, Hugging Face, OpenAI, Anthropic |
| `unknown` | Cost was not available; must be surfaced as unknown | Failure or incomplete telemetry |

Ollama usage also records latency and optional energy metrics. Energy is `null` when unavailable; zero means measured zero consumption and must not be used as a missing-value sentinel. Virtual cost and provider-reported cost are stored separately so local experiments cannot be presented as billed savings.
