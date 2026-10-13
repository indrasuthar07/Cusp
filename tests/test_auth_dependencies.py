"""
Unit tests for FastAPI auth dependencies and schemas.

Tests verify:
  1. Header extraction (_extract_bearer_token)
  2. Agent authentication dependency (get_current_agent)
  3. User session dependency (get_current_user)
  4. Role-based access control guard (require_role)
  5. Pydantic request/response schemas
  6. Router registration in the main FastAPI application
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app.auth.dependencies import (
    AgentIdentity,
    UserIdentity,
    _extract_bearer_token,
    get_current_agent,
    get_current_user,
    require_role,
)
from app.auth.oidc import OIDCClaims
from app.auth.schemas import (
    AgentInfoResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    RevokeTokenResponse,
    TokenListResponse,
    TokenSummary,
    UserInfoResponse,
)
from app.db.models import MembershipRole
from app.main import app


def _make_mock_request(headers: dict[str, str] | None = None) -> Request:
    """Create a mock Starlette Request with the specified headers."""
    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


# 1. Bearer token extraction 
def test_extract_bearer_token_success():
    req = _make_mock_request({"Authorization": "Bearer cusp_secret_token_123"})
    assert _extract_bearer_token(req) == "cusp_secret_token_123"


def test_extract_bearer_token_missing():
    req = _make_mock_request({})
    assert _extract_bearer_token(req) is None


def test_extract_bearer_token_non_bearer():
    req = _make_mock_request({"Authorization": "Basic dXNlcjpwYXNz"})
    assert _extract_bearer_token(req) is None


def test_extract_bearer_token_whitespace():
    req = _make_mock_request({"Authorization": "Bearer   cusp_token_with_spaces  "})
    assert _extract_bearer_token(req) == "cusp_token_with_spaces"


# 2. get_current_agent dependency 
@pytest.mark.asyncio
async def test_get_current_agent_missing_header():
    req = _make_mock_request({})
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_agent(req, db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Missing or malformed Authorization header" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_agent_invalid_token():
    req = _make_mock_request({"Authorization": "Bearer cusp_invalid_token"})
    db = AsyncMock()

    with patch("app.auth.dependencies.validate_service_token", new_callable=AsyncMock) as mock_val:
        mock_val.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_agent(req, db)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid, expired, or revoked" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_agent_success():
    req = _make_mock_request({"Authorization": "Bearer cusp_valid_token"})
    db = AsyncMock()

    token_row = MagicMock()
    token_row.id = 10
    token_row.public_id = uuid.uuid4()
    token_row.scopes = {"actions": ["read", "write"]}

    agent = MagicMock()
    agent.id = 20
    agent.public_id = uuid.uuid4()
    agent.name = "Test Agent"

    org = MagicMock()
    org.id = 30
    org.public_id = uuid.uuid4()
    org.name = "Acme Corp"

    agent.organization = org
    token_row.agent = agent

    with patch("app.auth.dependencies.validate_service_token", new_callable=AsyncMock) as mock_val:
        mock_val.return_value = token_row

        identity = await get_current_agent(req, db)

        assert isinstance(identity, AgentIdentity)
        assert identity.token_id == 10
        assert identity.token_public_id == token_row.public_id
        assert identity.agent_id == 20
        assert identity.agent_name == "Test Agent"
        assert identity.org_id == 30
        assert identity.org_name == "Acme Corp"
        assert identity.scopes == {"actions": ["read", "write"]}


# 3. get_current_user dependency
@pytest.mark.asyncio
async def test_get_current_user_missing_header():
    req = _make_mock_request({})
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(req, db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Missing OIDC identity token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_legacy_user_header_is_rejected():
    req = _make_mock_request({"X-Cusp-User-Id": "1"})
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(req, db)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_no_membership():
    req = _make_mock_request({"Authorization": "Bearer oidc-token"})
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    db.execute.return_value = exec_result

    with patch("app.auth.dependencies.get_settings") as settings, patch(
        "app.auth.dependencies.verify_oidc_token", new_callable=AsyncMock
    ) as verify:
        settings.return_value.oidc_issuer_url = "https://issuer.example"
        settings.return_value.oidc_jwks_url = "https://issuer.example/jwks"
        settings.return_value.oidc_audience = "cusp-dashboard"
        verify.return_value = OIDCClaims("subject-42", "u@example.com", "User")
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(req, db)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "no organization membership" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_success():
    req = _make_mock_request({"Cookie": "cusp_oidc_id_token=oidc-token"})
    db = AsyncMock()

    user_obj = MagicMock()
    user_obj.id = 1
    user_obj.public_id = uuid.uuid4()
    user_obj.email = "admin@example.com"
    user_obj.display_name = "Admin User"

    org_obj = MagicMock()
    org_obj.id = 5
    org_obj.public_id = uuid.uuid4()

    membership_obj = MagicMock()
    membership_obj.user = user_obj
    membership_obj.organization = org_obj
    membership_obj.role = MembershipRole.owner

    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [membership_obj]
    db.execute.return_value = exec_result

    with patch("app.auth.dependencies.get_settings") as settings, patch(
        "app.auth.dependencies.verify_oidc_token", new_callable=AsyncMock
    ) as verify:
        settings.return_value.oidc_issuer_url = "https://issuer.example"
        settings.return_value.oidc_jwks_url = "https://issuer.example/jwks"
        settings.return_value.oidc_audience = "cusp-dashboard"
        verify.return_value = OIDCClaims("subject-1", "admin@example.com", "Admin User")
        user_obj.oidc_subject = "subject-1"
        user_identity = await get_current_user(req, db)

    assert isinstance(user_identity, UserIdentity)
    assert user_identity.user_id == 1
    assert user_identity.email == "admin@example.com"
    assert user_identity.org_id == 5
    assert user_identity.role == MembershipRole.owner


# 4. require_role guard 
@pytest.mark.asyncio
async def test_require_role_allowed():
    guard = require_role(MembershipRole.owner, MembershipRole.admin)
    user = UserIdentity(
        user_id=1,
        user_public_id=uuid.uuid4(),
        email="owner@test.com",
        display_name="Owner",
        org_id=10,
        org_public_id=uuid.uuid4(),
        role=MembershipRole.owner,
    )

    result = await guard(user=user)
    assert result == user


@pytest.mark.asyncio
async def test_require_role_denied():
    guard = require_role(MembershipRole.owner, MembershipRole.admin)
    user = UserIdentity(
        user_id=2,
        user_public_id=uuid.uuid4(),
        email="viewer@test.com",
        display_name="Viewer",
        org_id=10,
        org_public_id=uuid.uuid4(),
        role=MembershipRole.viewer,
    )

    with pytest.raises(HTTPException) as exc_info:
        await guard(user=user)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "Requires one of roles" in exc_info.value.detail


# 5. Schemas validation 
def test_create_token_schemas():
    agent_id = uuid.uuid4()
    req = CreateTokenRequest(agent_public_id=agent_id, label="CI Runner")
    assert req.agent_public_id == agent_id
    assert req.label == "CI Runner"
    assert req.scopes == {}

    token_id = uuid.uuid4()
    from datetime import datetime, timezone
    resp = CreateTokenResponse(
        token_public_id=token_id,
        raw_token="cusp_raw123",
        label="CI Runner",
        scopes={"actions": ["read"]},
        created_at=datetime.now(timezone.utc),
    )
    assert resp.raw_token == "cusp_raw123"


# 6. App route registration 
def test_routes_registered_in_app():
    openapi_schema = app.openapi()
    paths = set(openapi_schema["paths"].keys())
    assert "/health" in paths
    assert "/api/v1/auth/tokens" in paths
    assert "/api/v1/auth/tokens/revoke" in paths
    assert "/api/v1/auth/me/agent" in paths
    assert "/api/v1/auth/me/user" in paths