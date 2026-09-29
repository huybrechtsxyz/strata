#!/usr/bin/env python3
"""Tests for `AzureKeyVaultResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core.exceptions import ResourceNotFoundError

from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.errors import ValueResolutionError
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)


class _FakeSecret:
    def __init__(self, value: str | None) -> None:
        self.value = value


class _FakeSecretClient:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, key: str) -> _FakeSecret:
        if key not in self._secrets:
            raise ResourceNotFoundError("not found")
        return _FakeSecret(self._secrets[key])


def test_class_declares_its_contract():
    assert AzureKeyVaultResolver.TYPE == "azure-keyvault"
    assert AzureKeyVaultResolver.CAPABILITIES == {"secrets"}
    assert AzureKeyVaultResolver.TRANSPORTS == {"sdk"}


def test_resolve_returns_the_secret_value(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({"DB_PASSWORD": "hunter2"})

    assert resolver.resolve("DB_PASSWORD") == "hunter2"


def test_resolve_missing_secret_raises(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({})

    with pytest.raises(ValueResolutionError, match="no secret named"):
        resolver.resolve("GHOST")


def test_resolve_without_vault_url_raises(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    resolver = AzureKeyVaultResolver()

    with pytest.raises(ValueResolutionError, match="AZURE_KEYVAULT_URL"):
        resolver.resolve("ANY")


def test_resolve_none_value_raises(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({"EMPTY": None})  # type: ignore[dict-item]

    with pytest.raises(ValueResolutionError, match="has no value"):
        resolver.resolve("EMPTY")


# ---------------------------------------------------------------------------
# Config-driven vault URL — docs/design/store-integration-configuration.md's
# Phase 2. `AZURE_KEYVAULT_URL` stays the fallback, unchanged, in every case.
# ---------------------------------------------------------------------------


def _config(*, address: str | None) -> IntegrationModel:
    endpoints = IntegrationEndpointsModel(address=address) if address is not None else None
    return IntegrationModel(
        meta=IntegrationMetaModel(name="azure-keyvault-prod"),
        spec=IntegrationSpecModel(type="azure-keyvault", endpoints=endpoints),
    )


def test_bound_config_endpoint_address_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://env-vault.example/")
    resolver = AzureKeyVaultResolver(_config(address="https://config-vault.example"))

    assert resolver._vault_url == "https://config-vault.example"


def test_no_config_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://env-vault.example/")
    resolver = AzureKeyVaultResolver(None)

    assert resolver._vault_url == "https://env-vault.example/"


def test_config_with_no_endpoints_falls_back_to_env_var(monkeypatch):
    """Per-field fallback, not per-resolver all-or-nothing: a bound document
    with `endpoints` unset still falls back to the env var, exactly like no
    document at all."""
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://env-vault.example/")
    resolver = AzureKeyVaultResolver(_config(address=None))

    assert resolver._vault_url == "https://env-vault.example/"
