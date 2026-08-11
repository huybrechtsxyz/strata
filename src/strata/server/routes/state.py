"""Typed accessors for per-app-instance config stored on `app.state`.

`create_app()` sets each of these exactly once, at app-creation time. Route
modules call these (as plain functions, not `Depends()`-injected parameters —
see the module docstring in `routes/__init__.py`) instead of closing over
`create_app()`'s local variables.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import Request

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from strata.server.auth.oidc_relying_party import OidcRelyingParty

# A pending login does not survive a restart or work behind multiple replicas —
# an accepted, documented limitation until ADR-0067 Step 8's session store gives
# this (and real sessions) a persistent home.
PENDING_LOGIN_TTL = 600


def get_engine(request: Request) -> "Engine":
    return request.app.state.engine


def get_admin_token(request: Request) -> Optional[str]:
    return request.app.state.admin_token


def get_relying_party(request: Request) -> Optional["OidcRelyingParty"]:
    return request.app.state.relying_party


def get_session_secret(request: Request) -> Optional[str]:
    return request.app.state.session_secret


def get_pending_logins(request: Request) -> Dict[str, Dict[str, Any]]:
    return request.app.state.pending_logins


def sweep_pending_logins(request: Request) -> None:
    """Drop pending logins older than `PENDING_LOGIN_TTL` (ADR-0067 Step 7)."""
    pending_logins = get_pending_logins(request)
    cutoff = time.time() - PENDING_LOGIN_TTL
    expired = [state for state, entry in pending_logins.items() if entry["created_at"] < cutoff]
    for state in expired:
        pending_logins.pop(state, None)
