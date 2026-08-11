"""Refresh-token encryption at rest (ADR-0067 Step 8).

Unlike ingest tokens (`db/tokens.py`), a refresh token must be *recoverable* —
the server presents it back to the identity provider on every `/auth/refresh`
call — so it is encrypted, not hashed. Uses `joserfc`'s JWE (already a
dependency via `authlib`, added for id_token verification in Step 7) rather
than a new crypto library.

The encryption key is derived from `--session-secret` via SHA-256 (32 bytes,
exactly what AES-256-GCM's direct-encryption mode requires) — the same
secret Step 7 already uses to sign stateless access tokens, not a second
secret to configure and lose track of.
"""

from __future__ import annotations

import hashlib

from joserfc import jwe
from joserfc.jwk import OctKey

_ENC_ALGORITHM = "A256GCM"


def _derive_key(session_secret: str) -> OctKey:
    key_bytes = hashlib.sha256(session_secret.encode("utf-8")).digest()
    return OctKey.import_key(key_bytes)


def encrypt_refresh_token(refresh_token: str, session_secret: str) -> str:
    """Return a JWE compact serialization of *refresh_token*, encrypted at rest."""
    key = _derive_key(session_secret)
    return jwe.encrypt_compact({"alg": "dir", "enc": _ENC_ALGORITHM}, refresh_token, key)


def decrypt_refresh_token(encrypted: str, session_secret: str) -> str:
    """Recover the plaintext refresh token from *encrypted*. Raises on tamper/wrong key."""
    key = _derive_key(session_secret)
    plaintext = jwe.decrypt_compact(encrypted, key).plaintext
    if plaintext is None:
        raise ValueError("Refresh token decryption produced no plaintext")
    return plaintext.decode("utf-8")
