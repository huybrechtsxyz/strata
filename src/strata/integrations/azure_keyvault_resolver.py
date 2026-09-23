#!/usr/bin/env python3
"""Azure Key Vault secret resolver — Azure SDK, not the `az` CLI.

Configured entirely from environment variables:

- ``AZURE_KEYVAULT_URL`` — the vault URL (e.g. ``https://my-vault.vault.azure.net``).

Authentication uses `azure.identity.DefaultAzureCredential`, which already
chains every mode v1 supported by hand (environment/service-principal,
workload identity / OIDC, managed identity, `az login`) — the real
production auth mode confirmed in `/memories/repo/v1-consumer-usage.md` is
managed identity, which `DefaultAzureCredential` picks up with no
configuration when running on an Azure resource with one assigned. A
user-assigned identity's client id is picked up from the standard
``AZURE_CLIENT_ID`` env var by the same chain — no strata-specific wiring
needed.

ADR-0021 D7 retrofit: a `StoreIntegration`, `TRANSPORTS={"sdk"}` — keeps
`SecretClient` unchanged. No `http_request`/`self.request()` involved: the
SDK client carries its own auth chain and HTTP stack, and reimplementing
that would be the opposite of leaner.
"""

from os import environ

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from strata.integrations.capabilities import StoreIntegration
from strata.integrations.errors import ValueResolutionError
from strata.models.integration_model import IntegrationModel


class AzureKeyVaultResolver(StoreIntegration):
    """Resolves Azure Key Vault secrets, by name."""

    TYPE = "azure-keyvault"
    CAPABILITIES = frozenset({"secrets"})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._vault_url = environ.get("AZURE_KEYVAULT_URL")
        self._client: SecretClient | None = None

    def _get_client(self) -> SecretClient:
        if not self._vault_url:
            raise ValueResolutionError("Azure Key Vault: AZURE_KEYVAULT_URL is not set.")
        if self._client is None:
            self._client = SecretClient(vault_url=self._vault_url, credential=DefaultAzureCredential())
        return self._client

    def resolve(self, key: str) -> str:
        """Return the value of secret `key`.

        Raises:
            ValueResolutionError: Not configured, unauthenticated/unreachable,
                or `key` does not exist in the vault.
        """
        client = self._get_client()
        try:
            secret = client.get_secret(key)
        except ResourceNotFoundError as exc:
            raise ValueResolutionError(f"Azure Key Vault: no secret named '{key}' in '{self._vault_url}'.") from exc
        except AzureError as exc:
            raise ValueResolutionError(f"Azure Key Vault: could not resolve '{key}': {exc}") from exc
        if secret.value is None:
            raise ValueResolutionError(f"Azure Key Vault: secret '{key}' has no value.")
        return secret.value
