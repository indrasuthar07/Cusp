"""
Auth API routes.

POST /api/v1/auth/tokens       — create a service token (admin/owner only)
GET  /api/v1/auth/tokens       — list tokens for an org  (admin/owner only)
POST /api/v1/auth/tokens/revoke — revoke a token         (admin/owner only)
GET  /api/v1/auth/me            — introspect calling identity (agent or user)
"""

from __future__ import annotations

import uuid

from app.auth.dependencies import (
    AgentIdentity,
    UserIdentity,
    get_current_agent,
    get_current_user,
    require_role,
)
from app.auth.schemas import (
    AgentInfoResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    RevokeTokenResponse,
    TokenListResponse,
    TokenSummary,
    UserInfoResponse,
)
from app.auth.service_tokens import create_service_token, revoke_service_token
from app.db import get_db
from app.db.models import AgentRegistration, MembershipRole, ServiceToken
from app.events import AuditLogPayload, get_event_publisher
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# Create token (admin/owner only)
@router.post(
    "/tokens",
    response_model=CreateTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service token for an agent",
)
async def create_token(
    body: CreateTokenRequest,
    user: UserIdentity = Depends(
        require_role(MembershipRole.owner, MembershipRole.admin)
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a new bearer token for the specified agent.
    The raw token is returned ONCE in the response body.
    Only org owners and admins may create tokens.
    """
    # Resolve agent by public_id within the user's org
    stmt = select(AgentRegistration).where(
        AgentRegistration.public_id == body.agent_public_id,
        AgentRegistration.org_id == user.org_id,
    )
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {body.agent_public_id} not found in your organization.",
        )

    if agent.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create a token for a disabled agent.",
        )

    token_row, raw_token = await create_service_token(
        db,
        agent_id=agent.id,
        label=body.label,
        scopes=body.scopes,
    )
    await db.commit()

    # Emit audit event to Redis Streams
    await get_event_publisher().publish_audit_log(
        org_id=user.org_id,
        payload=AuditLogPayload(
            actor_type="user",
            actor_id=str(user.user_public_id),
            action="token.created",
            resource_type="service_token",
            resource_id=str(token_row.public_id),
            details={"agent_public_id": str(agent.public_id), "label": token_row.label},
        ),
    )

    return CreateTokenResponse(
        token_public_id=token_row.public_id,
        raw_token=raw_token,
        label=token_row.label,
        scopes=token_row.scopes,
        created_at=token_row.created_at,
    )


# List tokens (admin/owner only)
@router.get(
    "/tokens",
    response_model=TokenListResponse,
    summary="List all service tokens for the organization",
)
async def list_tokens(
    user: UserIdentity = Depends(
        require_role(MembershipRole.owner, MembershipRole.admin)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all tokens in the org. Raw token values are never returned."""
    stmt = (
        select(ServiceToken)
        .options(selectinload(ServiceToken.agent))
        .join(AgentRegistration)
        .where(AgentRegistration.org_id == user.org_id)
        .order_by(ServiceToken.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    tokens = [
        TokenSummary(
            token_public_id=t.public_id,
            agent_public_id=t.agent.public_id,
            agent_name=t.agent.name,
            label=t.label,
            scopes=t.scopes,
            is_revoked=t.is_revoked,
            expires_at=t.expires_at,
            created_at=t.created_at,
        )
        for t in rows
    ]

    return TokenListResponse(tokens=tokens, count=len(tokens))


# Revoke token (admin/owner only)
@router.post(
    "/tokens/revoke",
    response_model=RevokeTokenResponse,
    summary="Revoke a service token by its public ID",
)
async def revoke_token(
    token_public_id: uuid.UUID,
    user: UserIdentity = Depends(
        require_role(MembershipRole.owner, MembershipRole.admin)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Permanently revoke a token. It can never be un-revoked."""
    # Verify the token belongs to the user's org
    stmt = (
        select(ServiceToken)
        .join(AgentRegistration)
        .where(
            ServiceToken.public_id == token_public_id,
            AgentRegistration.org_id == user.org_id,
        )
    )
    result = await db.execute(stmt)
    token_row = result.scalar_one_or_none()

    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found in your organization.",
        )

    revoked = await revoke_service_token(db, token_public_id, org_id=user.org_id)
    await db.commit()

    # Emit audit event to Redis Streams
    await get_event_publisher().publish_audit_log(
        org_id=user.org_id,
        payload=AuditLogPayload(
            actor_type="user",
            actor_id=str(user.user_public_id),
            action="token.revoked",
            resource_type="service_token",
            resource_id=str(token_public_id),
            details={"revoked": revoked},
        ),
    )

    return RevokeTokenResponse(
        token_public_id=token_public_id,
        revoked=revoked,
    )


# Identity introspection (agent)
@router.get(
    "/me/agent",
    response_model=AgentInfoResponse,
    summary="Introspect the calling agent's identity",
)
async def whoami_agent(
    agent: AgentIdentity = Depends(get_current_agent),
):
    """Returns the identity resolved from the Bearer token."""
    return AgentInfoResponse(
        agent_public_id=agent.agent_public_id,
        agent_name=agent.agent_name,
        org_public_id=agent.org_public_id,
        org_name=agent.org_name,
        scopes=agent.scopes,
    )


# Identity introspection (user)
@router.get(
    "/me/user",
    response_model=UserInfoResponse,
    summary="Introspect the calling user's identity",
)
async def whoami_user(
    user: UserIdentity = Depends(get_current_user),
):
    """Returns the identity resolved from the user session."""
    return UserInfoResponse(
        user_public_id=user.user_public_id,
        email=user.email,
        display_name=user.display_name,
        org_public_id=user.org_public_id,
        role=user.role.value,
    )
