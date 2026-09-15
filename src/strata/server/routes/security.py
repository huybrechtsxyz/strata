"""Shared bearer-token auth checks (ADR-0065 Step 2.4, ADR-0067 Steps 9\u201310) \u2014 used by
`routes/events.py`, `routes/tokens.py`, `routes/m2m.py`, `routes/rbac.py`, and any
future RBAC-gated route. One implementation, not one per route module.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import HTTPException, Request

from strata.server.routes.state import get_admin_token, get_engine, get_m2m_verifier, get_session_secret

if TYPE_CHECKING:
    from strata.server.auth.rbac import Principal, Tier


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


def verify_m2m_token(request: Request) -> Dict[str, Any]:
    """Bearer token required for M2M-only routes (ADR-0067 Step 10).

    Verified against the configured trusted-issuer list (`--m2m-trusted-issuer`) —
    structurally separate from `verify_ingest_token` (workspace-scoped, DB-hash-checked,
    never a JWT) and from `verify_admin_token` (a single static operator secret). Stores
    the verified claims on `request.state.m2m_claims` so the route handler can read the
    calling identity without re-verifying the token itself.
    """
    token = bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    verifier = get_m2m_verifier(request)
    if verifier is None:
        raise HTTPException(status_code=503, detail="Machine-to-machine authentication is not configured")
    try:
        claims = verifier.verify(token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    request.state.m2m_claims = claims
    return claims


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


def authenticate_principal(request: Request) -> "Principal":
    """Resolve the caller's bearer token into one normalized `Principal` (ADR-0067 Step 9).

    The prerequisite RBAC enforcement needs: `verify_session_token` (human, Step 7/8)
    and `M2mVerifier.verify` (Step 10) are otherwise two separate mechanisms that never
    converge anywhere — nothing before this verified a session token as a Bearer
    credential on any route other than `/auth/*` itself. Tries session-token
    verification first, then M2M verification; raises 401 only if neither recognizes
    the token. Stores the result on `request.state.principal` for the route handler.
    """
    from strata.server.auth.rbac import Principal
    from strata.server.auth.session_tokens import verify_session_token

    token = bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    session_secret = get_session_secret(request)
    if session_secret:
        claims = verify_session_token(token, session_secret)
        if claims is not None:
            principal = Principal(
                subject=claims.get("sub", ""),
                email=claims.get("email", ""),
                groups=tuple(claims.get("groups") or ()),
                auth_method="session",
            )
            request.state.principal = principal
            return principal

    verifier = get_m2m_verifier(request)
    if verifier is not None:
        try:
            m2m_claims = verifier.verify(token)
        except ValueError:
            m2m_claims = None
        if m2m_claims is not None:
            principal = Principal(subject=m2m_claims.get("_subject") or "", auth_method="m2m")
            request.state.principal = principal
            return principal

    raise HTTPException(status_code=401, detail="Invalid or unrecognized bearer token")


def require_rbac_admin(request: Request) -> None:
    """Admin-tier access to the RBAC management routes themselves (ADR-0067 Step 9).

    Accepts *either* the static `--admin-token` (break-glass — the same role it
    already plays for `/v1/tokens`) *or* a real, resolved `admin`-tier `Principal`.
    The static token exists specifically to bootstrap the very first role binding:
    nothing else could ever satisfy an `admin`-gated route before any binding exists.
    Deliberately global scope (`workspace=None`) — managing role bindings is a
    platform-wide action, never satisfied by a workspace-scoped binding.
    """
    from strata.server.auth.rbac import Tier
    from strata.server.db.rbac import resolve_access

    token = bearer_token(request)
    admin_token = get_admin_token(request)
    if token is not None and admin_token and hmac.compare_digest(token, admin_token):
        return

    principal = authenticate_principal(request)
    access = resolve_access(get_engine(request), principal.candidate_bindings())
    if not access.has_tier(Tier.ADMIN):
        raise HTTPException(status_code=403, detail="Requires RBAC admin tier or the admin token")


def require_tier(min_tier: "Tier", scoped: bool = False):
    """Dependency factory: require at least *min_tier* (ADR-0067 Step 9).

    `scoped=True` reads `workspace`/`environment` from the request's query
    parameters to check scoped access; `scoped=False` (the default) checks global
    access only, the same convention `require_rbac_admin` uses for platform-wide
    routes. A per-route dependency, not ASGI middleware — matching every other gate
    this server has (`verify_admin_token`, `verify_ingest_token`, `verify_m2m_token`).
    """
    from strata.server.db.rbac import resolve_access

    def _dependency(request: Request) -> "Principal":
        principal = authenticate_principal(request)
        workspace = request.query_params.get("workspace") if scoped else None
        environment = request.query_params.get("environment") if scoped else None
        access = resolve_access(get_engine(request), principal.candidate_bindings(), workspace, environment)
        if not access.has_tier(min_tier):
            raise HTTPException(status_code=403, detail=f"Requires tier '{min_tier.name.lower()}' or higher")
        request.state.access = access
        return principal

    return _dependency


def require_capability(name: str, scoped: bool = False):
    """Dependency factory: require capability *name* (e.g. `deployer`) (ADR-0067 Step 9).

    See `require_tier()` for the `scoped` parameter's meaning.
    """
    from strata.server.db.rbac import resolve_access

    def _dependency(request: Request) -> "Principal":
        principal = authenticate_principal(request)
        workspace = request.query_params.get("workspace") if scoped else None
        environment = request.query_params.get("environment") if scoped else None
        access = resolve_access(get_engine(request), principal.candidate_bindings(), workspace, environment)
        if not access.has_capability(name):
            raise HTTPException(status_code=403, detail=f"Requires capability '{name}'")
        request.state.access = access
        return principal

    return _dependency
