"""
HMAC-SHA256 service token creation and validation.

Flow:
  1. Admin calls "create token" for a registered agent.
  2. We generate a cryptographically random raw token (returned ONCE to the caller).
  3. We compute  HMAC-SHA256(CUSP_TOKEN_PEPPER, raw_token)  and store only the
     hash in the `service_tokens` table.  The raw token is never persisted.
  4. When an agent presents the raw token on subsequent requests, we re-hash it
     with the same pepper and look up the hash in the DB.

Security notes:
  • The pepper is a server-side secret loaded from CUSP_TOKEN_PEPPER env var.
  • Even if the DB leaks, attackers cannot recover usable tokens without the pepper.
  • Tokens are scoped to a single agent registration (and therefore a single org).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime

from app.config.settings import get_settings
from app.db.models import AgentRegistration, ServiceToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Token prefix for easy identification
TOKEN_PREFIX = "cusp_"
RAW_TOKEN_BYTES = 32  # 256-bit entropy


def _hash_token(raw_token: str, pepper: str) -> str:
    """Compute HMAC-SHA256(pepper, raw_token) and return the hex digest."""
    return hmac.new(
        key=pepper.encode("utf-8"),
        msg=raw_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


async def create_service_token(
    db: AsyncSession,
    *,
    agent_id: int,
    label: str = "",
    scopes: dict | None = None,
    expires_at: datetime | None = None,
) -> tuple[ServiceToken, str]:
    """
    Create a new service token for an agent.

    Returns:
        (ServiceToken row, raw_token_string)
        The raw token is returned exactly once — it is NOT stored anywhere.
    """
    settings = get_settings()

    # Generate a high-entropy raw token with a recognisable prefix
    raw_token = TOKEN_PREFIX + secrets.token_hex(RAW_TOKEN_BYTES)

    # Hash with the server pepper - only the hash is stored
    token_hash = _hash_token(raw_token, settings.cusp_token_pepper.get_secret_value())

    token_row = ServiceToken(
        public_id=uuid.uuid4(),
        agent_id=agent_id,
        token_hash=token_hash,
        label=label,
        scopes=scopes or {},
        is_revoked=False,
        expires_at=expires_at,
    )
    db.add(token_row)
    await db.flush()
    await db.refresh(token_row)

    return token_row, raw_token


async def validate_service_token(
    db: AsyncSession,
    raw_token: str,
) -> ServiceToken | None:
    """
    Validate a raw bearer token presented by an agent.

    Returns the ServiceToken row (with agent eagerly loaded) if valid,
    or None if the token is unknown, revoked, or expired.
    """
    settings = get_settings()

    if not raw_token.startswith(TOKEN_PREFIX):
        return None

    # Re-hash the presented token with the same pepper
    token_hash = _hash_token(raw_token, settings.cusp_token_pepper.get_secret_value())

    stmt = (
        select(ServiceToken)
        .options(
            selectinload(ServiceToken.agent).selectinload(
                AgentRegistration.organization
            )
        )
        .where(ServiceToken.token_hash == token_hash)
    )
    result = await db.execute(stmt)
    token_row = result.scalar_one_or_none()

    if token_row is None:
        return None

    # Check revocation
    if token_row.is_revoked:
        return None

    # Check expiry
    if token_row.expires_at is not None:
        now = datetime.now(UTC)
        if token_row.expires_at < now:
            return None

    # Check that the parent agent is not disabled
    if token_row.agent.is_disabled:
        return None

    # Check that the parent org is not disabled
    if token_row.agent.organization.is_disabled:
        return None

    return token_row


async def revoke_service_token(
    db: AsyncSession,
    token_public_id: uuid.UUID,
    *,
    org_id: int,
) -> bool:
    """
    Revoke a service token by its public UUID.
    Returns True if a token was found in the requested organization and revoked.
    """
    stmt = (
        select(ServiceToken)
        .join(AgentRegistration)
        .where(ServiceToken.public_id == token_public_id)
    )
    stmt = stmt.where(AgentRegistration.org_id == org_id)
    result = await db.execute(stmt)
    token_row = result.scalar_one_or_none()

    if token_row is None:
        return False

    token_row.is_revoked = True
    await db.flush()
    return True
