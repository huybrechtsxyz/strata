#!/usr/bin/env python3
"""Azure App Configuration resolver — variables and feature flags.

The store endpoint may come from a bound `kind: integration` document's
`spec.endpoints.address` (docs/design/store-integration-configuration.md's
Phase 2) — falls back to the environment variable below, unchanged, when
no document is bound or its `endpoints` is unset:

- ``AZURE_APPCONFIG_ENDPOINT`` — the store endpoint
  (e.g. ``https://my-config.azconfig.io``).

Authentication uses `azure.identity.DefaultAzureCredential` — see
`azure_keyvault_resolver.py`'s docstring for why (same reasoning, same
managed-identity production usage, never read from `spec.authentication`).

Resolves a plain configuration key/value. **Not yet special-cased for a
`.appconfig.featureflag/*`-style feature flag key** — Azure App Config
stores those as a JSON blob (`{"id": ..., "enabled": ..., ...}`), and this
returns that blob verbatim rather than parsing out `enabled`. No production
evidence yet of a feature (as opposed to a variable) actually resolving
through this store — parse it properly once one does.

ADR-0021 D7 retrofit: a `StoreIntegration`, `TRANSPORTS={"sdk"}` — keeps
`AzureAppConfigurationClient` unchanged, same reasoning as the Key Vault
resolver.

Also a `KeyValueStoreIntegration` (docs/work/key-value-store-integration.md's
Backend 2) — real compare-and-swap, unlike Key Vault's own retrofit:
App Configuration's ETag + `match_condition` natively supports both the
create-only (`NEW_KEY_VERSION`) and update-only (a real prior ETag)
cases, confirmed empirically against the installed SDK (`put()`'s own
docstring).
"""

from os import environ

from azure.appconfiguration import AzureAppConfigurationClient, ConfigurationSetting
from azure.core import MatchConditions
from azure.core.exceptions import AzureError, ResourceModifiedError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from strata.integrations.capabilities import NEW_KEY_VERSION, KeyValueStoreIntegration, StoreIntegration
from strata.integrations.errors import IntegrationError, KeyValueConflictError, ValueResolutionError
from strata.integrations.resolved_context import KeyValueEntry
from strata.models.integration_model import Capability, IntegrationModel


class AzureAppConfigResolver(StoreIntegration, KeyValueStoreIntegration):
    """Resolves Azure App Configuration variables/feature flags, by key."""

    TYPE = "azure-appconfig"
    CAPABILITIES = frozenset({Capability.VARIABLES, Capability.FEATURES, Capability.KEY_VALUE})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        endpoint_address = config.spec.endpoints.address if config is not None and config.spec.endpoints else None
        self._endpoint = endpoint_address or environ.get("AZURE_APPCONFIG_ENDPOINT")
        self._client: AzureAppConfigurationClient | None = None

    def _get_client(self) -> AzureAppConfigurationClient:
        if not self._endpoint:
            raise ValueResolutionError(
                "Azure App Configuration: no endpoint configured — set AZURE_APPCONFIG_ENDPOINT, or bind an "
                "Integration document with spec.endpoints.address set."
            )
        if self._client is None:
            self._client = AzureAppConfigurationClient(base_url=self._endpoint, credential=DefaultAzureCredential())
        return self._client

    def resolve(self, key: str) -> str:
        """Return the value of configuration key `key`.

        Raises:
            ValueResolutionError: Not configured, unauthenticated/unreachable,
                or `key` does not exist in the store.
        """
        client = self._get_client()
        try:
            setting = client.get_configuration_setting(key=key)
        except ResourceNotFoundError as exc:
            raise ValueResolutionError(f"Azure App Configuration: no key named '{key}' in '{self._endpoint}'.") from exc
        except AzureError as exc:
            raise ValueResolutionError(f"Azure App Configuration: could not resolve '{key}': {exc}") from exc
        if setting is None or setting.value is None:
            raise ValueResolutionError(f"Azure App Configuration: key '{key}' has no value.")
        return setting.value

    def set(self, key: str, value: str) -> None:
        """Create or overwrite configuration key `key`
        (`set_configuration_setting()` is already an upsert).

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or the write itself failed.
        """
        try:
            client = self._get_client()
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc
        try:
            client.set_configuration_setting(ConfigurationSetting(key=key, value=value))
        except AzureError as exc:
            raise IntegrationError(f"Azure App Configuration: could not write '{key}': {exc}") from exc

    def get(self, key: str) -> KeyValueEntry | None:
        """Return the current value and version of configuration key `key`,
        or `None` if it does not exist in the store — reuses `resolve()`'s
        own `get_configuration_setting()` call, but treats "not found" as
        a normal outcome here, not a failure (docs/work/
        key-value-store-integration.md's Backend 2).

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or unreachable.
        """
        try:
            client = self._get_client()
            setting = client.get_configuration_setting(key=key)
        except ResourceNotFoundError:
            return None
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc
        except AzureError as exc:
            raise IntegrationError(f"Azure App Configuration: could not resolve '{key}': {exc}") from exc
        if setting is None or setting.value is None:
            raise IntegrationError(f"Azure App Configuration: key '{key}' has no value.")
        return KeyValueEntry(value=setting.value.encode(), version=setting.etag or "")

    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        """Write `value` to configuration key `key`, returning the new ETag.

        Args:
            if_version: `NEW_KEY_VERSION` maps to `MatchConditions.IfMissing`
                (succeeds only if `key` doesn't exist yet); any other value
                maps to `MatchConditions.IfNotModified` with that value as
                the required ETag (succeeds only if `key`'s current ETag
                still matches); omitted is an unconditional write — all
                three natively supported by the SDK's own
                `set_configuration_setting()`, confirmed empirically
                against the installed package.

        Raises:
            strata.integrations.errors.KeyValueConflictError: the
                condition `if_version` specified didn't hold.
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or the write itself failed.
        """
        try:
            client = self._get_client()
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc
        setting = ConfigurationSetting(key=key, value=value.decode())
        try:
            if if_version == NEW_KEY_VERSION:
                result = client.set_configuration_setting(setting, MatchConditions.IfMissing)
            elif if_version is not None:
                result = client.set_configuration_setting(setting, MatchConditions.IfNotModified, etag=if_version)
            else:
                result = client.set_configuration_setting(setting)
        except ResourceModifiedError as exc:
            raise KeyValueConflictError(f"Azure App Configuration: '{key}' was modified concurrently.") from exc
        except AzureError as exc:
            raise IntegrationError(f"Azure App Configuration: could not write '{key}': {exc}") from exc
        return result.etag or ""
