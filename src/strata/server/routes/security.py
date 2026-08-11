"""Shared bearer-token auth checks (ADR-0065 Step 2.4) — used by `routes/events.py`
and `routes/tokens.py`. One implementation, not one per route module.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request

from strata.server.routes.state import get_admin_token, get_engine


def bearer_token(request: Request) -> Optional[str]:
    """Extract the raw token from a well-formed `Authorization: Bearer <token>` header."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer ") :]


def verify_ingest_token(request: Request) -> None:
    """Per-workspace bearer token required for /v1/events (Step 2.4)."""
    from strata.server.db.tokens import verify_token

    token = bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    if verify_token(get_engine(request), token) is None:
        raise HTTPException(status_code=403, detail="Invalid or revoked token")


def verify_admin_token(request: Request) -> None:
    """Separate, higher-privilege credential guarding /v1/tokens (Step 2.4)."""
    token = bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    admin_token = get_admin_token(request)
    if not admin_token or not hmac.compare_digest(token, admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def resolve_read_scope(request: Request) -> Optional[str]:
    """Bearer token optional for /v1/events/tail (Step 2.6).

    Returns the workspace scope to enforce — an ingest token's own workspace — or
    `None` for unrestricted access. Called directly from the route body rather than
    via `dependencies=[]`, unlike `verify_ingest_token`/`verify_admin_token` above:
    those are fire-and-forget checks, but this route needs the *scope value* itself
    to filter the query, not just a pass/fail auth decision.

    TEMPORARY: no token at all is treated as unrestricted access (same as a valid
    admin token) rather than a 401 — relaxed for the read-only React dashboard,
    which has no auth yet. A malformed/invalid/revoked token is still rejected. Once
    the dashboard grows real authentication, this should go back to requiring a
    token unconditionally.
    """
    from strata.server.db.tokens import verify_token

    token = bearer_token(request)
    if token is None:
        return None
    admin_token = get_admin_token(request)
    if admin_token and hmac.compare_digest(token, admin_token):
        return None
    workspace = verify_token(get_engine(request), token)
    if workspace is None:
        raise HTTPException(status_code=403, detail="Invalid or revoked token")
    return workspace
