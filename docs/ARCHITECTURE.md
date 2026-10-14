# Cusp architecture

Cusp has four stable boundaries: the SDK controls provider invocation, the gateway resolves deterministic policy and active budgets, Redis Streams carry redacted durable events, and PostgreSQL stores the ledger. Fake and Ollama providers are offline-first; hosted adapters require explicit egress consent.

The preflight hot path validates tenant ownership, evaluates policy, reserves estimated budget atomically, and returns an action-bound signed decision. Completion reconciles actual usage exactly once. Semantic embeddings, dashboard feeds, and future ML inference are deliberately outside the deterministic foundation and belong to later phases.
