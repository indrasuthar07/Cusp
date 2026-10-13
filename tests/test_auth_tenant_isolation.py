"""Route-level checks that resource lookup is always scoped to the caller tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.auth_routes import create_token, list_tokens, revoke_token
from app.auth.dependencies import UserIdentity
from app.auth.oidc import OIDCClaims
from app.auth.schemas import CreateTokenRequest
from app.db.models import MembershipRole
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql


def _user(org_id: int = 10) -> UserIdentity:
    return UserIdentity(
        user_id=1,
        user_public_id=uuid.uuid4(),
        email="owner@example.com",
        display_name="Owner",
        org_id=org_id,
        org_public_id=uuid.uuid4(),
        role=MembershipRole.owner,
    )


def _sql(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_create_token_filters_requested_agent_by_callers_org() -> None:
    db = AsyncMock()
    agent = MagicMock(id=22, public_id=uuid.uuid4(), name="agent", is_disabled=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    db.execute.return_value = result
    token = MagicMock(
        public_id=uuid.uuid4(), label="ci", scopes={}, created_at=datetime.now(UTC)
    )

    with (
        patch(
            "app.api.auth_routes.create_service_token", new_callable=AsyncMock
        ) as create,
        patch("app.api.auth_routes.get_event_publisher") as publisher,
    ):
        create.return_value = (token, "cusp_raw")
        publisher.return_value.publish_audit_log = AsyncMock()
        await create_token(
            CreateTokenRequest(agent_public_id=agent.public_id), _user(77), db
        )

    query = _sql(db.execute.call_args.args[0])
    assert "agent_registrations.org_id" in query
    assert "77" not in query  # value is bound, not interpolated
    create.assert_awaited_once()
    assert create.call_args.kwargs["agent_id"] == 22


@pytest.mark.asyncio
async def test_list_tokens_filters_agent_join_by_callers_org() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result

    await list_tokens(_user(88), db)

    query = _sql(db.execute.call_args.args[0])
    assert "agent_registrations.org_id" in query


@pytest.mark.asyncio
async def test_revoke_token_passes_callers_org_to_service_layer() -> None:
    db = AsyncMock()
    token_row = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = token_row
    db.execute.return_value = result
    token_id = uuid.uuid4()

    with (
        patch(
            "app.api.auth_routes.revoke_service_token", new_callable=AsyncMock
        ) as revoke,
        patch("app.api.auth_routes.get_event_publisher") as publisher,
    ):
        revoke.return_value = True
        publisher.return_value.publish_audit_log = AsyncMock()
        await revoke_token(token_id, _user(99), db)

    query = _sql(db.execute.call_args.args[0])
    assert "agent_registrations.org_id" in query
    assert revoke.call_args.kwargs["org_id"] == 99


@pytest.mark.asyncio
async def test_oidc_organization_claim_is_the_only_tenant_selector() -> None:
    from app.auth.dependencies import get_current_user
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer oidc")],
        }
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result

    with (
        patch("app.auth.dependencies.get_settings") as settings,
        patch(
            "app.auth.dependencies.verify_oidc_token", new_callable=AsyncMock
        ) as verify,
    ):
        settings.return_value.oidc_issuer_url = "https://issuer.example"
        settings.return_value.oidc_jwks_url = "https://issuer.example/jwks"
        settings.return_value.oidc_audience = "cusp-dashboard"
        verify.return_value = OIDCClaims(
            "subject-1",
            "user@example.com",
            "User",
            str(uuid.uuid4()),
        )
        with pytest.raises(HTTPException):
            await get_current_user(request, db)

    query = _sql(db.execute.call_args.args[0])
    assert "users.oidc_subject" in query
    assert "organizations.public_id" in query
