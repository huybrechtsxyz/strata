"""Stateless, HMAC-signed session tokens — an interim placeholder for ADR-0067 Step 7.

These are deliberately NOT the persistent, revocable session record ADR-0067's own
"Session model" section describes and Step 8 ("Session store") will add. They exist
so Step 7's `/auth/callback` has something concrete to hand back today: a short-lived,
self-contained bearer credential, verified locally with no database round-trip.

Once Step 8 lands, `/auth/callback` should mint *this* short-lived access token AND a
server-side refresh-token record — this module only ever covers the access-token half,
and has no revocation mechanism of its own (a token is valid until it expires, full stop).
Never use a long TTL with this module; that is precisely the gap Step 8 exists to close.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def mint_session_token(claims: Dict[str, Any], secret: str, ttl_seconds: int) -> str:
    """Return a signed `<payload>.<signature>` token embedding *claims* and an expiry.

    Not a JWT (no header, no registered algorithm negotiation, no library dependency) —
    a minimal, purpose-built equivalent, consistent with this codebase's existing
    preference for stdlib `hmac`/`hashlib` over a new dependency for one specific need.
    """
    payload = {**claims, "exp": time.time() + ttl_seconds}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(payload_b64, secret)
    return f"{payload_b64}.{signature}"


def verify_session_token(token: str, secret: str) -> Optional[Dict[str, Any]]:
    """Return the embedded claims if *token* has a valid signature and has not expired."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = _sign(payload_b64, secret)
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None

    if payload.get("exp", 0) < time.time():
        return None
    return payload
