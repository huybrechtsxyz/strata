#!/usr/bin/env python3
"""Azure Blob Storage key/value backend — Azure SDK, not the `az` CLI
(docs/work/key-value-store-integration.md's Backend 3).

The container URL may come from a bound `kind: integration` document's
`spec.endpoints.address` (same convention as the Key Vault/App Configuration
resolvers) — falls back to the environment variable below, unchanged, when
no document is bound or its `endpoints` is unset:

- ``AZURE_BLOB_CONTAINER_URL`` — the full container URL (e.g.
  ``https://<account>.blob.core.windows.net/<container>``).

Authentication uses `azure.identity.DefaultAzureCredential` — see
`azure_keyvault_resolver.py`'s docstring for why (same reasoning, same
managed-identity production usage, never read from `spec.authentication`).

A `KeyValueStoreIntegration` only — no `StoreIntegration` retrofit, unlike
Key Vault/App Configuration, since there is no existing v1-derived
variables/secrets/features resolver for Blob Storage to extend. Real
compare-and-swap via the Blob SDK's own ETag `match_condition` support,
confirmed empirically against the installed SDK before implementing (not
assumed): `upload_blob()`'s `overwrite=True` only skips the SDK's own
*automatic* `If-None-Match: *` guard (added only when `overwrite=False`
and no caller-supplied condition exists) — it does not strip out an
explicitly passed `etag`/`match_condition`, so passing both together
composes correctly for every `if_version` case below.
"""

from os import environ

from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceModifiedError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration
from strata.integrations.errors import IntegrationError, KeyValueConflictError
from strata.integrations.resolved_context import KeyValueEntry
from strata.models.integration_model import Capability, IntegrationModel


class AzureBlobKeyValueStore(KeyValueStoreIntegration):
    """Stores small records as blobs, by name, in one container."""

    TYPE = "azure-blob"
    CAPABILITIES = frozenset({Capability.KEY_VALUE})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        endpoint_address = config.spec.endpoints.address if config is not None and config.spec.endpoints else None
        self._container_url = endpoint_address or environ.get("AZURE_BLOB_CONTAINER_URL")
        self._client: ContainerClient | None = None

    def _get_client(self) -> ContainerClient:
        if not self._container_url:
            raise IntegrationError(
                "Azure Blob Storage: no container URL configured — set AZURE_BLOB_CONTAINER_URL, or bind an "
                "Integration document with spec.endpoints.address set."
            )
        if self._client is None:
            self._client = ContainerClient.from_container_url(self._container_url, credential=DefaultAzureCredential())
        return self._client

    def get(self, key: str) -> KeyValueEntry | None:
        """Return the current value and version of blob `key`, or `None`
        if it does not exist in the container.

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or unreachable.
        """
        client = self._get_client()
        try:
            downloader = client.get_blob_client(key).download_blob()
        except ResourceNotFoundError:
            return None
        except AzureError as exc:
            raise IntegrationError(f"Azure Blob Storage: could not resolve '{key}': {exc}") from exc
        data = downloader.readall()
        return KeyValueEntry(value=data, version=downloader.properties.etag or "")

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        """Write `value` to blob `key`, returning the new ETag.

        Args:
            if_version: `NEW_KEY_VERSION` maps to
                `MatchConditions.IfMissing` (succeeds only if `key` doesn't
                exist yet); any other value maps to
                `MatchConditions.IfNotModified` with that value as the
                required ETag (succeeds only if `key`'s current ETag still
                matches); omitted is an unconditional overwrite.

        Raises:
            strata.integrations.errors.KeyValueConflictError: the
                condition `if_version` specified didn't hold.
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or the write itself failed.
        """
        client = self._get_client()
        blob_client = client.get_blob_client(key)
        try:
            if if_version == NEW_KEY_VERSION:
                result = blob_client.upload_blob(value, overwrite=True, match_condition=MatchConditions.IfMissing)
            elif if_version is not None:
                result = blob_client.upload_blob(
                    value, overwrite=True, etag=if_version, match_condition=MatchConditions.IfNotModified
                )
            else:
                result = blob_client.upload_blob(value, overwrite=True)
        except ResourceModifiedError as exc:
            raise KeyValueConflictError(f"Azure Blob Storage: '{key}' was modified concurrently.") from exc
        except AzureError as exc:
            raise IntegrationError(f"Azure Blob Storage: could not write '{key}': {exc}") from exc
        return result["etag"] or ""
