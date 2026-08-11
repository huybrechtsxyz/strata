"""ASGI application factory for the strata state-service server.

``GET /healthz`` exists from Step 2.1 onward; from Step 2.2 onward it also
verifies database connectivity via the engine passed to :func:`create_app`.
``POST /v1/events`` (Step 2.3) is the ingest route — the body is always a
CloudEvents 1.0 + ECS envelope (``AuditController._build_envelope()``'s
output), never a raw artifact dump. Step 2.4 adds bearer-token auth on
``/v1/events`` (per-workspace ingest tokens, verified against the ``tokens``
table) and, when ``admin_token`` is configured, admin routes
(``/v1/tokens``) for managing those tokens over HTTP — so that, in steady
state, nobody but the server process itself ever needs a direct database
connection (the one exception is ``serve migrate``, which must run before
any table — including ``tokens`` — exists at all).

``fastapi`` is imported at module scope (not lazily inside ``create_app()``)
so route parameter annotations (``Request``, ``Body(...)``) resolve correctly
against this module's own ``__globals__`` — FastAPI's signature inspection
looks up annotations there, not in an enclosing function's locals, so a
lazy import inside ``create_app()`` would leave ``Request``/``Body`` on
route handlers unresolvable at real request-handling time. Requires the
optional dependency: pip install xyz-strata[server]
"""

from __future__ import annotations

import hmac
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

try:
    from fastapi import Body, Depends, FastAPI, HTTPException, Request
except ImportError as _exc:
    raise ImportError(
        "The 'fastapi' package is required for the strata state-service server.\n"
        "Install it with: pip install xyz-strata[server]"
    ) from _exc

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# ADR-0065 Phase 2's own "no large blobs" exclusion — an oversized body is
# rejected, never silently truncated or fully buffered/parsed.
_MAX_BODY_BYTES = 256 * 1024

# Step 2.6's read-tail endpoint caps the response size server-side regardless
# of what a client requests — the same size-discipline reasoning as above,
# applied to response size instead of request size.
_MAX_TAIL_LIMIT = 500
_DEFAULT_TAIL_LIMIT = 100


def _content_too_large(request: Request, body: bytes) -> bool:
    """True if the request exceeds the ingest size cap.

    Checks the Content-Length header first (cheap, no body read implied) and
    the actual body length too, defending against a missing/incorrect header.
    """
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
        return True
    return len(body) > _MAX_BODY_BYTES


def _bearer_token(request: Request) -> Optional[str]:
    """Extract the raw token from a well-formed `Authorization: Bearer <token>` header."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer ") :]


def create_app(engine: "Engine", admin_token: Optional[str] = None) -> FastAPI:
    """Build the FastAPI app. Requires ``pip install xyz-strata[server]``.

    `admin_token` enables the token-management routes (`/v1/tokens`) when
    provided — deliberately unregistered otherwise, so there is never an
    unauthenticated admin surface by accident.
    """
    from strata.server.db.engine import check_connection
    from strata.server.db.ingest import extract_row
    from strata.server.db.query import list_recent_events
    from strata.server.db.store import insert_event
    from strata.server.db.tokens import create_token, list_tokens, revoke_token, verify_token

    app = FastAPI(title="strata state service")

    def verify_ingest_token(request: Request) -> None:
        """Per-workspace bearer token required for /v1/events (Step 2.4)."""
        token = _bearer_token(request)
        if token is None:
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
        if verify_token(engine, token) is None:
            raise HTTPException(status_code=403, detail="Invalid or revoked token")

    def verify_admin_token(request: Request) -> None:
        """Separate, higher-privilege credential guarding /v1/tokens (Step 2.4)."""
        token = _bearer_token(request)
        if token is None:
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
        if not admin_token or not hmac.compare_digest(token, admin_token):
            raise HTTPException(status_code=403, detail="Invalid admin token")

    def resolve_read_scope(request: Request) -> Optional[str]:
        """Bearer token required for /v1/events/tail (Step 2.6).

        Returns the workspace scope to enforce — an ingest token's own workspace — or
        `None` for unrestricted (admin) access. Called directly from the route body
        rather than via `dependencies=[]`, unlike `verify_ingest_token`/`verify_admin_token`
        above: those are fire-and-forget checks, but this route needs the *scope value*
        itself to filter the query, not just a pass/fail auth decision.
        """
        token = _bearer_token(request)
        if token is None:
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
        if admin_token and hmac.compare_digest(token, admin_token):
            return None
        workspace = verify_token(engine, token)
        if workspace is None:
            raise HTTPException(status_code=403, detail="Invalid or revoked token")
        return workspace

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        """Liveness check — also verifies the database is reachable (Step 2.2)."""
        ok, detail = check_connection(engine)
        if not ok:
            raise HTTPException(status_code=503, detail=detail)
        return {"status": "ok"}

    @app.post("/v1/events", status_code=202, dependencies=[Depends(verify_ingest_token)])
    def ingest_event(request: Request, body: bytes = Body(...)) -> Dict[str, Any]:
        """Ingest one event envelope (Step 2.3). Idempotent, append-only."""
        if _content_too_large(request, body):
            raise HTTPException(status_code=413, detail="Payload too large")

        try:
            envelope = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Malformed JSON: {exc}") from exc
        if not isinstance(envelope, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")

        try:
            row = extract_row(envelope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            insert_event(engine, row)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Insert failed: {exc}") from exc

        return {"status": "accepted"}

    @app.get("/v1/events/tail")
    def tail_events(
        request: Request, limit: int = _DEFAULT_TAIL_LIMIT, workspace: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return the most recent events (Step 2.6) — a lean projection, not the full payload.

        A per-workspace ingest token can only tail its own workspace — any `workspace` query
        param is overridden to the token's own scope, never widened by it. The admin token can
        tail any workspace, or omit the filter for the full cross-workspace tail.
        """
        scope = resolve_read_scope(request)
        effective_workspace = scope if scope is not None else workspace
        capped_limit = max(1, min(limit, _MAX_TAIL_LIMIT))
        return {"events": list_recent_events(engine, workspace=effective_workspace, limit=capped_limit)}

    if admin_token:

        @app.post("/v1/tokens", status_code=201, dependencies=[Depends(verify_admin_token)])
        def create_token_route(workspace: str) -> Dict[str, str]:
            """Create a new per-workspace ingest token. The secret is returned exactly once."""
            return create_token(engine, workspace)

        @app.get("/v1/tokens", dependencies=[Depends(verify_admin_token)])
        def list_tokens_route(workspace: Optional[str] = None) -> Dict[str, Any]:
            """List tokens (never the hash or secret), optionally filtered by workspace."""
            return {"tokens": list_tokens(engine, workspace)}

        @app.delete("/v1/tokens/{token_id}", dependencies=[Depends(verify_admin_token)])
        def revoke_token_route(token_id: str) -> Dict[str, Any]:
            """Revoke a token by id."""
            if not revoke_token(engine, token_id):
                raise HTTPException(status_code=404, detail="Token not found or already revoked")
            return {"status": "revoked", "token_id": token_id}

    return app
