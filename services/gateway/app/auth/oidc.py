"""OIDC ID-token verification for dashboard users."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import AnyHttpUrl


@dataclass(frozen=True, slots=True)
class OIDCClaims:
    """Only claims used for identity and tenant resolution."""

    subject: str
    email: str
    display_name: str
    organization_id: str | None = None


class OIDCVerificationError(ValueError):
    """Raised when an OIDC token is missing, invalid, or unverifiable."""


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> Any:
    import jwt

    return jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)


def _verify_sync(
    token: str, *, issuer: str, audience: str, jwks_url: str
) -> OIDCClaims:
    try:
        import jwt
    except ImportError as exc:
        raise OIDCVerificationError(
            "OIDC verification dependency is unavailable"
        ) from exc
    if not token or token.count(".") != 2:
        raise OIDCVerificationError("invalid OIDC token")
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
            audience=audience,
            issuer=issuer,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except (jwt.PyJWTError, OSError, ValueError) as exc:
        raise OIDCVerificationError("invalid OIDC token") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject.strip():
        raise OIDCVerificationError("OIDC token has no subject")
    if not isinstance(email, str) or not email.strip():
        raise OIDCVerificationError("OIDC token has no email")
    display_name = claims.get("name") or claims.get("preferred_username") or email
    organization_id = claims.get("organization_id") or claims.get("org_id")
    return OIDCClaims(
        subject,
        email,
        str(display_name),
        str(organization_id) if organization_id else None,
    )


async def verify_oidc_token(
    token: str,
    *,
    issuer: str | AnyHttpUrl,
    audience: str,
    jwks_url: str | AnyHttpUrl,
) -> OIDCClaims:
    """Verify an OIDC token off the event loop and return bounded claims."""
    if not audience.strip():
        raise OIDCVerificationError("OIDC audience is not configured")
    return await asyncio.to_thread(
        _verify_sync,
        token,
        issuer=str(issuer),
        audience=audience,
        jwks_url=str(jwks_url),
    )
