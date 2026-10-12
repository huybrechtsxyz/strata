#!/usr/bin/env python3
"""Tests for `AzureBlobKeyValueStore` (docs/work/key-value-store-integration.md's Backend 3)."""

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceModifiedError, ResourceNotFoundError

from strata.integrations.azure_blob_keyvalue_store import AzureBlobKeyValueStore
from strata.integrations.capabilities import NEW_KEY_VERSION
from strata.integrations.errors import IntegrationError, KeyValueConflictError
from strata.integrations.resolved_context import KeyValueEntry
from strata.models.integration_model import (
    IntegrationEndpointsModel,
    IntegrationMetaModel,
    IntegrationModel,
    IntegrationSpecModel,
)


class _FakeProperties:
    def __init__(self, etag: str | None) -> None:
        self.etag = etag


class _FakeDownloader:
    def __init__(self, data: bytes, etag: str | None) -> None:
        self._data = data
        self.properties = _FakeProperties(etag)

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, container: "_FakeContainerClient", key: str) -> None:
        self._container = container
        self._key = key

    def download_blob(self) -> _FakeDownloader:
        if self._key not in self._container.blobs:
            raise ResourceNotFoundError("not found")
        data, etag = self._container.blobs[self._key]
        return _FakeDownloader(data, etag)

    def upload_blob(
        self,
        data: bytes,
        *,
        overwrite: bool = False,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
    ) -> dict[str, str]:
        existing = self._container.blobs.get(self._key)
        if match_condition == MatchConditions.IfMissing and existing is not None:
            raise ResourceModifiedError("already exists")
        if match_condition == MatchConditions.IfNotModified:
            current_etag = existing[1] if existing is not None else None
            if current_etag != etag:
                raise ResourceModifiedError("etag mismatch")
        self._container.version_counter += 1
        new_etag = str(self._container.version_counter)
        self._container.blobs[self._key] = (data, new_etag)
        return {"etag": new_etag}


class _FakeContainerClient:
    def __init__(self, blobs: dict[str, tuple[bytes, str | None]] | None = None) -> None:
        self.blobs = dict(blobs or {})
        self.version_counter = 0

    def get_blob_client(self, key: str) -> _FakeBlobClient:
        return _FakeBlobClient(self, key)


class _FailingBlobClient:
    """A blob client whose every call raises a generic `AzureError` — for
    the read/write failure-path tests."""

    def download_blob(self) -> _FakeDownloader:
        raise AzureError("boom")

    def upload_blob(self, data: bytes, **kwargs: object) -> dict[str, str]:
        raise AzureError("boom")


class _FailingContainerClient:
    def get_blob_client(self, key: str) -> _FailingBlobClient:
        return _FailingBlobClient()


def test_class_declares_its_contract():
    assert AzureBlobKeyValueStore.TYPE == "azure-blob"
    assert AzureBlobKeyValueStore.CAPABILITIES == {"key_value"}
    assert AzureBlobKeyValueStore.TRANSPORTS == {"sdk"}


def test_get_returns_entry_with_value_and_version(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FakeContainerClient({"K": (b"hello", "abc123")})

    assert store.get("K") == KeyValueEntry(value=b"hello", version="abc123")


def test_get_returns_none_when_not_found(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FakeContainerClient()

    assert store.get("GHOST") is None


def test_get_treats_an_empty_blob_as_a_real_entry_not_an_error(monkeypatch):
    """Unlike Key Vault/App Config, Blob Storage has no 'exists but has no
    value' state at all — an existing (even empty) blob always downloads
    real bytes (b"" for an empty one), never None. Distinct from NOT_FOUND
    above: an empty blob is still a found, valid entry."""
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FakeContainerClient({"EMPTY": (b"", "abc123")})

    assert store.get("EMPTY") == KeyValueEntry(value=b"", version="abc123")


def test_get_without_container_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_BLOB_CONTAINER_URL", raising=False)
    store = AzureBlobKeyValueStore()

    with pytest.raises(IntegrationError, match="AZURE_BLOB_CONTAINER_URL"):
        store.get("ANY")


def test_get_raises_integration_error_on_a_real_azure_failure(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FailingContainerClient()

    with pytest.raises(IntegrationError, match="could not resolve"):
        store.get("K")


def test_put_writes_unconditionally_and_returns_new_version(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    client = _FakeContainerClient()
    store._client = client

    version = store.put("K", b"hello")

    assert client.blobs["K"] == (b"hello", "1")
    assert version == "1"


def test_put_with_new_key_version_succeeds_when_key_does_not_exist(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    client = _FakeContainerClient()
    store._client = client

    version = store.put("K", b"hello", if_version=NEW_KEY_VERSION)

    assert client.blobs["K"] == (b"hello", "1")
    assert version == "1"


def test_put_with_new_key_version_raises_conflict_when_key_already_exists(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FakeContainerClient({"K": (b"existing", "abc123")})

    with pytest.raises(KeyValueConflictError, match="modified concurrently"):
        store.put("K", b"hello", if_version=NEW_KEY_VERSION)


def test_put_with_real_prior_version_succeeds_when_etag_matches(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    client = _FakeContainerClient({"K": (b"existing", "abc123")})
    store._client = client

    version = store.put("K", b"hello", if_version="abc123")

    assert client.blobs["K"] == (b"hello", "1")
    assert version == "1"


def test_put_with_real_prior_version_raises_conflict_when_etag_mismatches(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FakeContainerClient({"K": (b"existing", "abc123")})

    with pytest.raises(KeyValueConflictError, match="modified concurrently"):
        store.put("K", b"hello", if_version="stale-etag")


def test_put_without_container_url_raises_integration_error(monkeypatch):
    monkeypatch.delenv("AZURE_BLOB_CONTAINER_URL", raising=False)
    store = AzureBlobKeyValueStore()

    with pytest.raises(IntegrationError, match="AZURE_BLOB_CONTAINER_URL"):
        store.put("K", b"v")


def test_put_raises_integration_error_when_the_write_itself_fails(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://account.blob.core.windows.net/container")
    store = AzureBlobKeyValueStore()
    store._client = _FailingContainerClient()

    with pytest.raises(IntegrationError, match="could not write"):
        store.put("K", b"v")


# ---------------------------------------------------------------------------
# Config-driven container URL — same convention as the Key Vault/App
# Configuration resolvers. `AZURE_BLOB_CONTAINER_URL` stays the fallback,
# unchanged, in every case.
# ---------------------------------------------------------------------------


def _config(*, address: str | None) -> IntegrationModel:
    endpoints = IntegrationEndpointsModel(address=address) if address is not None else None
    return IntegrationModel(
        meta=IntegrationMetaModel(name="azure-blob-prod"),
        spec=IntegrationSpecModel(type="azure-blob", endpoints=endpoints),
    )


def test_bound_config_endpoint_address_is_preferred_over_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://env.blob.core.windows.net/env-container")
    store = AzureBlobKeyValueStore(_config(address="https://config.blob.core.windows.net/config-container"))

    assert store._container_url == "https://config.blob.core.windows.net/config-container"


def test_no_config_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://env.blob.core.windows.net/env-container")
    store = AzureBlobKeyValueStore(None)

    assert store._container_url == "https://env.blob.core.windows.net/env-container"


def test_config_with_no_endpoints_falls_back_to_env_var(monkeypatch):
    """Per-field fallback, not per-resolver all-or-nothing: a bound document
    with `endpoints` unset still falls back to the env var, exactly like no
    document at all."""
    monkeypatch.setenv("AZURE_BLOB_CONTAINER_URL", "https://env.blob.core.windows.net/env-container")
    store = AzureBlobKeyValueStore(_config(address=None))

    assert store._container_url == "https://env.blob.core.windows.net/env-container"
