"""`GET /healthz` — liveness check (ADR-0065 Steps 2.1-2.2)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from strata.server.routes.state import get_engine

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> Dict[str, Any]:
    """Liveness check — also verifies the database is reachable (Step 2.2)."""
    from strata.server.db.engine import check_connection

    ok, detail = check_connection(get_engine(request))
    if not ok:
        raise HTTPException(status_code=503, detail=detail)
    return {"status": "ok"}
