# Provider guide

## Offline-first profiles

The fake adapter is deterministic and requires no key. Ollama uses the local `/api/tags` and `/api/chat` APIs and reports virtual cost. Configure the local model and endpoint with `OLLAMA_BASE_URL` and `OLLAMA_MODEL_PROFILE`.

## Hosted profiles

Hosted adapters are optional. Supply the relevant key only when the profile is explicitly enabled and hosted-egress acknowledgement is configured. Gemini uses the official Generative Language REST API; Groq, Hugging Face Router, and OpenAI use the normalized OpenAI-compatible adapter; Anthropic uses its Messages API headers and response format.

Every hosted profile must declare `hosted_consent_required`, a route group, capabilities, and provider-reported cost semantics. HTTP 429 responses map to `provider_quota_exhausted` so governance can pause instead of retrying forever. Credentials are constructor inputs or environment secrets only and are never included in normalized responses.

## Capability checks

Call `validate_profile` before registering a profile and `health` before starting a provider run. An adapter rejects tool or structured-output requests that the selected profile does not advertise. Cross-provider downgrade is allowed only when policy explicitly selects a compatible route group.
