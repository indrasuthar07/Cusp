"""
FastAPI auth dependencies.

Two auth paths:
  1. Agent tokens  → `get_current_agent`  (Bearer token in Authorization header)
  2. User sessions → `get_current_user`   (JWT in cookie or Authorization header)

Both resolve to typed dataclasses that downstream routes can depend on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models import AgentRegistration, MembershipRole, Organization, ServiceToken, User
from app.auth.service_tokens import validate_service_token


# Resolved identity containers 

@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Resolved identity for an authenticated agent."""
    token_id: int
    token_public_id: uuid.UUID
    agent_id: int
    agent_public_id: uuid.UUID
    agent_name: str
    org_id: int
    org_public_id: uuid.UUID
    org_name: str
    scopes: dict


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """Resolved identity for an authenticated dashboard user."""
    user_id: int
    user_public_id: uuid.UUID
    email: str
    display_name: str
    org_id: int
    org_public_id: uuid.UUID
    role: MembershipRole


# Helper: extract bearer token from request 
def _extract_bearer_token(request: Request) -> str | None:
    """Pull a Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


# Agent dependency 
async def get_current_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentIdentity:
    """
    FastAPI dependency that resolves the calling agent from a Bearer token.

    Usage in a route:
        @router.post("/api/v1/actions/preflight")
        async def preflight(agent: AgentIdentity = Depends(get_current_agent)):
            ...
    """
    raw_token = _extract_bearer_token(request)
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_row: ServiceToken | None = await validate_service_token(db, raw_token)
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked service token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    agent: AgentRegistration = token_row.agent
    org: Organization = agent.organization

    return AgentIdentity(
        token_id=token_row.id,
        token_public_id=token_row.public_id,
        agent_id=agent.id,
        agent_public_id=agent.public_id,
        agent_name=agent.name,
        org_id=org.id,
        org_public_id=org.public_id,
        org_name=org.name,
        scopes=token_row.scopes,
    )


# User dependency (placeholder - will use OIDC in a later phase) 
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserIdentity:
    """
    FastAPI dependency that resolves the dashboard user.

    MVP: Reads X-Cusp-User-Id header (for local dev).
    Production: Will validate OIDC id_token from cookie.
    """
    user_id_str = request.headers.get("X-Cusp-User-Id")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Cusp-User-Id header (MVP auth). "
                   "In production this will be replaced by OIDC.",
        )

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.db.models import Membership

    try:
        uid = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Cusp-User-Id must be an integer.",
        )

    stmt = (
        select(Membership)
        .options(
            selectinload(Membership.user),
            selectinload(Membership.organization),
        )
        .join(User)
        .where(User.id == uid)
    )
    result = await db.execute(stmt)
    membership = result.scalars().first()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no organization membership.",
        )

    return UserIdentity(
        user_id=membership.user.id,
        user_public_id=membership.user.public_id,
        email=membership.user.email,
        display_name=membership.user.display_name,
        org_id=membership.organization.id,
        org_public_id=membership.organization.public_id,
        role=membership.role,
    )


# Role guard 
def require_role(*allowed_roles: MembershipRole):
    """
    Returns a FastAPI dependency that enforces the user has one of the
    specified roles.

    Usage:
        @router.post("/api/v1/agents")
        async def register_agent(
            user: UserIdentity = Depends(require_role(MembershipRole.owner, MembershipRole.admin)),
        ):
            ...
    """
    async def _guard(
        user: UserIdentity = Depends(get_current_user),
    ) -> UserIdentity:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {[r.value for r in allowed_roles]}. "
                       f"You have: {user.role.value}.",
            )
        return user

    return _guard