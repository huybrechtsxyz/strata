"""`/v1/tokens` — per-workspace ingest token management (ADR-0065 Step 2.4).

Only registered by `create_app()` when `admin_token` is configured — see
`app.py`; there is never an unauthenticated admin surface by accident.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from strata.server.routes.security import verify_admin_token
from strata.server.routes.state import get_engine

router = APIRouter()


@router.post("/v1/tokens", status_code=201, dependencies=[Depends(verify_admin_token)])
def create_token_route(request: Request, workspace: str) -> Dict[str, str]:
    """Create a new per-workspace ingest token. The secret is returned exactly once."""
    from strata.server.db.tokens import create_token

    return create_token(get_engine(request), workspace)


@router.get("/v1/tokens", dependencies=[Depends(verify_admin_token)])
def list_tokens_route(request: Request, workspace: Optional[str] = None) -> Dict[str, Any]:
    """List tokens (never the hash or secret), optionally filtered by workspace."""
    from strata.server.db.tokens import list_tokens

    return {"tokens": list_tokens(get_engine(request), workspace)}


@router.delete("/v1/tokens/{token_id}", dependencies=[Depends(verify_admin_token)])
def revoke_token_route(request: Request, token_id: str) -> Dict[str, Any]:
    """Revoke a token by id."""
    from strata.server.db.tokens import revoke_token

    if not revoke_token(get_engine(request), token_id):
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    return {"status": "revoked", "token_id": token_id}
