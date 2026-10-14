"""End-to-end Phase 5 MCP and semantic-loop contract scenario."""

from __future__ import annotations

from app.governance.engine import (
    DecisionTokenSigner,
    GovernanceContext,
    PolicyResolver,
    ProgressSignal,
)
from app.mcp.proxy import (
    authorize_mcp_request,
    parse_upstream_allowlist,
    validate_jsonrpc_request,
)
from app.mcp.redaction import canonical_json

from services.worker.app.embedding import DeterministicEmbedder, cosine_similarity


def test_allowed_mcp_retry_becomes_redacted_semantic_pause() -> None:
    payload = validate_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "run_tests",
                "arguments": {"command": "pytest", "token": "never-store"},
            },
        }
    )
    upstream = parse_upstream_allowlist("devtools=https://mcp.example.test/rpc")[
        "devtools"
    ]
    authorize_mcp_request(
        {
            "mcp": {
                "upstreams": [upstream.name],
                "methods": ["tools/call"],
                "tools": {"devtools": ["run_tests"]},
            }
        },
        upstream_name=upstream.name,
        payload=payload,
    )

    safe_event = canonical_json(payload)
    assert "never-store" not in safe_event
    assert '"method":"tools/call"' in safe_event

    embedder = DeterministicEmbedder(32)
    similarity = cosine_similarity(
        embedder.embed("run tests"), embedder.embed("run tests")
    )
    resolver = PolicyResolver(
        {
            "default_action": "allow",
            "loop_detection": {
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 1,
            },
        },
        policy_version=1,
        signer=DecisionTokenSigner("s" * 32),
    )
    decision = resolver.resolve(
        GovernanceContext(
            action_id="phase5-integration",
            provider_profile="fake",
            estimated_cost_micro_usd=0,
            progress=ProgressSignal(
                semantic_similarity=similarity,
                embedding_available=True,
            ),
        )
    )
    assert decision.verdict == "pause"
    assert decision.reason_code == "semantic_repeat_no_progress"
