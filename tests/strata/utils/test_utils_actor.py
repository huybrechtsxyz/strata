#!/usr/bin/env python3
"""Tests for resolve_actor() — docs/design/audit-trail.md's "Actor/identity".

Phase 3 of that doc's Layer 2 Implementation Plan.
"""

from strata.utils.actor import resolve_actor


def test_prefers_build_requestedfor(monkeypatch):
    monkeypatch.setenv("BUILD_REQUESTEDFOR", "jane.doe")
    monkeypatch.setenv("BUILD_REQUESTEDFOREMAIL", "jane.doe@example.com")
    assert resolve_actor() == "jane.doe"


def test_falls_back_to_build_requestedforemail(monkeypatch):
    monkeypatch.delenv("BUILD_REQUESTEDFOR", raising=False)
    monkeypatch.setenv("BUILD_REQUESTEDFOREMAIL", "jane.doe@example.com")
    assert resolve_actor() == "jane.doe@example.com"


def test_falls_back_to_os_user(monkeypatch):
    monkeypatch.delenv("BUILD_REQUESTEDFOR", raising=False)
    monkeypatch.delenv("BUILD_REQUESTEDFOREMAIL", raising=False)
    monkeypatch.setattr("getpass.getuser", lambda: "local-dev")
    assert resolve_actor() == "local-dev"


def test_falls_back_to_unknown_when_everything_fails(monkeypatch):
    monkeypatch.delenv("BUILD_REQUESTEDFOR", raising=False)
    monkeypatch.delenv("BUILD_REQUESTEDFOREMAIL", raising=False)

    def _raise():
        raise OSError("no login name")

    monkeypatch.setattr("getpass.getuser", lambda: _raise())
    assert resolve_actor() == "unknown"


def test_never_raises_even_with_empty_environment(monkeypatch):
    monkeypatch.delenv("BUILD_REQUESTEDFOR", raising=False)
    monkeypatch.delenv("BUILD_REQUESTEDFOREMAIL", raising=False)
    # Whatever the real OS user resolves to (or "unknown" in a stripped
    # container), this must not raise.
    assert isinstance(resolve_actor(), str)
