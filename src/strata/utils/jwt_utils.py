"""Minimal JWT payload decoding — claims extraction only, never signature verification.

Used to read claims (``email``, ``sub``, etc.) out of an ID token that was already
obtained through a trusted local channel — e.g. ``gcloud auth print-identity-token``,
where the token comes from an already-authenticated local CLI session, not from an
untrusted network peer. This is the same trust footing as reading ``az account show``'s
JSON output directly: the token is trusted because the *channel* that produced it is
trusted, not because its signature was checked here.

Do not use this to accept a token presented by a remote caller — that requires real
signature verification against the issuer's published keys, which this module
deliberately does not do.
"""

import base64
import json
from typing import Any, Dict


def decode_payload_unverified(token: str) -> Dict[str, Any]:
    """Return the decoded claims from a JWT's payload segment.

    Args:
        token: A compact JWT (``header.payload.signature``).

    Returns:
        The decoded claims dict, or ``{}`` if the token is malformed.
    """
    try:
        payload_segment = token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        decoded = base64.urlsafe_b64decode(payload_segment + padding)
        return json.loads(decoded)
    except Exception:
        return {}
