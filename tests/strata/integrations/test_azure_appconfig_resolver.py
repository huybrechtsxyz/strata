#!/usr/bin/env python3
"""Tests for `AzureAppConfigResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core.exceptions import ResourceNotFoundError

from strata.integrations.azure_appconfig_resolver import AzureAppConfigResolver
from strata.integrations.errors import ValueResolutionError
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
