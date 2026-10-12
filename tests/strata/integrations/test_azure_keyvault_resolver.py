#!/usr/bin/env python3
"""Tests for `AzureKeyVaultResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError

from strata.integrations.azure_keyvault_resolver import AzureKeyVaultResolver
from strata.integrations.errors import IntegrationError, ValueResolutionError
from strata.integrations.resolved_context import KeyValueEntry, SecretMetadata
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)


class _FakeSecretProperties:
    def __init__(self, created_on=None, updated_on=None, version=None) -> None:
        self.created_on = created_on
        self.updated_on = updated_on
        self.version = version


class _FakeSecret:
    def __init__(self, value: str | None, properties: _FakeSecretProperties | None = None) -> None:
        self.value = value
        self.properties = properties or _FakeSecretProperties()


class _FakeSecretClient:
    def __init__(
        self, secrets: dict[str, str], *, properties_by_key: dict[str, _FakeSecretProperties] | None = None
    ) -> None:
        self._secrets = secrets
        self._properties_by_key = properties_by_key or {}
        self._version_counter = 0

    def get_secret(self, key: str) -> _FakeSecret:
        if key not in self._secrets:
            raise ResourceNotFoundError("not found")
        return _FakeSecret(self._secrets[key], self._properties_by_key.get(key))

    def set_secret(self, key: str, value: str) -> _FakeSecret:
        self._secrets[key] = value
        self._version_counter += 1
        properties = _FakeSecretProperties(version=str(self._version_counter))
        self._properties_by_key[key] = properties
        return _FakeSecret(value, properties)


class _FailingSecretClient:
    """A client whose every call raises a generic `AzureError` — for
    write/metadata failure-path tests."""

    def get_secret(self, key: str) -> _FakeSecret:
        raise AzureError("boom")

    def set_secret(self, key: str, value: str) -> _FakeSecret:
        raise AzureError("boom")


def test_class_declares_its_contract():
    assert AzureKeyVaultResolver.TYPE == "azure-keyvault"
    assert AzureKeyVaultResolver.CAPABILITIES == {"secrets", "key_value"}
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


# ---------------------------------------------------------------------------
# set() / metadata() — docs/design/values-secrets-command.md Phase 5 (D3)
# ---------------------------------------------------------------------------


def test_set_writes_via_set_secret(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    client = _FakeSecretClient({})
    resolver._client = client

    resolver.set("DB_PASSWORD", "new-value")

    assert client._secrets == {"DB_PASSWORD": "new-value"}


def test_set_without_vault_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    resolver = AzureKeyVaultResolver()

    with pytest.raises(IntegrationError, match="AZURE_KEYVAULT_URL"):
        resolver.set("K", "v")


def test_set_raises_integration_error_when_the_write_itself_fails(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FailingSecretClient()

    with pytest.raises(IntegrationError, match="could not write"):
        resolver.set("K", "v")


def test_metadata_returns_created_and_updated_timestamps(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updated = datetime(2026, 6, 1, tzinfo=timezone.utc)
    resolver._client = _FakeSecretClient(
        {"DB_PASSWORD": "hunter2"},
        properties_by_key={"DB_PASSWORD": _FakeSecretProperties(created_on=created, updated_on=updated)},
    )

    meta = resolver.metadata("DB_PASSWORD")

    assert meta == SecretMetadata(created_at=created, updated_at=updated)


def test_metadata_returns_none_when_secret_not_found(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({})

    assert resolver.metadata("GHOST") is None


def test_metadata_without_vault_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    resolver = AzureKeyVaultResolver()

    with pytest.raises(IntegrationError, match="AZURE_KEYVAULT_URL"):
        resolver.metadata("K")


def test_metadata_raises_integration_error_on_a_real_azure_failure(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FailingSecretClient()

    with pytest.raises(IntegrationError, match="could not read metadata"):
        resolver.metadata("K")


# ---------------------------------------------------------------------------
# get() / put() — KeyValueStoreIntegration (docs/work/key-value-store-
# integration.md's Backend 7)
# ---------------------------------------------------------------------------


def test_get_returns_entry_with_value_and_version(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient(
        {"DB_PASSWORD": "hunter2"},
        properties_by_key={"DB_PASSWORD": _FakeSecretProperties(version="abc123")},
    )

    assert resolver.get("DB_PASSWORD") == KeyValueEntry(value=b"hunter2", version="abc123")


def test_get_returns_none_when_not_found(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({})

    assert resolver.get("GHOST") is None


def test_get_treats_an_empty_string_value_as_a_real_entry_not_an_error(monkeypatch):
    """ "" is not None — distinct from the 'has no value' (None) case below,
    and from NOT_FOUND above: an existing secret with an empty value is
    still a found, valid entry."""
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient(
        {"EMPTY": ""}, properties_by_key={"EMPTY": _FakeSecretProperties(version="abc123")}
    )

    assert resolver.get("EMPTY") == KeyValueEntry(value=b"", version="abc123")


def test_get_without_vault_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    resolver = AzureKeyVaultResolver()

    with pytest.raises(IntegrationError, match="AZURE_KEYVAULT_URL"):
        resolver.get("ANY")


def test_get_none_value_raises_integration_error(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({"EMPTY": None})  # type: ignore[dict-item]

    with pytest.raises(IntegrationError, match="has no value"):
        resolver.get("EMPTY")


def test_get_raises_integration_error_on_a_real_azure_failure(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FailingSecretClient()

    with pytest.raises(IntegrationError, match="could not resolve"):
        resolver.get("K")


def test_put_writes_unconditionally_and_returns_new_version(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    client = _FakeSecretClient({})
    resolver._client = client

    version = resolver.put("DB_PASSWORD", b"new-value")

    assert client._secrets == {"DB_PASSWORD": "new-value"}
    assert version == "1"


def test_put_rejects_if_version_for_create_only():
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({})

    from strata.integrations.capabilities import NEW_KEY_VERSION

    with pytest.raises(IntegrationError, match="does not support conditional writes"):
        resolver.put("K", b"v", if_version=NEW_KEY_VERSION)


def test_put_rejects_if_version_for_an_update():
    resolver = AzureKeyVaultResolver()
    resolver._client = _FakeSecretClient({})

    with pytest.raises(IntegrationError, match="does not support conditional writes"):
        resolver.put("K", b"v", if_version="some-prior-version")


def test_supports_cas_is_false():
    """Advertised so a CAS-dependent caller (resolve_output_store_integration(),
    semantic_checks.py's validate-time check) can reject this backend up
    front instead of discovering it only once put(if_version=...) raises."""
    assert AzureKeyVaultResolver.SUPPORTS_CAS is False


def test_put_without_vault_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_KEYVAULT_URL", raising=False)
    resolver = AzureKeyVaultResolver()

    with pytest.raises(IntegrationError, match="AZURE_KEYVAULT_URL"):
        resolver.put("K", b"v")


def test_put_raises_integration_error_when_the_write_itself_fails(monkeypatch):
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "https://vault.example/")
    resolver = AzureKeyVaultResolver()
    resolver._client = _FailingSecretClient()

    with pytest.raises(IntegrationError, match="could not write"):
        resolver.put("K", b"v")
