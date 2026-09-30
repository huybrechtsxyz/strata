#!/usr/bin/env python3
"""Azure Key Vault secret resolver — Azure SDK, not the `az` CLI.

The vault URL may come from a bound `kind: integration` document's
`spec.endpoints.address` (docs/design/store-integration-configuration.md's
Phase 2) — falls back to the environment variable below, unchanged, when
no document is bound or its `endpoints` is unset:

- ``AZURE_KEYVAULT_URL`` — the vault URL (e.g. ``https://my-vault.vault.azure.net``).

Authentication uses `azure.identity.DefaultAzureCredential`, which already
chains every mode v1 supported by hand (environment/service-principal,
workload identity / OIDC, managed identity, `az login`) — the real
production auth mode confirmed in `/memories/repo/v1-consumer-usage.md` is
managed identity, which `DefaultAzureCredential` picks up with no
configuration when running on an Azure resource with one assigned. A
user-assigned identity's client id is picked up from the standard
``AZURE_CLIENT_ID`` env var by the same chain — no strata-specific wiring
needed, and never read from `spec.authentication` (see that design's own
"Deliberately out of scope" section for why).

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
from strata.integrations.errors import IntegrationError, ValueResolutionError
from strata.integrations.resolved_context import SecretMetadata
from strata.models.integration_model import Capability, IntegrationModel


class AzureKeyVaultResolver(StoreIntegration):
    """Resolves Azure Key Vault secrets, by name."""

    TYPE = "azure-keyvault"
    CAPABILITIES = frozenset({Capability.SECRETS})
    TRANSPORTS = frozenset({"sdk"})

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        endpoint_address = config.spec.endpoints.address if config is not None and config.spec.endpoints else None
        self._vault_url = endpoint_address or environ.get("AZURE_KEYVAULT_URL")
        self._client: SecretClient | None = None

    def _get_client(self) -> SecretClient:
        if not self._vault_url:
            raise ValueResolutionError(
                "Azure Key Vault: no vault URL configured — set AZURE_KEYVAULT_URL, or bind an "
                "Integration document with spec.endpoints.address set."
            )
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

    def set(self, key: str, value: str) -> None:
        """Create or overwrite secret `key` (`SecretClient.set_secret()` is
        already an upsert — no separate create/update call needed here,
        unlike Infisical's real API).

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or the write itself failed.
        """
        try:
            client = self._get_client()
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc
        try:
            client.set_secret(key, value)
        except AzureError as exc:
            raise IntegrationError(f"Azure Key Vault: could not write '{key}': {exc}") from exc

    def metadata(self, key: str) -> SecretMetadata | None:
        """Best-effort created/updated timestamps, from the same
        `get_secret()` call `resolve()` uses (`SecretProperties.created_on`/
        `.updated_on` — already-parsed `datetime` objects, no further
        parsing needed here unlike Infisical's raw ISO-8601 strings).

        Returns `None` when `key` doesn't exist — not configured/unreachable
        still raises, matching `resolve()`'s own treatment of those cases.

        Raises:
            strata.integrations.errors.IntegrationError: Not configured,
                unauthenticated, or unreachable.
        """
        try:
            client = self._get_client()
            secret = client.get_secret(key)
        except ResourceNotFoundError:
            return None
        except ValueResolutionError as exc:
            raise IntegrationError(str(exc)) from exc
        except AzureError as exc:
            raise IntegrationError(f"Azure Key Vault: could not read metadata for '{key}': {exc}") from exc
        properties = secret.properties
        return SecretMetadata(created_at=properties.created_on, updated_at=properties.updated_on)
