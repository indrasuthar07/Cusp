"""Unit tests for Phase 5 MCP proxy request and allowlist contracts."""

from __future__ import annotations

import pytest
from app.mcp.proxy import (
    MCPProxyError,
    parse_upstream_allowlist,
    validate_jsonrpc_request,
)


def test_allowlist_requires_explicit_http_upstream() -> None:
    registry = parse_upstream_allowlist("tools=https://mcp.example.test/rpc")
    assert registry["tools"].url == "https://mcp.example.test/rpc"
    with pytest.raises(MCPProxyError, match="HTTP or HTTPS"):
        parse_upstream_allowlist("tools=file:///etc/passwd")


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
