"""Safe Streamable HTTP MCP forwarding primitives."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

UPSTREAM_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
SUPPORTED_METHODS = frozenset({"tools/call", "prompts/get", "resources/read"})


class MCPProxyError(ValueError):
    """Client-safe proxy configuration or request error."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class MCPUpstream:
    name: str
    url: str


def parse_upstream_allowlist(value: str) -> dict[str, MCPUpstream]:
    """Parse ``name=https://host/path`` entries into an immutable registry.

    Only explicitly configured HTTP(S) URLs are accepted. Caller bearer tokens
    are never part of this configuration and are never forwarded upstream.
    """

    result: dict[str, MCPUpstream] = {}
    for raw_entry in value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        name, separator, raw_url = entry.partition("=")
        if not separator or not UPSTREAM_NAME.fullmatch(name.strip()):
            raise MCPProxyError(
                "invalid_mcp_allowlist", "MCP allowlist entries must be name=url"
            )
        url = raw_url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MCPProxyError(
                "invalid_mcp_allowlist", "MCP upstream URLs must use HTTP or HTTPS"
            )
        key = name.strip()
        if key in result:
            raise MCPProxyError("invalid_mcp_allowlist", "duplicate MCP upstream name")
        result[key] = MCPUpstream(key, url)
    return result


def validate_jsonrpc_request(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise MCPProxyError("invalid_mcp_request", "MCP request must be a JSON object")
    if payload.get("jsonrpc") != "2.0":
        raise MCPProxyError("invalid_mcp_request", "MCP request must use JSON-RPC 2.0")
    method = payload.get("method")
    if method not in SUPPORTED_METHODS:
        raise MCPProxyError(
            "unsupported_mcp_method",
            "MCP method is unsupported by this proxy",
            status_code=501,
        )
    if "id" not in payload:
        raise MCPProxyError("invalid_mcp_request", "MCP requests require an id")
    return payload


async def stream_mcp_request(
    upstream: MCPUpstream,
    payload: dict,
    *,
    timeout_seconds: float,
) -> tuple[int, str, AsyncIterator[bytes]]:
    """Open a response stream without forwarding caller credentials."""

    client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        stream_context = client.stream(
            "POST",
            upstream.url,
            json=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        response = await stream_context.__aenter__()
    except (httpx.HTTPError, OSError) as exc:
        await client.aclose()
        raise MCPProxyError(
            "mcp_upstream_unavailable", "MCP upstream is unavailable", status_code=502
        ) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    media_type = response.headers.get("content-type", "application/json").split(";", 1)[
        0
    ]
    return response.status_code, media_type, body()
