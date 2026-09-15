"""`GET /v1/whoami` — verifies an M2M bearer token and echoes back the calling
identity (ADR-0067 Step 10).

Only registered by `create_app()` when at least one trusted issuer is configured —
the same all-or-nothing gating `oidc_config`+`session_secret` already use for
`/auth/*`. This route exists so a CI pipeline (or an operator debugging one) can
confirm its federated credential actually verifies against the server's configured
trusted-issuer list, without inventing an RBAC-gated business route ahead of Step 9 —
it deliberately grants nothing beyond "yes, this token is valid, and here is what it
claims about itself."
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from strata.server.routes.security import verify_m2m_token

router = APIRouter()


@router.get("/v1/whoami", dependencies=[Depends(verify_m2m_token)])
def whoami_route(request: Request) -> Dict[str, Any]:
    """Return the verified M2M caller's claims, as set by `verify_m2m_token`."""
    return {"claims": request.state.m2m_claims}
