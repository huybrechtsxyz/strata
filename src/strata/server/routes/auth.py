"""`/auth/login`, `/auth/callback` — OIDC Authorization Code + PKCE login (ADR-0067 Step 7).

Only registered by `create_app()` when both `oidc_config` and `session_secret`
are configured — see `app.py`, the same all-or-nothing gating `/v1/tokens`
already uses for `admin_token`.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from strata.server.routes.state import get_pending_logins, get_relying_party, get_session_secret, sweep_pending_logins

router = APIRouter()


@router.get("/auth/login")
def auth_login(request: Request) -> Dict[str, str]:
    """Begin an OIDC Authorization Code + PKCE login (ADR-0067 Step 7).

    Returns the URL to send the browser to, rather than issuing an HTTP
    redirect itself — every other route on this server is JSON-only, and this
    keeps that consistent (see the module docstring in `server/auth/oidc_relying_party.py`).
    """
    from strata.server.auth.pkce import code_challenge_s256, generate_code_verifier, generate_nonce, generate_state

    relying_party = get_relying_party(request)
    pending_logins = get_pending_logins(request)
    sweep_pending_logins(request)

    state = generate_state()
    nonce = generate_nonce()
    code_verifier = generate_code_verifier()
    pending_logins[state] = {"code_verifier": code_verifier, "nonce": nonce, "created_at": time.time()}
    try:
        assert relying_party is not None  # gated by create_app(); see module docstring
        url = relying_party.build_authorization_url(state, code_challenge_s256(code_verifier), nonce)
    except Exception as exc:
        pending_logins.pop(state, None)
        raise HTTPException(status_code=503, detail=f"Could not reach identity provider: {exc}") from exc
    return {"authorization_url": url, "state": state}


@router.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str) -> Dict[str, Any]:
    """Complete the login: exchange the code, verify the id_token, mint a session (ADR-0067 Step 7).

    The `id_token`'s signature and standard claims (`iss`/`aud`/`exp`/`nonce`) are
    verified via `authlib`/`joserfc` against the issuer's published JWKS — this is
    not optional and there is no userinfo-only fallback: a token exchange that
    returns no `id_token`, or one that fails verification, is a rejected login, not
    a degraded-but-accepted one.

    The returned token is a stateless, short-lived bearer credential — see
    `server/auth/session_tokens.py`'s module docstring for why this is an interim
    placeholder, not the persistent/revocable session Step 8 will add.
    """
    from strata.server.auth.session_tokens import mint_session_token

    relying_party = get_relying_party(request)
    assert relying_party is not None  # gated by create_app(); see module docstring
    session_secret = get_session_secret(request)
    assert session_secret is not None  # gated by create_app(); see module docstring
    pending_logins = get_pending_logins(request)

    sweep_pending_logins(request)
    pending = pending_logins.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Unknown or expired login state")

    try:
        ok, token_response = relying_party.exchange_code(code, pending["code_verifier"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not reach identity provider: {exc}") from exc
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=f"Code exchange failed: {token_response.get('error_description', token_response)}",
        )

    id_token = token_response.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Identity provider did not return an id_token")

    try:
        claims = relying_party.verify_id_token(id_token, nonce=pending["nonce"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not verify id_token: {exc}") from exc

    # Userinfo enrichment is best-effort and supplementary only — verified id_token
    # claims are the trusted source; a userinfo failure never fails the login.
    userinfo_claims = relying_party.fetch_userinfo(relying_party.discover(), token_response["access_token"])
    claims = {**userinfo_claims, **claims}

    session_token = mint_session_token(claims, session_secret, ttl_seconds=300)
    return {
        "access_token": session_token,
        "token_type": "Bearer",
        "expires_in": 300,
        "claims": claims,
    }
