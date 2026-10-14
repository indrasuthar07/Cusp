"""Authenticated Streamable HTTP MCP proxy endpoints."""

from __future__ import annotations

from app.auth.dependencies import AgentIdentity, get_current_agent
from app.config.settings import get_settings
from app.mcp.proxy import (
    MCPProxyError,
    parse_upstream_allowlist,
    stream_mcp_request,
    validate_jsonrpc_request,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


@router.post("/{upstream_name}")
async def proxy_mcp(
    upstream_name: str,
    request: Request,
    agent: AgentIdentity = Depends(get_current_agent),
) -> StreamingResponse:
    """Forward an approved MCP JSON-RPC request to one configured upstream.

    The upstream URL comes only from the server allowlist. The agent bearer
    token is used for Cusp authentication and is deliberately not forwarded.
    """

    del agent  # Identity enforcement is performed by the dependency.
    try:
        upstreams = parse_upstream_allowlist(get_settings().cusp_mcp_upstream_allowlist)
        upstream = upstreams.get(upstream_name)
        if upstream is None:
            raise MCPProxyError(
                "mcp_upstream_not_allowed",
                "MCP upstream is not allowed",
                status_code=403,
            )
        payload = validate_jsonrpc_request(await request.json())
        status_code, media_type, body = await stream_mcp_request(
            upstream,
            payload,
            timeout_seconds=get_settings().cusp_mcp_timeout_seconds,
        )
    except MCPProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_mcp_request") from exc
    return StreamingResponse(body, status_code=status_code, media_type=media_type)
