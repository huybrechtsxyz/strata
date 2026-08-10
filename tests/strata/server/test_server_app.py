"""Tests for create_app() (ADR-0065 Step 2.1) using a fake `fastapi` module.

The real `fastapi` package is an optional dependency (`pip install
xyz-strata[server]`) and is deliberately not installed in the dev/test
environment — mirrors the existing `mcp` package's fake-module test pattern
(see tests/strata/commands/test_commands_mcp.py).
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, Callable, Dict

import pytest


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
    return fake_fastapi


@pytest.fixture
def fake_fastapi_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake = _make_fake_fastapi_module()
    monkeypatch.setitem(sys.modules, "fastapi", fake)
    return fake


class TestCreateApp:
    def test_registers_healthz_route(self, fake_fastapi_module: ModuleType) -> None:
        from strata.server.app import create_app

        app = create_app()
        assert "/healthz" in app.routes

    def test_healthz_handler_returns_ok_status(self, fake_fastapi_module: ModuleType) -> None:
        from strata.server.app import create_app

        app = create_app()
        handler = app.routes["/healthz"]
        assert handler() == {"status": "ok"}

    def test_registers_no_other_routes(self, fake_fastapi_module: ModuleType) -> None:
        """Step 2.1 deliberately adds only /healthz — no /v1/events yet."""
        from strata.server.app import create_app

        app = create_app()
        assert list(app.routes.keys()) == ["/healthz"]
