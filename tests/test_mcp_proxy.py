"""Unit tests for Phase 5 MCP proxy request and allowlist contracts."""

from __future__ import annotations

import httpx
import pytest
from app.mcp.proxy import (
    MCPProxyError,
    authorize_mcp_request,
    parse_upstream_allowlist,
    stream_mcp_request,
    validate_jsonrpc_request,
)
from app.mcp.redaction import canonical_json, redact_and_canonicalize


def test_allowlist_requires_explicit_http_upstream() -> None:
    registry = parse_upstream_allowlist("tools=https://mcp.example.test/rpc")
    assert registry["tools"].url == "https://mcp.example.test/rpc"
    with pytest.raises(MCPProxyError, match="HTTP or HTTPS"):
        parse_upstream_allowlist("tools=file:///etc/passwd")
    with pytest.raises(MCPProxyError, match="HTTP or HTTPS"):
        parse_upstream_allowlist("tools=https://user:password@mcp.example.test/rpc")


def test_allowlist_rejects_duplicates_and_malformed_entries() -> None:
    with pytest.raises(MCPProxyError, match="duplicate"):
        parse_upstream_allowlist("tools=https://one.test,tools=https://two.test")
    with pytest.raises(MCPProxyError, match="name=url"):
        parse_upstream_allowlist("https://missing-name.test")


def test_jsonrpc_supports_tools_and_context_methods() -> None:
    for method in ("tools/call", "prompts/get", "resources/read"):
        payload = validate_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": method}
        )
        assert payload["method"] == method


def test_jsonrpc_rejects_sampling_and_notifications() -> None:
    with pytest.raises(MCPProxyError, match="unsupported"):
        validate_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "sampling/createMessage"}
        )
    with pytest.raises(MCPProxyError, match="require an id"):
        validate_jsonrpc_request({"jsonrpc": "2.0", "method": "tools/call"})


def test_mcp_permissions_are_deny_by_default_and_tool_specific() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "secret.txt"}},
    }
    with pytest.raises(MCPProxyError, match="permission"):
        authorize_mcp_request({}, upstream_name="filesystem", payload=payload)
    scopes = {
        "mcp": {
            "upstreams": ["filesystem"],
            "methods": ["tools/call"],
            "tools": {"filesystem": ["read_*"]},
        }
    }
    authorize_mcp_request(scopes, upstream_name="filesystem", payload=payload)
    with pytest.raises(MCPProxyError, match="tool"):
        authorize_mcp_request(
            scopes,
            upstream_name="filesystem",
            payload={**payload, "params": {"name": "delete_file"}},
        )


def test_mcp_redaction_is_recursive_and_canonical() -> None:
    payload = {
        "params": {
            "arguments": {"path": "private.py", "token": "secret-value"},
            "name": "read_file",
        },
        "method": "tools/call",
    }
    safe = redact_and_canonicalize(payload)
    assert safe["params"]["arguments"] == "[REDACTED]"
    assert (
        canonical_json(payload)
        == '{"method":"tools/call","params":{"arguments":"[REDACTED]","name":"read_file"}}'
    )
    assert "secret-value" not in canonical_json(payload)


@pytest.mark.asyncio
async def test_streamable_proxy_forwards_only_jsonrpc_payload_and_not_bearer_token() -> (
    None
):
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers.get("authorization")
        observed["payload"] = request.content
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": 7, "result": {"content": "ok"}},
        )

    payload = validate_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "x"}},
        }
    )
    authorize_mcp_request(
        {
            "mcp": {
                "upstreams": ["filesystem"],
                "methods": ["tools/call"],
                "tools": {"filesystem": ["read_file"]},
            }
        },
        upstream_name="filesystem",
        payload=payload,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status_code, media_type, stream = await stream_mcp_request(
        parse_upstream_allowlist("filesystem=https://mcp.example.test/rpc")[
            "filesystem"
        ],
        payload,
        timeout_seconds=1,
        client=client,
    )
    response_body = b"".join([chunk async for chunk in stream])
    await client.aclose()
    assert status_code == 200
    assert media_type == "application/json"
    assert observed["authorization"] is None
    assert b'"method":"tools/call"' in observed["payload"]  # type: ignore[operator]
    assert b'"result"' in response_body
