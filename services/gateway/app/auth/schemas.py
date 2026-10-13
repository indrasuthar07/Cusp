"""
Pydantic response/request schemas for the auth API.
These are the public-facing shapes — never expose internal IDs or token hashes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# Token creation
class CreateTokenRequest(BaseModel):
    """Request body for POST /api/v1/auth/tokens."""

    agent_public_id: uuid.UUID = Field(
        description="Public UUID of the agent this token is for."
    )
    label: str = Field(
        default="",
        max_length=255,
        description="Human-readable label for the token (e.g. 'CI pipeline').",
    )
    scopes: dict = Field(
        default_factory=dict,
        description="Permission scopes granted to this token.",
    )


class CreateTokenResponse(BaseModel):
    """Response for POST /api/v1/auth/tokens.

    IMPORTANT: `raw_token` is returned exactly ONCE. It is never stored
    and cannot be retrieved again.
    """

    token_public_id: uuid.UUID
    raw_token: str = Field(
        description="The raw bearer token — copy it now, it will never be shown again."
    )
    label: str
    scopes: dict
    created_at: datetime


# Token listing
class TokenSummary(BaseModel):
    """A single token in a list response — never includes the raw token."""

    token_public_id: uuid.UUID
    agent_public_id: uuid.UUID
    agent_name: str
    label: str
    scopes: dict
    is_revoked: bool
    expires_at: datetime | None
    created_at: datetime


class TokenListResponse(BaseModel):
    tokens: list[TokenSummary]
    count: int


# Token revocation
class RevokeTokenResponse(BaseModel):
    token_public_id: uuid.UUID
    revoked: bool


# Identity introspection
class AgentInfoResponse(BaseModel):
    """Response for GET /api/v1/auth/me (agent)."""

    agent_public_id: uuid.UUID
    agent_name: str
    org_public_id: uuid.UUID
    org_name: str
    scopes: dict


class UserInfoResponse(BaseModel):
    """Response for GET /api/v1/auth/me (user)."""

    user_public_id: uuid.UUID
    email: str
    display_name: str
    org_public_id: uuid.UUID
    role: str
