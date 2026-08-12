"""`POST /v1/events`, `GET /v1/events/tail`, `GET /v1/workspaces` (ADR-0065 Steps 2.3/2.6).

The body of `POST /v1/events` is always a CloudEvents 1.0 + ECS envelope
(``AuditController._build_envelope()``'s output), never a raw artifact dump.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from strata.server.routes.security import resolve_read_scope, verify_ingest_token
from strata.server.routes.state import get_engine

router = APIRouter()

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


@router.get("/v1/workspaces")
def workspaces_route(request: Request) -> Dict[str, Any]:
    """List distinct workspaces seen in `events` — unauthenticated, read-only.

    Backs the read-only dashboard's "Workspaces" view. Unlike `/v1/tokens` (which
    lists registered ingest tokens and requires the admin token), this reflects
    workspaces that have actually sent data, with no credential required.
    """
    from strata.server.db.query import list_workspaces

    return {"workspaces": list_workspaces(get_engine(request))}


@router.post("/v1/events", status_code=202, dependencies=[Depends(verify_ingest_token)])
def ingest_event(request: Request, body: bytes = Body(...)) -> Dict[str, Any]:
    """Ingest one event envelope (Step 2.3). Idempotent, append-only."""
    from strata.server.db.ingest import extract_row
    from strata.server.db.store import insert_event

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

    engine = get_engine(request)
    try:
        insert_event(engine, row)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Insert failed: {exc}") from exc

    return {"status": "accepted"}


@router.get("/v1/events/tail")
def tail_events(request: Request, limit: int = _DEFAULT_TAIL_LIMIT, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Return the most recent events (Step 2.6) — a lean projection, not the full payload.

    A per-workspace ingest token can only tail its own workspace — any `workspace` query
    param is overridden to the token's own scope, never widened by it. The admin token can
    tail any workspace, or omit the filter for the full cross-workspace tail.
    """
    from strata.server.db.query import list_recent_events

    scope = resolve_read_scope(request)
    effective_workspace = scope if scope is not None else workspace
    capped_limit = max(1, min(limit, _MAX_TAIL_LIMIT))
    return {"events": list_recent_events(get_engine(request), workspace=effective_workspace, limit=capped_limit)}
