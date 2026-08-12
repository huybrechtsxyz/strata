"""Tests for create_app() (ADR-0065 Steps 2.1-2.4) using a fake `fastapi` module.

The real `fastapi` package is an optional dependency (`pip install
xyz-strata[server]`) and is deliberately not installed in the dev/test
environment — mirrors the existing `mcp` package's fake-module test pattern
(see tests/strata/commands/test_commands_mcp.py). `sqlalchemy`, however, *is*
a real dev dependency (needs no external service for sqlite), so the engine
passed to create_app() is a real in-memory SQLite engine, not faked.

The fake FastAPI captures each route's `dependencies=[Depends(fn)]` list so
tests can run them exactly like real FastAPI would — before the route
handler itself — since Step 2.4 added auth dependencies to /v1/events and
/v1/tokens.
"""

from __future__ import annotations

import datetime
import inspect
import json
import sys
import urllib.parse
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Dict, Generator, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from strata.server.db.schema import metadata
from strata.server.db.tokens import create_token


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class _FakeRequest:
    def __init__(self, headers: Dict[str, str] | None = None, app: Any = None) -> None:
        self.headers = headers or {}
        self.app = app


def _make_fake_fastapi_module() -> ModuleType:
    """Build a minimal fake `fastapi` module — just enough to exercise create_app()."""
    fake_fastapi = ModuleType("fastapi")

    class _FakeRouteRegistrar:
        """Shared route-registration behaviour for both `FastAPI` and `APIRouter` fakes —
        real FastAPI's `APIRouter` supports the exact same `.get()`/`.post()`/`.delete()`
        decorators as the app itself, which is what `include_router()` relies on.
        """

        def __init__(self) -> None:
            # Keyed by (method, path) — /v1/tokens has both a GET and a POST route.
            self.routes: Dict[tuple, Callable[..., Any]] = {}
            self.dependencies: Dict[tuple, List[Callable[..., Any]]] = {}

        def _register(self, method: str, path: str, dependencies: Optional[List[Callable[..., Any]]]):
            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self.routes[(method, path)] = fn
                self.dependencies[(method, path)] = dependencies or []
                return fn

            return decorator

        def get(self, path: str, dependencies: Optional[List[Callable[..., Any]]] = None):
            return self._register("GET", path, dependencies)

        def post(self, path: str, status_code: int = 200, dependencies: Optional[List[Callable[..., Any]]] = None):
            return self._register("POST", path, dependencies)

        def delete(self, path: str, dependencies: Optional[List[Callable[..., Any]]] = None):
            return self._register("DELETE", path, dependencies)

    class _FakeAPIRouter(_FakeRouteRegistrar):
        """Fakes `fastapi.APIRouter` — one per route module (`routes/health.py` etc.)."""

    class _FakeFastAPI(_FakeRouteRegistrar):
        def __init__(self, title: str = "") -> None:
            super().__init__()
            self.title = title
            self.middlewares: List[Any] = []
            # Real FastAPI's `Starlette.state` — an arbitrary-attribute bag route
            # modules read per-app-instance config from (see `routes/state.py`).
            self.state = SimpleNamespace()

        def include_router(self, router: "_FakeAPIRouter") -> None:
            """Fakes `app.include_router()` — merges a router's routes into this app's own."""
            self.routes.update(router.routes)
            self.dependencies.update(router.dependencies)

        def add_middleware(self, middleware_class: Any, **kwargs: Any) -> None:
            self.middlewares.append((middleware_class, kwargs))

    fake_fastapi.FastAPI = _FakeFastAPI  # type: ignore[attr-defined]
    fake_fastapi.APIRouter = _FakeAPIRouter  # type: ignore[attr-defined]
    fake_fastapi.HTTPException = _FakeHTTPError  # type: ignore[attr-defined]
    fake_fastapi.Request = _FakeRequest  # type: ignore[attr-defined]
    fake_fastapi.Body = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    # Real FastAPI wraps a callable in a marker object; since this fake never
    # performs real dependency injection (tests call dependencies explicitly,
    # see _run_dependencies below), returning the callable unchanged is enough.
    fake_fastapi.Depends = lambda fn: fn  # type: ignore[attr-defined]
    return fake_fastapi


def _make_fake_fastapi_middleware_cors_module() -> ModuleType:
    """Build the fake `fastapi.middleware.cors` submodule `app.py` imports `CORSMiddleware` from."""

    class _FakeCORSMiddleware:
        def __init__(self, app: Any, **kwargs: Any) -> None:
            self.app = app
            self.kwargs = kwargs

    fake_cors = ModuleType("fastapi.middleware.cors")
    fake_cors.CORSMiddleware = _FakeCORSMiddleware  # type: ignore[attr-defined]
    return fake_cors


@pytest.fixture
def fake_fastapi_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake = _make_fake_fastapi_module()
    monkeypatch.setitem(sys.modules, "fastapi", fake)
    fake_middleware = ModuleType("fastapi.middleware")
    fake_cors = _make_fake_fastapi_middleware_cors_module()
    monkeypatch.setitem(sys.modules, "fastapi.middleware", fake_middleware)
    monkeypatch.setitem(sys.modules, "fastapi.middleware.cors", fake_cors)
    return fake


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine, checkfirst=True)
    yield engine
    engine.dispose()


def _call_route(app: Any, method: str, path: str, headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> Any:
    """Run a route's dependencies (as real FastAPI would, before the handler), then the handler itself.

    Always passes `request` to the handler if its signature declares it — most
    route handlers now read per-instance config off `request.app.state` (see
    `routes/state.py`) rather than closing over `create_app()`'s locals.
    """
    request = _FakeRequest(headers=headers, app=app)
    key = (method, path)
    for dependency in app.dependencies.get(key, []):
        dependency(request)
    handler = app.routes[key]
    if "request" in inspect.signature(handler).parameters:
        kwargs = {"request": request, **kwargs}
    return handler(**kwargs)


class TestCreateApp:
    def test_registers_healthz_route(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert ("GET", "/healthz") in app.routes

    def test_registers_events_and_healthz_only_without_admin_token(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        """No admin_token configured -> /v1/tokens routes are not registered at all."""
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert set(app.routes.keys()) == {
            ("GET", "/healthz"),
            ("GET", "/v1/workspaces"),
            ("POST", "/v1/events"),
            ("GET", "/v1/events/tail"),
        }

    def test_registers_token_routes_when_admin_token_configured(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        assert set(app.routes.keys()) == {
            ("GET", "/healthz"),
            ("GET", "/v1/workspaces"),
            ("POST", "/v1/events"),
            ("GET", "/v1/events/tail"),
            ("POST", "/v1/tokens"),
            ("GET", "/v1/tokens"),
            ("DELETE", "/v1/tokens/{token_id}"),
        }

    def test_healthz_returns_ok_when_db_reachable(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert _call_route(app, "GET", "/healthz", headers={}) == {"status": "ok"}

    def test_healthz_raises_503_when_db_unreachable(self, fake_fastapi_module: ModuleType) -> None:
        from strata.server.app import create_app

        broken_engine = MagicMock()
        broken_engine.connect.side_effect = RuntimeError("connection refused")

        app = create_app(broken_engine)

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "GET", "/healthz", headers={})
        assert exc_info.value.status_code == 503

    def test_healthz_requires_no_auth(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        """/healthz must stay reachable without a token — health probes need this."""
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        assert app.dependencies.get(("GET", "/healthz"), []) == []


class TestWorkspacesRoute:
    """GET /v1/workspaces — unauthenticated, read-only, backs the React dashboard."""

    def test_requires_no_auth(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        assert app.dependencies.get(("GET", "/v1/workspaces"), []) == []

    def test_returns_distinct_workspaces_sorted(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app
        from strata.server.db.store import insert_event

        for execution_id, workspace in [("exec-a", "beta"), ("exec-b", "alpha"), ("exec-c", "beta")]:
            insert_event(
                sqlite_engine,
                {
                    "execution_id": execution_id,
                    "record_type": "xyz.huybrechts.strata.deployment.completed",
                    "recorded_at": datetime.datetime.now(datetime.timezone.utc),
                    "workspace": workspace,
                    "payload": {},
                },
            )
        app = create_app(sqlite_engine)

        result = _call_route(app, "GET", "/v1/workspaces", headers={})

        assert result == {"workspaces": ["alpha", "beta"]}

    def test_empty_store_returns_empty_list(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)

        result = _call_route(app, "GET", "/v1/workspaces", headers={})

        assert result == {"workspaces": []}


def _make_envelope(**overrides: Any) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {
        "specversion": "1.0",
        "type": "xyz.huybrechts.strata.deployment.completed",
        "source": "/strata/my-workspace/my-deploy",
        "id": "11111111-1111-1111-1111-111111111111",
        "time": "2026-08-10T12:00:00+00:00",
        "data": {
            "event": {"kind": "event", "action": "deployment-completed", "outcome": "success"},
            "labels": {"execution_id": "exec-123", "deployment": "my-deploy"},
            "strata": {"execution_id": "exec-123"},
        },
    }
    envelope.update(overrides)
    return envelope


@pytest.fixture
def ingest_token(sqlite_engine: Engine) -> str:
    """A real, active ingest token for 'my-workspace' — returns the plaintext secret."""
    return create_token(sqlite_engine, "my-workspace")["token"]


class TestIngestEvent:
    def _post(self, app: Any, body: bytes, token: str, headers: Optional[Dict[str, str]] = None) -> Any:
        merged_headers = {"authorization": f"Bearer {token}", **(headers or {})}
        return _call_route(app, "POST", "/v1/events", headers=merged_headers, body=body)

    def test_valid_envelope_returns_accepted_and_inserts_row(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app
        from strata.server.db.schema import events

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        result = self._post(app, body, ingest_token)

        assert result == {"status": "accepted"}
        with sqlite_engine.connect() as conn:
            rows = conn.execute(events.select()).fetchall()
        assert len(rows) == 1

    def test_duplicate_envelope_is_a_noop_and_still_returns_accepted(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app
        from strata.server.db.schema import events

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        first = self._post(app, body, ingest_token)
        second = self._post(app, body, ingest_token)

        assert first == {"status": "accepted"}
        assert second == {"status": "accepted"}
        with sqlite_engine.connect() as conn:
            rows = conn.execute(events.select()).fetchall()
        assert len(rows) == 1

    def test_malformed_json_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, b"{not json", ingest_token)
        assert exc_info.value.status_code == 400

    def test_non_object_json_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, b"[1, 2, 3]", ingest_token)
        assert exc_info.value.status_code == 400

    def test_missing_execution_id_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        envelope = _make_envelope()
        del envelope["data"]["labels"]["execution_id"]
        body = json.dumps(envelope).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, body, ingest_token)
        assert exc_info.value.status_code == 400

    def test_oversized_body_returns_413(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        huge_body = b"x" * (256 * 1024 + 1)

        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, huge_body, ingest_token)
        assert exc_info.value.status_code == 413

    def test_oversized_content_length_header_rejected_before_parsing(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        small_body = b"{}"

        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, small_body, ingest_token, headers={"content-length": str(256 * 1024 + 1)})
        assert exc_info.value.status_code == 413

    def test_insert_failure_returns_503(self, fake_fastapi_module: ModuleType, ingest_token: str) -> None:
        from strata.server.app import create_app

        broken_engine = MagicMock()
        broken_engine.begin.side_effect = RuntimeError("connection lost")
        # verify_token also needs the (broken) engine — patch it to report the token as valid
        # so the test isolates the insert failure, not an auth failure.
        broken_engine.connect.return_value.__enter__.return_value.execute.return_value.mappings.return_value.first.return_value = {
            "workspace": "my-workspace"
        }

        app = create_app(broken_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            self._post(app, body, ingest_token)
        assert exc_info.value.status_code == 503


class TestIngestEventAuth:
    def test_missing_authorization_header_returns_401(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/events", headers={}, body=body)
        assert exc_info.value.status_code == 401

    def test_malformed_authorization_header_returns_401(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/events", headers={"authorization": ingest_token}, body=body)
        assert exc_info.value.status_code == 401

    def test_wrong_token_returns_403(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine, ingest_token: str
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/events", headers={"authorization": "Bearer wrong-token"}, body=body)
        assert exc_info.value.status_code == 403

    def test_revoked_token_returns_403(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app
        from strata.server.db.tokens import revoke_token

        created = create_token(sqlite_engine, "my-workspace")
        revoke_token(sqlite_engine, created["token_id"])

        app = create_app(sqlite_engine)
        body = json.dumps(_make_envelope()).encode("utf-8")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/events", headers={"authorization": f"Bearer {created['token']}"}, body=body)
        assert exc_info.value.status_code == 403


class TestTokenRoutes:
    def _admin_headers(self, token: str = "admin-secret") -> Dict[str, str]:
        return {"authorization": f"Bearer {token}"}

    def test_create_token_route_returns_token_and_id(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        result = _call_route(app, "POST", "/v1/tokens", headers=self._admin_headers(), workspace="my-workspace")

        assert "token_id" in result
        assert "token" in result

    def test_create_token_route_missing_admin_auth_returns_401(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/tokens", headers={}, workspace="my-workspace")
        assert exc_info.value.status_code == 401

    def test_create_token_route_wrong_admin_token_returns_403(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/v1/tokens", headers=self._admin_headers("wrong"), workspace="my-workspace")
        assert exc_info.value.status_code == 403

    def test_list_tokens_route_returns_created_tokens(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        create_token(sqlite_engine, "my-workspace")
        app = create_app(sqlite_engine, admin_token="admin-secret")

        result = _call_route(app, "GET", "/v1/tokens", headers=self._admin_headers(), workspace=None)

        assert len(result["tokens"]) == 1
        assert "token_hash" not in result["tokens"][0]
        assert "token" not in result["tokens"][0]

    def test_revoke_token_route_marks_token_revoked(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        created = create_token(sqlite_engine, "my-workspace")
        app = create_app(sqlite_engine, admin_token="admin-secret")

        result = _call_route(
            app, "DELETE", "/v1/tokens/{token_id}", headers=self._admin_headers(), token_id=created["token_id"]
        )

        assert result == {"status": "revoked", "token_id": created["token_id"]}

    def test_revoke_unknown_token_returns_404(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, admin_token="admin-secret")
        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(
                app, "DELETE", "/v1/tokens/{token_id}", headers=self._admin_headers(), token_id="does-not-exist"
            )
        assert exc_info.value.status_code == 404


class TestTailRoute:
    """ADR-0065 Step 2.6 — GET /v1/events/tail."""

    def _tail(self, app: Any, headers: Dict[str, str], **kwargs: Any) -> Any:
        return _call_route(app, "GET", "/v1/events/tail", headers=headers, **kwargs)

    def test_missing_authorization_header_is_unrestricted(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        """TEMPORARY relaxation: no token is treated as unrestricted access, not a 401.

        Backs the no-auth v0 read-only React dashboard. See `resolve_read_scope()`'s
        docstring in `app.py` for the plan to require a token again once the
        dashboard grows real authentication.
        """
        from strata.server.app import create_app

        self._insert(sqlite_engine, "exec-a", "workspace-a")
        self._insert(sqlite_engine, "exec-b", "workspace-b")
        app = create_app(sqlite_engine)

        result = self._tail(app, {}, limit=100, workspace=None)

        assert {event["execution_id"] for event in result["events"]} == {"exec-a", "exec-b"}

    def test_wrong_token_returns_403(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        with pytest.raises(_FakeHTTPError) as exc_info:
            self._tail(app, {"authorization": "Bearer wrong-token"}, limit=100, workspace=None)
        assert exc_info.value.status_code == 403

    def _insert(self, engine: Engine, execution_id: str, workspace: str) -> None:
        from strata.server.db.store import insert_event

        insert_event(
            engine,
            {
                "execution_id": execution_id,
                "record_type": "xyz.huybrechts.strata.deployment.completed",
                "recorded_at": datetime.datetime.now(datetime.timezone.utc),
                "workspace": workspace,
                "payload": {},
            },
        )

    def test_ingest_token_returns_only_its_own_workspace_events(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        self._insert(sqlite_engine, "exec-a", "workspace-a")
        self._insert(sqlite_engine, "exec-b", "workspace-b")
        token = create_token(sqlite_engine, "workspace-a")["token"]
        app = create_app(sqlite_engine)

        result = self._tail(app, {"authorization": f"Bearer {token}"}, limit=100, workspace=None)

        assert [event["execution_id"] for event in result["events"]] == ["exec-a"]

    def test_ingest_token_cannot_widen_scope_via_workspace_param(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        self._insert(sqlite_engine, "exec-b", "workspace-b")
        token = create_token(sqlite_engine, "workspace-a")["token"]
        app = create_app(sqlite_engine)

        # Attempting to request workspace-b's events with a workspace-a token must not leak them.
        result = self._tail(app, {"authorization": f"Bearer {token}"}, limit=100, workspace="workspace-b")

        assert result["events"] == []

    def test_admin_token_can_see_all_workspaces(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        self._insert(sqlite_engine, "exec-a", "workspace-a")
        self._insert(sqlite_engine, "exec-b", "workspace-b")
        app = create_app(sqlite_engine, admin_token="admin-secret")

        result = self._tail(app, {"authorization": "Bearer admin-secret"}, limit=100, workspace=None)

        assert {event["execution_id"] for event in result["events"]} == {"exec-a", "exec-b"}

    def test_limit_is_capped_server_side(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app
        from strata.server.routes.events import _MAX_TAIL_LIMIT

        for i in range(3):
            self._insert(sqlite_engine, f"exec-{i}", "my-workspace")
        token = create_token(sqlite_engine, "my-workspace")["token"]
        app = create_app(sqlite_engine)

        # Requesting far above the cap must not error and must not return more than the cap.
        result = self._tail(app, {"authorization": f"Bearer {token}"}, limit=_MAX_TAIL_LIMIT * 10, workspace=None)

        assert len(result["events"]) <= _MAX_TAIL_LIMIT
        assert len(result["events"]) == 3


class TestAuthRoutes:
    """ADR-0067 Step 7 — /auth/login + /auth/callback registration gating and the happy path."""

    _ISSUER = "https://idp.example.test"
    _CLIENT_ID = "strata-control-plane"
    _REDIRECT_BASE = "https://control-plane.example.test"

    def _oidc_config(self) -> Any:
        from strata.server.auth.oidc_relying_party import OidcRelyingPartyConfig

        return OidcRelyingPartyConfig(issuer=self._ISSUER, client_id=self._CLIENT_ID, redirect_base=self._REDIRECT_BASE)

    def _discovery_doc(self) -> Dict[str, Any]:
        return {
            "issuer": self._ISSUER,
            "authorization_endpoint": f"{self._ISSUER}/authorize",
            "token_endpoint": f"{self._ISSUER}/token",
            "userinfo_endpoint": f"{self._ISSUER}/userinfo",
            "jwks_uri": f"{self._ISSUER}/.well-known/jwks.json",
        }

    def _sign_id_token(self, rsa_key: Any, nonce: str, **overrides: Any) -> str:
        import time as _time

        from joserfc import jwt as joserfc_jwt

        now = int(_time.time())
        claims = {
            "iss": self._ISSUER,
            "aud": self._CLIENT_ID,
            "sub": "user-123",
            "email": "user@example.test",
            "exp": now + 300,
            "iat": now,
            "nonce": nonce,
        }
        claims.update(overrides)
        return joserfc_jwt.encode({"alg": "RS256", "kid": rsa_key.kid}, claims, rsa_key)

    def _urlopen_mock(
        self,
        discovery: Dict[str, Any],
        rsa_key: Any,
        token_response: Optional[Dict[str, Any]] = None,
        userinfo_response: Optional[Dict[str, Any]] = None,
    ) -> Any:
        import json as _json

        class _FakeResponse:
            def __init__(self, payload: Dict[str, Any]) -> None:
                self._body = _json.dumps(payload).encode("utf-8")
                self.status = 200

            def read(self) -> bytes:
                return self._body

            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

        def _urlopen(req: Any, timeout: Any = None) -> _FakeResponse:
            url = req.full_url if hasattr(req, "full_url") else req
            if url == f"{self._ISSUER}/.well-known/openid-configuration":
                return _FakeResponse(discovery)
            if url == discovery["jwks_uri"]:
                return _FakeResponse({"keys": [rsa_key.as_dict(private=False)]})
            if url == discovery["token_endpoint"]:
                return _FakeResponse(token_response or {"error": "not configured"})
            if url == discovery["userinfo_endpoint"]:
                return _FakeResponse(userinfo_response or {})
            raise AssertionError(f"Unexpected URL requested in test: {url}")

        return _urlopen

    def test_not_registered_without_oidc_config(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert ("GET", "/auth/login") not in app.routes
        assert ("GET", "/auth/callback") not in app.routes

    def test_not_registered_with_only_oidc_config_and_no_session_secret(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, oidc_config=self._oidc_config())
        assert ("GET", "/auth/login") not in app.routes

    def test_registered_when_fully_configured(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")
        assert ("GET", "/auth/login") in app.routes
        assert ("GET", "/auth/callback") in app.routes

    def test_login_returns_authorization_url_and_state(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            result = _call_route(app, "GET", "/auth/login", headers={})

        assert result["authorization_url"].startswith(f"{self._ISSUER}/authorize?")
        assert "state" in result

    def test_full_login_round_trip_returns_session_token(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            login = _call_route(app, "GET", "/auth/login", headers={})

        state = login["state"]
        # Recover the nonce that /auth/login generated internally via the authorization_url.
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(login["authorization_url"]).query)["nonce"][0]
        id_token = self._sign_id_token(rsa_key, nonce=nonce)
        token_response = {"access_token": "at-1", "id_token": id_token, "expires_in": 3600}

        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(discovery, rsa_key, token_response=token_response),
        ):
            result = _call_route(app, "GET", "/auth/callback", headers={}, code="auth-code", state=state)

        assert result["token_type"] == "Bearer"
        assert result["claims"]["sub"] == "user-123"
        assert "session_id" not in result  # no refresh_token in the exchange -> no session row (Step 8)

    def test_callback_with_unknown_state_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "GET", "/auth/callback", headers={}, code="auth-code", state="never-issued")
        assert exc_info.value.status_code == 400

    def test_callback_with_wrong_nonce_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            login = _call_route(app, "GET", "/auth/login", headers={})

        # Sign the id_token with a nonce that does NOT match the one /auth/login generated.
        id_token = self._sign_id_token(rsa_key, nonce="wrong-nonce")
        token_response = {"access_token": "at-1", "id_token": id_token, "expires_in": 3600}

        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(discovery, rsa_key, token_response=token_response),
        ):
            with pytest.raises(_FakeHTTPError) as exc_info:
                _call_route(app, "GET", "/auth/callback", headers={}, code="auth-code", state=login["state"])
        assert exc_info.value.status_code == 400

    def test_callback_missing_id_token_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            login = _call_route(app, "GET", "/auth/login", headers={})

        token_response = {"access_token": "at-1", "expires_in": 3600}  # no id_token
        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(discovery, rsa_key, token_response=token_response),
        ):
            with pytest.raises(_FakeHTTPError) as exc_info:
                _call_route(app, "GET", "/auth/callback", headers={}, code="auth-code", state=login["state"])
        assert exc_info.value.status_code == 400

    def test_callback_failed_code_exchange_returns_400(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            login = _call_route(app, "GET", "/auth/login", headers={})

        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(discovery, rsa_key, token_response={"error": "invalid_grant"}),
        ):
            with pytest.raises(_FakeHTTPError) as exc_info:
                _call_route(app, "GET", "/auth/callback", headers={}, code="bad-code", state=login["state"])
        assert exc_info.value.status_code == 400

    def _login_and_callback_with_refresh_token(
        self, app: Any, discovery: Dict[str, Any], rsa_key: Any
    ) -> Dict[str, Any]:
        """Helper: run /auth/login -> /auth/callback with a refresh_token in the exchange, creating a session."""
        with patch("urllib.request.urlopen", side_effect=self._urlopen_mock(discovery, rsa_key)):
            login = _call_route(app, "GET", "/auth/login", headers={})

        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(login["authorization_url"]).query)["nonce"][0]
        id_token = self._sign_id_token(rsa_key, nonce=nonce)
        token_response = {
            "access_token": "at-1",
            "id_token": id_token,
            "refresh_token": "the-refresh-token",
            "expires_in": 3600,
        }
        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(discovery, rsa_key, token_response=token_response),
        ):
            return _call_route(app, "GET", "/auth/callback", headers={}, code="auth-code", state=login["state"])

    def test_callback_with_refresh_token_creates_a_session(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app
        from strata.server.db.sessions import get_session

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        result = self._login_and_callback_with_refresh_token(app, discovery, rsa_key)

        assert "session_id" in result
        assert result["claims"]["session_id"] == result["session_id"]
        row = get_session(sqlite_engine, result["session_id"])
        assert row is not None
        assert row["subject"] == "user-123"
        assert row["encrypted_refresh_token"] != "the-refresh-token"  # encrypted, not stored raw

    def test_auth_refresh_returns_new_access_token(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        login_result = self._login_and_callback_with_refresh_token(app, discovery, rsa_key)
        session_id = login_result["session_id"]

        with patch(
            "urllib.request.urlopen",
            side_effect=self._urlopen_mock(
                discovery, rsa_key, token_response={"access_token": "at-2", "expires_in": 3600}
            ),
        ):
            result = _call_route(app, "POST", "/auth/refresh", headers={}, session_id=session_id)

        assert result["token_type"] == "Bearer"
        assert result["access_token"]

    def test_auth_refresh_with_unknown_session_returns_401(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/auth/refresh", headers={}, session_id="never-issued")
        assert exc_info.value.status_code == 401

    def test_auth_refresh_with_revoked_session_returns_401(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app
        from strata.server.db.sessions import revoke_session

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(sqlite_engine, oidc_config=self._oidc_config(), session_secret="shh")

        login_result = self._login_and_callback_with_refresh_token(app, discovery, rsa_key)
        revoke_session(sqlite_engine, login_result["session_id"])

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/auth/refresh", headers={}, session_id=login_result["session_id"])
        assert exc_info.value.status_code == 401

    def test_list_sessions_requires_admin_token(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(
            sqlite_engine, admin_token="admin-secret", oidc_config=self._oidc_config(), session_secret="shh"
        )

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "GET", "/auth/sessions", headers={})
        assert exc_info.value.status_code == 401

    def test_list_sessions_with_admin_token_returns_sessions(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(
            sqlite_engine, admin_token="admin-secret", oidc_config=self._oidc_config(), session_secret="shh"
        )

        self._login_and_callback_with_refresh_token(app, discovery, rsa_key)

        result = _call_route(app, "GET", "/auth/sessions", headers={"authorization": "Bearer admin-secret"})

        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["subject"] == "user-123"
        assert "encrypted_refresh_token" not in result["sessions"][0]

    def test_revoke_session_route_with_admin_token(
        self, fake_fastapi_module: ModuleType, sqlite_engine: Engine
    ) -> None:
        from joserfc.jwk import RSAKey

        from strata.server.app import create_app

        rsa_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        discovery = self._discovery_doc()
        app = create_app(
            sqlite_engine, admin_token="admin-secret", oidc_config=self._oidc_config(), session_secret="shh"
        )

        login_result = self._login_and_callback_with_refresh_token(app, discovery, rsa_key)
        session_id = login_result["session_id"]

        result = _call_route(
            app,
            "DELETE",
            "/auth/sessions/{session_id}",
            headers={"authorization": "Bearer admin-secret"},
            session_id=session_id,
        )
        assert result == {"status": "revoked", "session_id": session_id}

        # The very next refresh attempt against this session must now fail.
        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(app, "POST", "/auth/refresh", headers={}, session_id=session_id)
        assert exc_info.value.status_code == 401

    def test_revoke_unknown_session_returns_404(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(
            sqlite_engine, admin_token="admin-secret", oidc_config=self._oidc_config(), session_secret="shh"
        )

        with pytest.raises(_FakeHTTPError) as exc_info:
            _call_route(
                app,
                "DELETE",
                "/auth/sessions/{session_id}",
                headers={"authorization": "Bearer admin-secret"},
                session_id="does-not-exist",
            )
        assert exc_info.value.status_code == 404
