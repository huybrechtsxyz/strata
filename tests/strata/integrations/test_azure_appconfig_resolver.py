#!/usr/bin/env python3
"""Tests for `AzureAppConfigResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError

from strata.integrations.azure_appconfig_resolver import AzureAppConfigResolver
from strata.integrations.errors import IntegrationError, ValueResolutionError
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)


class _FakeSetting:
    def __init__(self, value: str | None) -> None:
        self.value = value


class _FakeAppConfigClient:
    def __init__(self, settings: dict[str, str]) -> None:
        self._settings = settings

    def get_configuration_setting(self, key: str) -> _FakeSetting:
        if key not in self._settings:
            raise ResourceNotFoundError("not found")
        return _FakeSetting(self._settings[key])

    def set_configuration_setting(self, setting) -> _FakeSetting:
        self._settings[setting.key] = setting.value
        return _FakeSetting(setting.value)


class _FailingAppConfigClient:
    """A client whose every call raises a generic `AzureError` — for the
    write failure-path test."""

    def set_configuration_setting(self, setting) -> _FakeSetting:
        raise AzureError("boom")


def test_class_declares_its_contract():
    assert AzureAppConfigResolver.TYPE == "azure-appconfig"
    assert AzureAppConfigResolver.CAPABILITIES == {"variables", "features"}
    assert AzureAppConfigResolver.TRANSPORTS == {"sdk"}


def test_resolve_returns_the_configuration_value(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"REGION": "westeurope"})

    assert resolver.resolve("REGION") == "westeurope"


def test_resolve_missing_key_raises(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({})

    with pytest.raises(ValueResolutionError, match="no key named"):
        resolver.resolve("GHOST")


def test_resolve_without_endpoint_raises(monkeypatch):
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    resolver = AzureAppConfigResolver()

    with pytest.raises(ValueResolutionError, match="AZURE_APPCONFIG_ENDPOINT"):
        resolver.resolve("ANY")


# ---------------------------------------------------------------------------
# set() — docs/design/values-secrets-command.md Phase 5 (D3). No metadata()
# override for this resolver — only Infisical/Azure Key Vault are secret-
# capable (see the module docstring).
# ---------------------------------------------------------------------------


def test_set_writes_via_set_configuration_setting(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    client = _FakeAppConfigClient({})
    resolver._client = client

    resolver.set("REGION", "northeurope")

    assert client._settings == {"REGION": "northeurope"}


def test_set_without_endpoint_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    resolver = AzureAppConfigResolver()

    with pytest.raises(IntegrationError, match="AZURE_APPCONFIG_ENDPOINT"):
        resolver.set("K", "v")


def test_set_raises_integration_error_when_the_write_itself_fails(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FailingAppConfigClient()

    with pytest.raises(IntegrationError, match="could not write"):
        resolver.set("K", "v")


# ---------------------------------------------------------------------------
# Config-driven endpoint — docs/design/store-integration-configuration.md's
# Phase 2. `AZURE_APPCONFIG_ENDPOINT` stays the fallback, unchanged, in every
# case.
# ---------------------------------------------------------------------------


def _config(*, address: str | None) -> IntegrationModel:
    endpoints = IntegrationEndpointsModel(address=address) if address is not None else None
    return IntegrationModel(
        meta=IntegrationMetaModel(name="azure-appconfig-prod"),
        spec=IntegrationSpecModel(type="azure-appconfig", endpoints=endpoints),
    )


def test_bound_config_endpoint_address_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://env-config.example/")
    resolver = AzureAppConfigResolver(_config(address="https://config-config.example"))

    assert resolver._endpoint == "https://config-config.example"


def test_no_config_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://env-config.example/")
    resolver = AzureAppConfigResolver(None)

    assert resolver._endpoint == "https://env-config.example/"


def test_config_with_no_endpoints_falls_back_to_env_var(monkeypatch):
    """Per-field fallback, not per-resolver all-or-nothing: a bound document
    with `endpoints` unset still falls back to the env var, exactly like no
    document at all."""
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://env-config.example/")
    resolver = AzureAppConfigResolver(_config(address=None))

    assert resolver._endpoint == "https://env-config.example/"
