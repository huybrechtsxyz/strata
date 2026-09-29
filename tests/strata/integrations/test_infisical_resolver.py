#!/usr/bin/env python3
"""Tests for `InfisicalResolver` (ADR-0021 D7 retrofit — urllib -> http_request)."""

import json

import pytest

from strata.integrations import infisical_resolver as module
from strata.integrations.errors import ValueResolutionError
from strata.integrations.infisical_resolver import InfisicalResolver
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)
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
    monkeypatch.setattr(
        module, "http_request", lambda *a, **k: HttpResult(status=200, body=json.dumps({"secrets": []}))
    )

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

    monkeypatch.setattr(
        module, "http_request", lambda *a, **k: HttpResult(status=NO_RESPONSE, body="connection refused")
    )

    resolver = InfisicalResolver()
    with pytest.raises(ValueResolutionError, match="could not reach"):
        resolver.resolve("ANY")


# ---------------------------------------------------------------------------
# Config-driven addr/project_id/environment — docs/design/
# store-integration-configuration.md's Phase 2. The matching env var stays
# the fallback, unchanged, in every case.
# ---------------------------------------------------------------------------


def _config(
    *, address: str | None = None, project_id: str | None = None, environment: str | None = None
) -> IntegrationModel:
    endpoints = IntegrationEndpointsModel(address=address) if address is not None else None
    configuration: dict[str, str] = {}
    if project_id is not None:
        configuration["project_id"] = project_id
    if environment is not None:
        configuration["environment"] = environment
    return IntegrationModel(
        meta=IntegrationMetaModel(name="infisical-prod"),
        spec=IntegrationSpecModel(type="infisical", endpoints=endpoints, configuration=configuration or None),
    )


def test_bound_config_address_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_ADDR", "https://env.infisical.example")
    resolver = InfisicalResolver(_config(address="https://config.infisical.example"))

    assert resolver._addr == "https://config.infisical.example"


def test_no_config_address_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_ADDR", "https://env.infisical.example")
    resolver = InfisicalResolver(None)

    assert resolver._addr == "https://env.infisical.example"


def test_config_with_no_endpoints_falls_back_to_env_var_address(monkeypatch):
    monkeypatch.setenv("INFISICAL_ADDR", "https://env.infisical.example")
    resolver = InfisicalResolver(_config())

    assert resolver._addr == "https://env.infisical.example"


def test_no_config_address_falls_back_to_default_when_env_var_also_unset(monkeypatch):
    monkeypatch.delenv("INFISICAL_ADDR", raising=False)
    resolver = InfisicalResolver(None)

    assert resolver._addr == "https://app.infisical.com"


def test_bound_config_project_id_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "env-project")
    resolver = InfisicalResolver(_config(project_id="config-project"))

    assert resolver._project_id == "config-project"


def test_no_config_project_id_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "env-project")
    resolver = InfisicalResolver(None)

    assert resolver._project_id == "env-project"


def test_config_with_no_project_id_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "env-project")
    resolver = InfisicalResolver(_config(address="https://config.infisical.example"))

    assert resolver._project_id == "env-project"


def test_bound_config_environment_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "env-environment")
    resolver = InfisicalResolver(_config(environment="config-environment"))

    assert resolver._environment == "config-environment"


def test_no_config_environment_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "env-environment")
    resolver = InfisicalResolver(None)

    assert resolver._environment == "env-environment"


def test_no_config_environment_falls_back_to_default_when_env_var_also_unset(monkeypatch):
    monkeypatch.delenv("INFISICAL_ENVIRONMENT", raising=False)
    resolver = InfisicalResolver(None)

    assert resolver._environment == "prod"
