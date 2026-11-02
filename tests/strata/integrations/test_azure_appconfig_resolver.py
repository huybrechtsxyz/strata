#!/usr/bin/env python3
"""Tests for `AzureAppConfigResolver` (ADR-0021 D7 retrofit — StoreIntegration, SDK client unchanged)."""

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceModifiedError, ResourceNotFoundError

from strata.integrations.azure_appconfig_resolver import AzureAppConfigResolver
from strata.integrations.capabilities import NEW_KEY_VERSION
from strata.integrations.errors import IntegrationError, KeyValueConflictError, ValueResolutionError
from strata.integrations.resolved_context import KeyValueEntry
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)


class _FakeSetting:
    def __init__(self, value: str | None, etag: str | None = None) -> None:
        self.value = value
        self.etag = etag


class _FakeAppConfigClient:
    def __init__(self, settings: dict[str, str], *, etags: dict[str, str] | None = None) -> None:
        self._settings = settings
        self._etags = dict(etags or {})
        self._version_counter = 0

    def get_configuration_setting(self, key: str) -> _FakeSetting:
        if key not in self._settings:
            raise ResourceNotFoundError("not found")
        return _FakeSetting(self._settings[key], self._etags.get(key))

    def set_configuration_setting(
        self, setting, match_condition: MatchConditions | None = None, *, etag: str | None = None
    ) -> _FakeSetting:
        if match_condition == MatchConditions.IfMissing and setting.key in self._settings:
            raise ResourceModifiedError("already exists")
        if match_condition == MatchConditions.IfNotModified:
            required_etag = etag if etag is not None else setting.etag
            if self._etags.get(setting.key) != required_etag:
                raise ResourceModifiedError("etag mismatch")
        self._settings[setting.key] = setting.value
        self._version_counter += 1
        new_etag = str(self._version_counter)
        self._etags[setting.key] = new_etag
        return _FakeSetting(setting.value, new_etag)


class _FailingAppConfigClient:
    """A client whose every call raises a generic `AzureError` — for the
    write/read failure-path tests."""

    def get_configuration_setting(self, key: str) -> _FakeSetting:
        raise AzureError("boom")

    def set_configuration_setting(self, setting, match_condition=None, *, etag: str | None = None) -> _FakeSetting:
        raise AzureError("boom")


def test_class_declares_its_contract():
    assert AzureAppConfigResolver.TYPE == "azure-appconfig"
    assert AzureAppConfigResolver.CAPABILITIES == {"variables", "features", "key_value"}
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
# get() / put() — KeyValueStoreIntegration (docs/work/key-value-store-
# integration.md's Backend 2) — real ETag-based CAS, unlike Key Vault.
# ---------------------------------------------------------------------------


def test_get_returns_entry_with_value_and_version(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"REGION": "westeurope"}, etags={"REGION": "abc123"})

    assert resolver.get("REGION") == KeyValueEntry(value=b"westeurope", version="abc123")


def test_get_returns_none_when_not_found(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({})

    assert resolver.get("GHOST") is None


def test_get_treats_an_empty_string_value_as_a_real_entry_not_an_error(monkeypatch):
    """ "" is not None — distinct from the 'has no value' (None) case below,
    and from NOT_FOUND above: an existing key with an empty value is still
    a found, valid entry."""
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"EMPTY": ""}, etags={"EMPTY": "abc123"})

    assert resolver.get("EMPTY") == KeyValueEntry(value=b"", version="abc123")


def test_get_without_endpoint_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    resolver = AzureAppConfigResolver()

    with pytest.raises(IntegrationError, match="AZURE_APPCONFIG_ENDPOINT"):
        resolver.get("ANY")


def test_get_none_value_raises_integration_error(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"EMPTY": None})  # type: ignore[dict-item]

    with pytest.raises(IntegrationError, match="has no value"):
        resolver.get("EMPTY")


def test_get_raises_integration_error_on_a_real_azure_failure(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FailingAppConfigClient()

    with pytest.raises(IntegrationError, match="could not resolve"):
        resolver.get("K")


def test_put_writes_unconditionally_and_returns_new_version(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    client = _FakeAppConfigClient({})
    resolver._client = client

    version = resolver.put("REGION", b"northeurope")

    assert client._settings == {"REGION": "northeurope"}
    assert version == "1"


def test_put_with_new_key_version_succeeds_when_key_does_not_exist(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    client = _FakeAppConfigClient({})
    resolver._client = client

    version = resolver.put("REGION", b"northeurope", if_version=NEW_KEY_VERSION)

    assert client._settings == {"REGION": "northeurope"}
    assert version == "1"


def test_put_with_new_key_version_raises_conflict_when_key_already_exists(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"REGION": "westeurope"}, etags={"REGION": "abc123"})

    with pytest.raises(KeyValueConflictError, match="modified concurrently"):
        resolver.put("REGION", b"northeurope", if_version=NEW_KEY_VERSION)


def test_put_with_real_prior_version_succeeds_when_etag_matches(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    client = _FakeAppConfigClient({"REGION": "westeurope"}, etags={"REGION": "abc123"})
    resolver._client = client

    version = resolver.put("REGION", b"northeurope", if_version="abc123")

    assert client._settings == {"REGION": "northeurope"}
    assert version == "1"


def test_put_with_real_prior_version_raises_conflict_when_etag_mismatches(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FakeAppConfigClient({"REGION": "westeurope"}, etags={"REGION": "abc123"})

    with pytest.raises(KeyValueConflictError, match="modified concurrently"):
        resolver.put("REGION", b"northeurope", if_version="stale-etag")


def test_put_without_endpoint_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_APPCONFIG_ENDPOINT", raising=False)
    resolver = AzureAppConfigResolver()

    with pytest.raises(IntegrationError, match="AZURE_APPCONFIG_ENDPOINT"):
        resolver.put("K", b"v")


def test_put_raises_integration_error_when_the_write_itself_fails(monkeypatch):
    monkeypatch.setenv("AZURE_APPCONFIG_ENDPOINT", "https://config.example/")
    resolver = AzureAppConfigResolver()
    resolver._client = _FailingAppConfigClient()

    with pytest.raises(IntegrationError, match="could not write"):
        resolver.put("K", b"v")


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
