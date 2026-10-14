# Cusp security model

## Trust boundaries

- Agent service tokens authenticate agents and are scoped to one organization.
- OIDC authenticates dashboard users; membership and role checks are resolved server-side.
- Provider adapters own provider credentials. Keys are never returned by API models or written to events.
- Redis is active state; PostgreSQL is the durable ledger. Neither is exposed by the Compose host ports outside local development.

## Privacy classes

`offline_private` profiles (fake and Ollama) do not send prompts outside the local deployment. Hosted profiles are `hosted_consent_required`; they cannot be enabled unless the organization explicitly acknowledges hosted egress. Raw prompts, source code, bearer tokens, and provider keys must not be stored in logs or audit payloads.

## Threat model

Protected assets are provider credentials, source code, budget state, policy decisions, organization isolation, and the append-only audit trail. Threats include token replay, cross-tenant identifier substitution, prompt leakage to a hosted provider, Redis/worker restart loss, duplicate charging, provider quota loops, and forged control/decision messages.

Mitigations include signed action-bound decision tokens, tenant-scoped queries, HMAC fingerprints instead of raw content, atomic Redis Lua reservations, durable retry metadata, redacted DLQ entries, bounded provider retries, and fail-safe pause behavior when caps or consent cannot be verified.

## Fail-safe rules

- A missing or invalid policy pauses a cost-bearing action.
- A hosted action without egress consent pauses.
- Quota exhaustion pauses or downgrades only when an explicit safe profile is configured.
- Redis reservation failure never grants an unbounded hosted action.
- Missing telemetry is `unknown`, never silently zero.
- Statistical or future ML signals may recommend pause, but deterministic policy remains authoritative until guarded enforcement is enabled.
