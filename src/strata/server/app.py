"""ASGI application factory for the strata state-service server.

A thin factory: build the `FastAPI` app, stash per-instance runtime config on
`app.state` (the same singleton-config surface real FastAPI apps already use
this for), and assemble routers — one per concern, `strata.server.routes.*` —
via `app.include_router()`. This is FastAPI's own recommended shape once a
handful of closures-in-one-function stops scaling ("Bigger Applications -
Multiple Files"); route logic itself lives in `routes/health.py`,
`routes/events.py`, `routes/tokens.py`, `routes/auth.py`.

``GET /healthz`` exists from Step 2.1 onward; from Step 2.2 onward it also
verifies database connectivity. ``POST /v1/events`` (Step 2.3) is the ingest
route — the body is always a CloudEvents 1.0 + ECS envelope
(``AuditController._build_envelope()``'s output), never a raw artifact dump.
Step 2.4 adds bearer-token auth on ``/v1/events`` (per-workspace ingest
tokens, verified against the ``tokens`` table) and, when ``admin_token`` is
configured, admin routes (``/v1/tokens``) for managing those tokens over
HTTP — so that, in steady state, nobody but the server process itself ever
needs a direct database connection (the one exception is ``serve migrate``,
which must run before any table — including ``tokens`` — exists at all).
ADR-0067 Step 7 adds ``/auth/login``/``/auth/callback`` when both
``oidc_config`` and ``session_secret`` are configured.

Requires the optional dependency: pip install xyz-strata[server]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
except ImportError as _exc:
    raise ImportError(
        "The 'fastapi' package is required for the strata state-service server.\n"
        "Install it with: pip install xyz-strata[server]"
    ) from _exc

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from strata.server.auth.oidc_relying_party import OidcRelyingPartyConfig


def create_app(
    engine: "Engine",
    admin_token: Optional[str] = None,
    oidc_config: Optional["OidcRelyingPartyConfig"] = None,
    session_secret: Optional[str] = None,
) -> FastAPI:
    """Build the FastAPI app. Requires ``pip install xyz-strata[server]``.

    `admin_token` enables the token-management routes (`/v1/tokens`) when
    provided — deliberately unregistered otherwise, so there is never an
    unauthenticated admin surface by accident.

    `oidc_config` + `session_secret` together enable the `/auth/login` and
    `/auth/callback` routes (ADR-0067 Step 7) — both are required, or neither
    route is registered at all, the same all-or-nothing gating `/v1/tokens`
    already uses for `admin_token`.
    """
    from strata.server.auth.oidc_relying_party import OidcRelyingParty
    from strata.server.routes import auth, events, health, tokens

    app = FastAPI(title="strata state service")

    # Per-instance runtime config, read by routes/state.py's accessors — never
    # closures, so route modules can be split out and tested independently.
    app.state.engine = engine
    app.state.admin_token = admin_token
    app.state.relying_party = OidcRelyingParty(oidc_config) if oidc_config else None
    app.state.session_secret = session_secret
    # In-memory only — a pending login does not survive a restart or work behind
    # multiple replicas. See routes/state.py's PENDING_LOGIN_TTL docstring.
    app.state.pending_logins = {}

    # Wide open for now — the read-only dashboard (a separate React app, no auth yet)
    # runs on its own origin/port and needs to call this API directly from the browser.
    # Tighten this once the dashboard grows real authentication.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(events.router)

    if admin_token:
        app.include_router(tokens.router)

    if app.state.relying_party and session_secret:
        app.include_router(auth.router)

    return app
