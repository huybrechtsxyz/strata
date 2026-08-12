"""`/auth/login`, `/auth/callback`, `/auth/refresh`, `/auth/sessions` — OIDC
Authorization Code + PKCE login (ADR-0067 Step 7) and the persistent session
store backing refresh/revocation (ADR-0067 Step 8).

`/auth/login` and `/auth/callback` are only registered by `create_app()` when
both `oidc_config` and `session_secret` are configured — see `app.py`, the
same all-or-nothing gating `/v1/tokens` already uses for `admin_token`.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from strata.server.routes.security import verify_admin_token
from strata.server.routes.state import (
    get_engine,
    get_pending_logins,
    get_relying_party,
    get_session_secret,
    sweep_pending_logins,
)

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

    The returned token is a stateless, short-lived bearer credential (ADR-0067 Step 7).
    If the identity provider also returned a `refresh_token` (requires the `offline_access`
    scope), a persistent session row is created (Step 8) and its `session_id` is both
    embedded in the access token's claims and returned in the response — present it to
    `/auth/refresh` to silently renew without logging in again. No `refresh_token` in the
    exchange → no session row, no `session_id`; the access token is purely stateless, same
    as before Step 8 existed.
    """
    from strata.server.auth.refresh_crypto import encrypt_refresh_token
    from strata.server.auth.session_tokens import mint_session_token
    from strata.server.db.sessions import create_session

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

    session_id = None
    refresh_token = token_response.get("refresh_token")
    if refresh_token:
        encrypted = encrypt_refresh_token(refresh_token, session_secret)
        session_id = create_session(
            get_engine(request), subject=claims["sub"], encrypted_refresh_token=encrypted, email=claims.get("email")
        )
        claims = {**claims, "session_id": session_id}

    session_token = mint_session_token(claims, session_secret, ttl_seconds=300)
    response: Dict[str, Any] = {
        "access_token": session_token,
        "token_type": "Bearer",
        "expires_in": 300,
        "claims": claims,
    }
    if session_id:
        response["session_id"] = session_id
    return response


@router.post("/auth/refresh")
def auth_refresh(request: Request, session_id: str) -> Dict[str, Any]:
    """Silently renew an access token using a persisted session's refresh token (ADR-0067 Step 8).

    Checked only here, not on every request — the stateless access token stays the
    hot-path credential. A revoked or unknown session fails immediately; the identity
    provider is never even contacted in that case.
    """
    from strata.server.auth.refresh_crypto import decrypt_refresh_token, encrypt_refresh_token
    from strata.server.auth.session_tokens import mint_session_token
    from strata.server.db.sessions import get_session, touch_session

    relying_party = get_relying_party(request)
    assert relying_party is not None  # gated by create_app(); see module docstring
    session_secret = get_session_secret(request)
    assert session_secret is not None  # gated by create_app(); see module docstring
    engine = get_engine(request)

    session = get_session(engine, session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked session")

    refresh_token = decrypt_refresh_token(session["encrypted_refresh_token"], session_secret)
    try:
        ok, token_response = relying_party.refresh_access_token(refresh_token)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not reach identity provider: {exc}") from exc
    if not ok:
        raise HTTPException(
            status_code=401,
            detail=f"Refresh failed: {token_response.get('error_description', token_response)}",
        )

    # The IdP may rotate the refresh token on use; store the new one if so, otherwise
    # keep the existing (still-valid) one untouched.
    rotated_refresh_token = token_response.get("refresh_token")
    new_encrypted = encrypt_refresh_token(rotated_refresh_token, session_secret) if rotated_refresh_token else None
    touch_session(engine, session_id, encrypted_refresh_token=new_encrypted)

    claims = {"sub": session["subject"], "email": session["email"], "session_id": session_id}
    session_token = mint_session_token(claims, session_secret, ttl_seconds=300)
    return {"access_token": session_token, "token_type": "Bearer", "expires_in": 300}


@router.get("/auth/sessions", dependencies=[Depends(verify_admin_token)])
def list_sessions_route(request: Request) -> Dict[str, Any]:
    """List all human login sessions (never the encrypted refresh token) — admin-only (ADR-0067 Step 8).

    The operator-facing "who is currently logged in" view the ADR's own "Session model"
    section calls for.
    """
    from strata.server.db.sessions import list_sessions

    return {"sessions": list_sessions(get_engine(request))}


@router.delete("/auth/sessions/{session_id}", dependencies=[Depends(verify_admin_token)])
def revoke_session_route(request: Request, session_id: str) -> Dict[str, Any]:
    """Revoke a session by id — admin-only (ADR-0067 Step 8).

    The next `/auth/refresh` against this session fails immediately; the literal
    "kick them out right now" the ADR's "Session model" section calls for.
    """
    from strata.server.db.sessions import revoke_session

    if not revoke_session(get_engine(request), session_id):
        raise HTTPException(status_code=404, detail="Session not found or already revoked")
    return {"status": "revoked", "session_id": session_id}
