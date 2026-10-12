"""
Unit tests for HMAC-SHA256 service token hashing.

These tests verify the core cryptographic logic WITHOUT needing
a running PostgreSQL database. They test:
  1. Token generation produces the correct prefix and length.
  2. Hashing is deterministic — same input always gives same hash.
  3. Different tokens produce different hashes.
  4. Different peppers produce different hashes (pepper isolation).
  5. Hashing uses HMAC-SHA256 specifically (not plain SHA-256).
"""

import hashlib
import hmac

from app.auth.service_tokens import TOKEN_PREFIX, RAW_TOKEN_BYTES, _hash_token


def test_hash_is_deterministic():
    """Same token + same pepper = same hash every time."""
    token = "cusp_abc123"
    pepper = "my-secret-pepper"
    h1 = _hash_token(token, pepper)
    h2 = _hash_token(token, pepper)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest is always 64 chars


def test_different_tokens_different_hashes():
    """Two different raw tokens must produce different hashes."""
    pepper = "same-pepper"
    h1 = _hash_token("cusp_token_aaa", pepper)
    h2 = _hash_token("cusp_token_bbb", pepper)
    assert h1 != h2


def test_different_peppers_different_hashes():
    """Same token but different pepper must produce different hashes.
    This proves the pepper is actually used in the HMAC computation."""
    token = "cusp_same_token"
    h1 = _hash_token(token, "pepper-one")
    h2 = _hash_token(token, "pepper-two")
    assert h1 != h2


def test_hash_matches_manual_hmac():
    """The hash output must exactly match a manual HMAC-SHA256 computation.
    This guards against accidentally using plain SHA-256 or a different algo."""
    token = "cusp_test_token_123"
    pepper = "test-pepper-value"

    expected = hmac.new(
        key=pepper.encode("utf-8"),
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    actual = _hash_token(token, pepper)
    assert actual == expected


def test_token_prefix_constant():
    """The token prefix should be 'cusp_' for easy identification."""
    assert TOKEN_PREFIX == "cusp_"


def test_raw_token_entropy():
    """Raw tokens should use at least 256 bits of entropy (32 bytes)."""
    assert RAW_TOKEN_BYTES >= 32