#!/usr/bin/env python3
"""Tests for `InfisicalResolver` (ADR-0021 D7 retrofit — urllib -> http_request)."""

import json

import pytest

from strata.integrations import infisical_resolver as module
from strata.integrations.errors import ValueResolutionError
from strata.integrations.infisical_resolver import InfisicalResolver
from strata.utils.transport import HttpResult


def test_class_declares_its_contract():
    assert InfisicalResolver.TYPE == "infisical"
    assert InfisicalResolver.CAPABILITIES == {"variables", "secrets"}
    assert InfisicalResolver.TRANSPORTS == {"http"}


def test_resolve_with_a_service_token(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "svc-token")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")

    captured: dict[str, object] = {}

    def _fake_http_request(method, url, *, headers=None, body=None, timeout=30):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        payload = json.dumps({"secrets": [{"secretKey": "DB_PASSWORD", "secretValue": "hunter2"}]})
        return HttpResult(status=200, body=payload)

    monkeypatch.setattr(module, "http_request", _fake_http_request)

    resolver = InfisicalResolver()
    assert resolver.resolve("DB_PASSWORD") == "hunter2"
    assert captured["headers"] == {"Authorization": "Bearer svc-token"}
    assert "workspaceId=proj-1" in str(captured["url"])


def test_resolve_caches_across_multiple_keys_in_one_instance(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "svc-token")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")

    call_count = {"n": 0}

    def _fake_http_request(method, url, *, headers=None, body=None, timeout=30):
        call_count["n"] += 1
        payload = json.dumps(
            {"secrets": [{"secretKey": "A", "secretValue": "1"}, {"secretKey": "B", "secretValue": "2"}]}
        )
        return HttpResult(status=200, body=payload)

    monkeypatch.setattr(module, "http_request", _fake_http_request)

    resolver = InfisicalResolver()
    assert resolver.resolve("A") == "1"
    assert resolver.resolve("B") == "2"
    assert call_count["n"] == 1


def test_resolve_missing_key_raises(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "svc-token")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")
    monkeypatch.setattr(module, "http_request", lambda *a, **k: HttpResult(status=200, body=json.dumps({"secrets": []})))

    resolver = InfisicalResolver()
    with pytest.raises(ValueResolutionError, match="no secret named"):
        resolver.resolve("GHOST")


def test_resolve_without_project_id_raises(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "svc-token")
    monkeypatch.delenv("INFISICAL_PROJECT_ID", raising=False)

    resolver = InfisicalResolver()
    with pytest.raises(ValueResolutionError, match="INFISICAL_PROJECT_ID"):
        resolver.resolve("ANY")


def test_resolve_without_credentials_raises(monkeypatch):
    monkeypatch.delenv("INFISICAL_TOKEN", raising=False)
    monkeypatch.delenv("INFISICAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("INFISICAL_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")

    resolver = InfisicalResolver()
    with pytest.raises(ValueResolutionError, match="not authenticated"):
        resolver.resolve("ANY")


def test_universal_auth_login_exchanges_for_a_token(monkeypatch):
    monkeypatch.delenv("INFISICAL_TOKEN", raising=False)
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "cid")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")

    calls: list[str] = []

    def _fake_http_request(method, url, *, headers=None, body=None, timeout=30):
        calls.append(url)
        if "auth/universal-auth/login" in url:
            return HttpResult(status=200, body=json.dumps({"accessToken": "exchanged-token"}))
        return HttpResult(status=200, body=json.dumps({"secrets": [{"secretKey": "K", "secretValue": "V"}]}))

    monkeypatch.setattr(module, "http_request", _fake_http_request)

    resolver = InfisicalResolver()
    assert resolver.resolve("K") == "V"
    assert len(calls) == 2


def test_unreachable_backend_raises(monkeypatch):
    monkeypatch.setenv("INFISICAL_TOKEN", "svc-token")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-1")

    from strata.utils.transport import NO_RESPONSE

    monkeypatch.setattr(module, "http_request", lambda *a, **k: HttpResult(status=NO_RESPONSE, body="connection refused"))

    resolver = InfisicalResolver()
    with pytest.raises(ValueResolutionError, match="could not reach"):
        resolver.resolve("ANY")
