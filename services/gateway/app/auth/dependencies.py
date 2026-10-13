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
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import OIDCVerificationError, verify_oidc_token
from app.config.settings import get_settings
from app.db import get_db
from app.db.models import (
    AgentRegistration,
    Membership,
    MembershipRole,
    Organization,
    ServiceToken,
    User,
)
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


# User dependency
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserIdentity:
    """Resolve a dashboard user from a verified OIDC ID token.

    The token may be supplied as a secure-session cookie or an Authorization
    bearer token. Organization membership is resolved from the database; no
    request header or caller-selected organization is trusted.
    """
    raw_token = request.cookies.get("cusp_oidc_id_token") or _extract_bearer_token(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OIDC identity token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    if settings.oidc_issuer_url is None or settings.oidc_jwks_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC dashboard authentication is not configured.",
        )
    try:
        claims = await verify_oidc_token(
            raw_token,
            issuer=settings.oidc_issuer_url,
            audience=settings.oidc_audience,
            jwks_url=settings.oidc_jwks_url,
        )
    except OIDCVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OIDC identity token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    stmt = (
        select(Membership)
        .options(
            selectinload(Membership.user),
            selectinload(Membership.organization),
        )
        .join(User)
        .join(Membership.organization)
        .where(
            User.oidc_subject == claims.subject,
            Organization.is_disabled.is_(False),
        )
    )
    if claims.organization_id:
        try:
            organization_public_id = uuid.UUID(claims.organization_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid OIDC organization claim.",
            ) from exc
        stmt = stmt.where(Organization.public_id == organization_public_id)
    result = await db.execute(stmt)
    memberships = result.scalars().all()

    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no organization membership.",
        )
    if len(memberships) > 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC identity has multiple organizations; a trusted organization claim is required.",
        )
    membership = memberships[0]

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