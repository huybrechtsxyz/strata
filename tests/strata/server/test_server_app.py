"""Tests for create_app() (ADR-0065 Steps 2.1-2.2) using a fake `fastapi` module.

The real `fastapi` package is an optional dependency (`pip install
xyz-strata[server]`) and is deliberately not installed in the dev/test
environment — mirrors the existing `mcp` package's fake-module test pattern
(see tests/strata/commands/test_commands_mcp.py). `sqlalchemy`, however, *is*
a real dev dependency (needs no external service for sqlite), so the engine
passed to create_app() is a real in-memory SQLite engine, not faked.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, Callable, Dict, Generator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _make_fake_fastapi_module() -> ModuleType:
    """Build a minimal fake `fastapi` module — just enough to exercise create_app()."""
    fake_fastapi = ModuleType("fastapi")

    class _FakeFastAPI:
        def __init__(self, title: str = "") -> None:
            self.title = title
            self.routes: Dict[str, Callable[..., Any]] = {}

        def get(self, path: str):
            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self.routes[path] = fn
                return fn

            return decorator

    fake_fastapi.FastAPI = _FakeFastAPI  # type: ignore[attr-defined]
    fake_fastapi.HTTPException = _FakeHTTPError  # type: ignore[attr-defined]
    return fake_fastapi


@pytest.fixture
def fake_fastapi_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake = _make_fake_fastapi_module()
    monkeypatch.setitem(sys.modules, "fastapi", fake)
    return fake


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    engine = create_engine("sqlite:///:memory:")
    yield engine
    engine.dispose()


class TestCreateApp:
    def test_registers_healthz_route(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert "/healthz" in app.routes

    def test_registers_no_other_routes(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        """Step 2.1 deliberately adds only /healthz — no /v1/events yet."""
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        assert list(app.routes.keys()) == ["/healthz"]

    def test_healthz_returns_ok_when_db_reachable(self, fake_fastapi_module: ModuleType, sqlite_engine: Engine) -> None:
        from strata.server.app import create_app

        app = create_app(sqlite_engine)
        handler = app.routes["/healthz"]
        assert handler() == {"status": "ok"}

    def test_healthz_raises_503_when_db_unreachable(self, fake_fastapi_module: ModuleType) -> None:
        from strata.server.app import create_app

        broken_engine = MagicMock()
        broken_engine.connect.side_effect = RuntimeError("connection refused")

        app = create_app(broken_engine)
        handler = app.routes["/healthz"]

        with pytest.raises(_FakeHTTPError) as exc_info:
            handler()
        assert exc_info.value.status_code == 503
