#!/usr/bin/env python3
"""Tests for `AzureKeyVaultResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core.exceptions import ResourceNotFoundError

from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.errors import ValueResolutionError


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
