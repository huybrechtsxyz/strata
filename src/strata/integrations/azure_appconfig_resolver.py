#!/usr/bin/env python3
"""Azure App Configuration resolver — variables and feature flags.

Configured entirely from environment variables:

- ``AZURE_APPCONFIG_ENDPOINT`` — the store endpoint
  (e.g. ``https://my-config.azconfig.io``).

Authentication uses `azure.identity.DefaultAzureCredential` — see
`azure_keyvault_resolver.py`'s docstring for why (same reasoning, same
managed-identity production usage).

Resolves a plain configuration key/value. **Not yet special-cased for a
`.appconfig.featureflag/*`-style feature flag key** — Azure App Config
stores those as a JSON blob (`{"id": ..., "enabled": ..., ...}`), and this
returns that blob verbatim rather than parsing out `enabled`. No production
evidence yet of a feature (as opposed to a variable) actually resolving
through this store — parse it properly once one does.

ADR-0021 D7 retrofit: a `StoreIntegration`, `TRANSPORTS={"sdk"}` — keeps
`AzureAppConfigurationClient` unchanged, same reasoning as the Key Vault
resolver.
"""

from os import environ

from azure.appconfiguration import AzureAppConfigurationClient
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import ValueResolutionError
from strata.models.integration_model import IntegrationModel


class AzureAppConfigResolver(StoreIntegration):
    """Resolves Azure App Configuration variables/feature flags, by key."""

    TYPE = "azure-appconfig"
    CAPABILITIES = frozenset({"variables", "features"})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._endpoint = environ.get("AZURE_APPCONFIG_ENDPOINT")
        self._client: AzureAppConfigurationClient | None = None

    def _get_client(self) -> AzureAppConfigurationClient:
        if not self._endpoint:
            raise ValueResolutionError("Azure App Configuration: AZURE_APPCONFIG_ENDPOINT is not set.")
        if self._client is None:
            self._client = AzureAppConfigurationClient(
                base_url=self._endpoint, credential=DefaultAzureCredential()
            )
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
            raise ValueResolutionError(
                f"Azure App Configuration: no key named '{key}' in '{self._endpoint}'."
            ) from exc
        except AzureError as exc:
            raise ValueResolutionError(f"Azure App Configuration: could not resolve '{key}': {exc}") from exc
        if setting is None or setting.value is None:
            raise ValueResolutionError(f"Azure App Configuration: key '{key}' has no value.")
        return setting.value
