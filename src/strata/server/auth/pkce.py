"""PKCE (RFC 7636) and CSRF-state helpers for the OIDC relying party (ADR-0067 Step 7).

Stdlib-only, no new dependency — mirrors the discipline `identity_token_cache.py`
and the CLI-side identity integrations already follow.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def _b64url(data: bytes) -> str:
    """Base64url-encode without padding, per RFC 7636's required transformation."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """A high-entropy, unguessable CSRF-protection value for the authorize request."""
    return _b64url(secrets.token_bytes(32))


def generate_nonce() -> str:
    """A high-entropy value bound to the login attempt and echoed back in the `id_token`.

    Distinct from `state` (which protects the *authorization response*, i.e. the
    `/auth/callback` redirect itself) — `nonce` protects the *id_token* specifically,
    preventing a captured/replayed id_token from a different login attempt being
    accepted. Mechanically identical generation to `generate_state()`, kept as a
    separate function so call sites read as "this is the OIDC nonce claim", not
    "this happens to reuse the CSRF-state generator."
    """
    return _b64url(secrets.token_bytes(32))


def generate_code_verifier() -> str:
    """A PKCE code_verifier — 43-128 characters, RFC 7636 \u00a74.1."""
    return _b64url(secrets.token_bytes(32))


def code_challenge_s256(verifier: str) -> str:
    """Derive the S256 code_challenge from a code_verifier, per RFC 7636 \u00a74.2."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)
