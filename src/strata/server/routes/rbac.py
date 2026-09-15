"""`/v1/rbac/bindings` — role-binding management (ADR-0067 Step 9).

Only registered by `create_app()` when `admin_token` is configured — the exact same
all-or-nothing gating `/v1/tokens` already uses, and for the same reason: without an
`admin_token`, there would be no way to ever bootstrap the very first `admin`-tier
role binding, so leaving these routes registered-but-unreachable would be a false
promise rather than a safe default. Once `admin_token` is configured, each request is
still gated per-call by `require_rbac_admin` (accepts the admin token *or* a resolved
`admin`-tier `Principal` — see its own docstring for the bootstrap reasoning).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from strata.server.routes.security import require_rbac_admin
from strata.server.routes.state import get_engine

router = APIRouter()


@router.post("/v1/rbac/bindings", status_code=201, dependencies=[Depends(require_rbac_admin)])
def create_binding_route(
    request: Request,
    subject_type: str,
    subject: str,
    tier: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    workspace: Optional[str] = None,
    environment: Optional[str] = None,
) -> Dict[str, str]:
    """Create a new role binding. Rejects one that would grant neither a tier nor a capability."""
    from strata.server.db.rbac import create_binding

    try:
        binding_id = create_binding(
            get_engine(request),
            subject_type=subject_type,
            subject=subject,
            tier=tier,
            capabilities=capabilities,
            workspace=workspace,
            environment=environment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"binding_id": binding_id}


@router.get("/v1/rbac/bindings", dependencies=[Depends(require_rbac_admin)])
def list_bindings_route(request: Request, workspace: Optional[str] = None) -> Dict[str, Any]:
    """List role bindings, optionally filtered by workspace (exact match, not wildcard)."""
    from strata.server.db.rbac import list_bindings

    return {"bindings": list_bindings(get_engine(request), workspace)}


@router.delete("/v1/rbac/bindings/{binding_id}", dependencies=[Depends(require_rbac_admin)])
def revoke_binding_route(request: Request, binding_id: str) -> Dict[str, Any]:
    """Revoke a role binding by id."""
    from strata.server.db.rbac import revoke_binding

    if not revoke_binding(get_engine(request), binding_id):
        raise HTTPException(status_code=404, detail="Binding not found or already revoked")
    return {"status": "revoked", "binding_id": binding_id}
