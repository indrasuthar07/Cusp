# MCP integration guide

The gateway exposes an authenticated Streamable HTTP proxy at
`POST /api/v1/mcp/{upstream_name}`. The upstream name must be present in
`CUSP_MCP_UPSTREAM_ALLOWLIST`, whose format is a comma-separated list such as:

```text
filesystem=https://mcp.example.test/rpc,issues=https://issues.example.test/mcp
```

Only HTTP(S) URLs without embedded credentials, query strings, or fragments are
accepted. The gateway never forwards the agent's Cusp bearer token to an MCP
upstream, and redirects are disabled.

Service-token MCP permissions are explicit and deny by default. A token must
carry a scope shaped like this:

```json
{
  "mcp": {
    "upstreams": ["filesystem"],
    "methods": ["tools/call", "resources/read"],
    "tools": {"filesystem": ["read_*", "list_directory"]}
  }
}
```

`tools/call` requires an exact or wildcard-matched tool grant for the selected
upstream. Context methods require an explicit method grant. Legacy
`sampling/createMessage` requests are rejected because direct model calls are
owned by the provider adapters.

The proxy forwards the request body only to the allowlisted upstream so the
upstream can execute the requested operation. Any event, audit, DLQ, or
fingerprint representation must use `app.mcp.redaction.redact_and_canonicalize`
or `canonical_json`; credential fields, prompts, source code, tool arguments,
and tool output are replaced before persistence. Canonical output sorts object
keys and uses stable compact JSON, making fingerprints deterministic without
storing raw user payloads.
