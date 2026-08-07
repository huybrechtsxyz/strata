"""Azure Key Vault integration (secrets via az CLI or REST API)."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from strata.exceptions import SecretStoreUnavailableError
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.capabilities import ISecretStore
from strata.models.integration_model import IntegrationModel
from strata.utils.secret_metadata import SecretMetadata

logger = get_logger(__name__)


class AzureKeyVaultIntegration(StoreIntegration):
    """
    Azure Key Vault integration for secret storage.

    Implements singleton pattern per vault URL - multiple instances
    possible for different Azure Key Vault URLs.

    Supports multiple authentication methods:
    - Azure CLI (az login)
    - Service Principal with client secret
    - Service Principal with OIDC (federated identity)
    """

    # Command executable name
    COMMAND = "az"

    # Declare supported capabilities
    CAPABILITIES = [ISecretStore]

    # Singleton instance keying based on endpoint

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on Key Vault URL.

        Creates separate singleton instances per vault URL.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Normalized vault URL string or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        # Get vault URL from config
        vault_url = ""
        if config.endpoints and config.endpoints.address:
            vault_url = config.endpoints.address
            # Normalize vault URL
            if vault_url and not vault_url.endswith("/"):
                vault_url += "/"

        return vault_url or config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize Azure Key Vault integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Get vault URL from config
        self.keyvault_url = ""
        if self.config.endpoints and self.config.endpoints.address:
            self.keyvault_url = self._resolve_env_vars(self.config.endpoints.address)
            if self.keyvault_url and not self.keyvault_url.endswith("/"):
                self.keyvault_url += "/"

        # Get authentication configuration from environment variables.
        # Field names in the oauth2 sub-model are env-var name references.
        self.tenant_id = self._get_env_var(self._get_auth_var_name("tenant_id", "AZURE_TENANT_ID"))
        self.client_id = self._get_env_var(self._get_auth_var_name("client_id", "AZURE_CLIENT_ID"))
        self.client_secret = self._get_env_var(self._get_auth_var_name("client_secret", "AZURE_CLIENT_SECRET"))
        self.subscription_id = self._get_env_var("AZURE_SUBSCRIPTION_ID")  # no model equivalent

        logger.debug(
            "Azure Key Vault integration initialized",
            name=self.integration_name,
            has_vault_url=bool(self.keyvault_url),
        )

    # Auth helpers

    def _get_auth_var_name(self, field: str, default: str) -> str:
        """
        Return the env-var name for an OAuth2 credential field.

        When ``authentication.method == "oauth2"`` the sub-model fields hold
        env-var name references (e.g. ``oauth2.client_id = "AZURE_CLIENT_ID"``)
        rather than literal values.  Falls back to *default* for backward compat.
        """
        auth = self.config.authentication
        if auth and auth.method == "oauth2" and auth.oauth2:
            val = getattr(auth.oauth2, field, None)
            if val:
                return val
        return default

    def _is_cli_logged_in(self) -> bool:
        """
        Check whether there is an active Azure CLI login session.

        Returns True if ``az account show`` succeeds (exit code 0), which is
        the case after running ``az login`` on a developer workstation or when
        running inside an Azure resource with a Managed Identity assigned.
        """
        try:
            result = self._run_integration(args=["account", "show"], timeout=10)
            return result.returncode == 0
        except Exception:
            return False

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve az CLI version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from az CLI output.

        Args:
            version_output: Raw JSON output from az version

        Returns:
            Version string (e.g., "2.55.0")
        """
        try:
            version_data = json.loads(version_output)
            if "azure-cli" in version_data:
                return version_data["azure-cli"]
            return version_output.strip()
        except Exception:
            # Fallback to regex if JSON parsing fails
            match = re.search(r'"azure-cli":\s*"([^"]+)"', version_output)
            if match:
                return match.group(1)
            return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for azure_keyvault."""
        return {
            "name": "azure_keyvault",
            "command": None,
            "install_url": "https://learn.microsoft.com/en-us/azure/key-vault/",
            "env_vars": [
                {"name": "AZURE_TENANT_ID", "purpose": "Azure Active Directory tenant ID", "required": True},
                {
                    "name": "AZURE_CLIENT_ID",
                    "purpose": "Service principal / managed identity client ID",
                    "required": True,
                },
                {
                    "name": "AZURE_CLIENT_SECRET",
                    "purpose": "Service principal client secret (omit for OIDC / managed identity)",
                    "required": False,
                },
                {"name": "AZURE_SUBSCRIPTION_ID", "purpose": "Azure subscription ID", "required": True},
            ],
            "auth_methods": [
                {
                    "method": "az login (interactive)",
                    "description": "Run 'az login' on your workstation. No env vars required. Preferred for local development.",
                },
                {
                    "method": "Service principal (secret)",
                    "description": "Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET.",
                },
                {
                    "method": "OIDC / Workload Identity",
                    "description": "Set AZURE_TENANT_ID and AZURE_CLIENT_ID; omit AZURE_CLIENT_SECRET. Used in GitHub Actions / Azure Pipelines.",
                },
                {
                    "method": "Managed Identity",
                    "description": "No env vars required when running on an Azure resource with MI assigned.",
                },
            ],
            "yaml_example": "type: azure_keyvault\nspec:\n  vault_url: https://my-vault.vault.azure.net",
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available with proper configuration.

        Returns:
            Tuple of (success, error_message)
        """
        # Check integration availability
        if not self.is_available():
            self._info = f"{self.integration_name} CLI (az) is not installed or not in PATH."
            logger.warning("Azure CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI (az) is not installed or not in PATH. "
                "Install from https://learn.microsoft.com/cli/azure/install-azure-cli",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning("Azure CLI version validation failed", name=self.integration_name, error=version_error)
            return False, version_error

        # Check Key Vault URL
        if not self.keyvault_url:
            self._info = f"{self.integration_name} Key Vault URL not configured."
            logger.warning("Azure Key Vault URL not configured", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} Key Vault URL not configured. "
                "Set endpoints.address in config or KEYVAULT_URL environment variable "
                "(e.g., https://myvault.vault.azure.net/)",
            )

        # Check authentication configuration — accept az login, OIDC, or client secret
        has_oidc = self.tenant_id and self.client_id and self.subscription_id
        has_secret = self.tenant_id and self.client_id and self.client_secret
        has_cli_login = self._is_cli_logged_in()

        if not has_oidc and not has_secret and not has_cli_login:
            self._info = f"{self.integration_name} authentication not configured."
            logger.warning("Azure authentication not configured", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} authentication not configured. Set either:\n"
                "  az login (developer workstation)\n"
                "  OIDC: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_SUBSCRIPTION_ID\n"
                "  Secret: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Azure Key Vault is available and configured", name=self.integration_name, version=self.get_version()
        )
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["keyvault_url"] = self.keyvault_url
        info["has_cli_login"] = self._is_cli_logged_in()
        info["has_oidc"] = bool(self.tenant_id and self.client_id and self.subscription_id)
        info["has_secret"] = bool(self.tenant_id and self.client_id and self.client_secret)
        return info

    # Secret management methods (ISecretStore implementation)

    def get_secret(self, key: str, prefer_cli: bool = True, timeout: int = 60, **kwargs) -> Optional[str]:
        """
        Retrieve a secret from Azure Key Vault.

        Implements the unified store interface from StoreIntegration.

        Tries up to three methods in order (CLI, API with CLI token, API with
        client credentials — or the reverse when ``prefer_cli`` is False). Each
        method that fails with a real problem (auth, network, non-404 HTTP
        status) is treated as "try the next method", not as a final answer —
        only when every method has been exhausted without confirming a 404 is
        the last error re-raised.

        Args:
            key: The name of the secret in Key Vault (secret_name)
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            The secret value, or ``None`` only when an API attempt confirmed
            the secret does not exist (HTTP 404).

        Raises:
            SecretStoreUnavailableError: Key Vault is not configured, or every
                attempted method failed without confirming "not found".
        """
        available, error = self.ensure_available()
        if not available:
            raise SecretStoreUnavailableError(self.integration_name, error)

        logger.debug(
            "Retrieving secret from Azure Key Vault",
            name=self.integration_name,
            secret_name=key,
            prefer_cli=prefer_cli,
        )

        if prefer_cli:
            attempts: List[Tuple[str, Any]] = [
                ("CLI", lambda: self._get_secret_via_cli(key, timeout)),
                ("API (CLI token)", lambda: self._get_secret_via_api(key, use_cli_token=True)),
                ("API (client credentials)", lambda: self._get_secret_via_api(key, use_cli_token=False)),
            ]
        else:
            attempts = [
                ("API (client credentials)", lambda: self._get_secret_via_api(key, use_cli_token=False)),
                ("API (CLI token)", lambda: self._get_secret_via_api(key, use_cli_token=True)),
                ("CLI", lambda: self._get_secret_via_cli(key, timeout)),
            ]

        last_error: Optional[SecretStoreUnavailableError] = None
        for label, attempt in attempts:
            try:
                result = attempt()
            except SecretStoreUnavailableError as exc:
                last_error = exc
                continue
            if result:
                logger.info(
                    f"Secret retrieved from Azure Key Vault via {label}", name=self.integration_name, secret_name=key
                )
                return result
            if label != "CLI":
                # An API attempt returning falsy/None is a confirmed 404 — the
                # CLI attempt's None is ambiguous (best-effort) and never hits
                # this branch since `label == "CLI"` there.
                return result

        # Every method failed without confirming "not found".
        if last_error is not None:
            raise last_error
        return None

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List all secret names in the Key Vault.

        Implements the unified store interface from StoreIntegration.

        Args:
            prefix: Filter secrets by name prefix (case-sensitive)
            **kwargs: Additional arguments (prefer_cli and timeout supported)

        Returns:
            List of secret names, or empty list if failed
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.debug("Cannot list secrets from Azure Key Vault", name=self.integration_name, error=error)
            return []

        logger.debug(
            "Listing secrets from Azure Key Vault",
            name=self.integration_name,
            prefix=prefix,
            prefer_cli=prefer_cli,
        )

        if prefer_cli:
            # Try Azure CLI first
            result = self._list_secrets_via_cli(timeout)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info(
                    "Secrets listed from Azure Key Vault via CLI",
                    name=self.integration_name,
                    count=len(result),
                    prefix=prefix,
                )
                return result

            # Fall back to API with CLI token
            result = self._list_secrets_via_api(use_cli_token=True)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info(
                    "Secrets listed from Azure Key Vault via API (CLI token)",
                    name=self.integration_name,
                    count=len(result),
                    prefix=prefix,
                )
                return result

            # Fall back to API with client credentials
            result = self._list_secrets_via_api(use_cli_token=False)
            # Filter by prefix
            if prefix:
                result = [s for s in result if s.startswith(prefix)]
            logger.info(
                "Secrets listed from Azure Key Vault via API (client credentials)",
                name=self.integration_name,
                count=len(result),
                prefix=prefix,
            )
            return result
        else:
            # Try API with client credentials first
            result = self._list_secrets_via_api(use_cli_token=False)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info(
                    "Secrets listed from Azure Key Vault via API (client credentials)",
                    name=self.integration_name,
                    count=len(result),
                    prefix=prefix,
                )
                return result

            # Fall back to API with CLI token
            result = self._list_secrets_via_api(use_cli_token=True)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info(
                    "Secrets listed from Azure Key Vault via API (CLI token)",
                    name=self.integration_name,
                    count=len(result),
                    prefix=prefix,
                )
                return result

            # Fall back to Azure CLI
            result = self._list_secrets_via_cli(timeout)
            # Filter by prefix
            if prefix:
                result = [s for s in result if s.startswith(prefix)]
            logger.info(
                "Secrets listed from Azure Key Vault via CLI",
                name=self.integration_name,
                count=len(result),
                prefix=prefix,
            )
            return result

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """
        Write a secret to Azure Key Vault (create-if-not-exists semantics).

        Implements ISecretStore interface.  Never overwrites an existing secret —
        if the key already exists the method returns True without writing.  On a
        concurrent write race the caller must re-read the value.

        Args:
            key: Secret name in Key Vault
            value: Secret value to store
            **kwargs: prefer_cli, timeout

        Returns:
            True if the secret exists (created now or already present), False on failure
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot write secret to Azure Key Vault", name=self.integration_name, error=error)
            return False

        # Check existence first — never overwrite
        existing = self.get_secret(key, prefer_cli=prefer_cli, timeout=timeout)
        if existing is not None:
            logger.info(
                "Secret already exists in Azure Key Vault — skipping write", name=self.integration_name, secret_name=key
            )
            return True

        logger.debug("Writing secret to Azure Key Vault", name=self.integration_name, secret_name=key)

        if prefer_cli:
            result = self._run_integration(
                args=[
                    "keyvault",
                    "secret",
                    "set",
                    "--vault-name",
                    self._vault_name(),
                    "--name",
                    key,
                    "--value",
                    value,
                    "--output",
                    "none",
                ],
                timeout=timeout,
            )
            if result.returncode == 0:
                logger.info("Secret written to Azure Key Vault via CLI", name=self.integration_name, secret_name=key)
                return True
            logger.warning(
                "Failed to write secret via CLI — trying API",
                name=self.integration_name,
                secret_name=key,
                stderr=result.stderr,
            )

        # API fallback
        ok = self._set_secret_via_api(key, value, use_cli_token=True)
        if not ok:
            ok = self._set_secret_via_api(key, value, use_cli_token=False)
        if ok:
            logger.info("Secret written to Azure Key Vault via API", name=self.integration_name, secret_name=key)
        else:
            logger.warning("Failed to write secret to Azure Key Vault", name=self.integration_name, secret_name=key)
        return ok

    def get_secret_metadata(self, key: str, **kwargs) -> Optional[SecretMetadata]:
        """Return creation/update timestamps for a Key Vault secret."""
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            return None

        data = None
        if prefer_cli:
            result = self._run_integration(
                args=[
                    "keyvault",
                    "secret",
                    "show",
                    "--vault-name",
                    self._vault_name(),
                    "--name",
                    key,
                    "--output",
                    "json",
                ],
                timeout=timeout,
            )
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                except (json.JSONDecodeError, ValueError):
                    return None

        if data is None:
            # API fallback
            token = self._get_access_token_via_cli() or self._get_access_token_via_api()
            if not token:
                return None
            try:
                url = f"{self.keyvault_url.rstrip('/')}secrets/{urllib.parse.quote(key, safe='')}?api-version=7.4"
                req = urllib.request.Request(url, method="GET")
                req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                return None

        if data is None:
            return None

        attrs = data.get("attributes") or {}
        meta = SecretMetadata()
        if attrs.get("created"):
            meta.created_at = datetime.fromtimestamp(attrs["created"], tz=timezone.utc)
        if attrs.get("updated"):
            meta.updated_at = datetime.fromtimestamp(attrs["updated"], tz=timezone.utc)
        if attrs.get("expires"):
            meta.expires_on = datetime.fromtimestamp(attrs["expires"], tz=timezone.utc)
        # Extract version from the id URL (last segment)
        secret_id = data.get("id", "")
        if "/" in secret_id:
            meta.version = secret_id.rsplit("/", 1)[-1]
        return meta

    def update_secret(self, key: str, value: str, **kwargs) -> bool:
        """Overwrite an existing secret in Key Vault (rotation only).

        Unlike set_secret() this skips the existence check and writes unconditionally.
        Key Vault creates a new version automatically.
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot update secret in Azure Key Vault", name=self.integration_name, error=error)
            return False

        if prefer_cli:
            result = self._run_integration(
                args=[
                    "keyvault",
                    "secret",
                    "set",
                    "--vault-name",
                    self._vault_name(),
                    "--name",
                    key,
                    "--value",
                    value,
                    "--output",
                    "none",
                ],
                timeout=timeout,
            )
            if result.returncode == 0:
                logger.info("Secret updated in Azure Key Vault via CLI", name=self.integration_name, secret_name=key)
                return True
            logger.warning(
                "Failed to update secret via CLI — trying API",
                name=self.integration_name,
                secret_name=key,
            )

        ok = self._set_secret_via_api(key, value, use_cli_token=True)
        if not ok:
            ok = self._set_secret_via_api(key, value, use_cli_token=False)
        if ok:
            logger.info("Secret updated in Azure Key Vault via API", name=self.integration_name, secret_name=key)
        return ok

    def _vault_name(self) -> str:
        """Extract the vault name from the vault URL (e.g. 'myvault' from 'https://myvault.vault.azure.net/')."""
        url = self.keyvault_url.rstrip("/")
        # e.g. https://myvault.vault.azure.net -> myvault
        host = url.split("//")[-1].split(".")[0] if "//" in url else url.split(".")[0]
        return host

    def _set_secret_via_api(self, key: str, value: str, use_cli_token: bool) -> bool:
        """Write a secret via the Key Vault REST API."""
        import json as _json  # local to keep top-level imports unchanged

        token = self._get_access_token_via_cli() if use_cli_token else self._get_access_token_via_api()
        if not token:
            return False
        try:
            url = f"{self.keyvault_url.rstrip('/')}secrets/{urllib.parse.quote(key, safe='')}?api-version=7.4"
            body = _json.dumps({"value": value}).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="PUT")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

    # Authentication methods

    def _get_access_token_via_cli(self) -> Optional[str]:
        """Get Azure access token via AzureCLIIntegration (cached per resource scope)."""
        try:
            from strata.integrations.azure_cli import AzureCLIIntegration
            from strata.models.integration_model import IntegrationModel

            az = AzureCLIIntegration(IntegrationModel(name="azure", type="azure_cli"))
            token = az.get_access_token(resource="https://vault.azure.net")
            if token:
                logger.debug("Azure access token obtained via AzureCLIIntegration (cached)", name=self.integration_name)
            return token
        except Exception as e:
            logger.warning(
                "Error getting Azure access token via CLI", name=self.integration_name, error_type=type(e).__name__
            )
            return None

    def _get_access_token_via_api(self) -> Optional[str]:
        """
        Get Azure access token using HTTPS API call with client credentials.
        Only works with client secret authentication.

        Returns:
            Access token string or None if failed
        """
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            return None

        try:
            logger.debug("Authenticating to Azure Key Vault using client credentials", name=self.integration_name)
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            data = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://vault.azure.net/.default",
                }
            ).encode("utf-8")

            req = urllib.request.Request(token_url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            with urllib.request.urlopen(req, timeout=10) as response:
                token_response = json.loads(response.read().decode("utf-8"))
                token = token_response.get("access_token")
                if token:
                    logger.info(
                        "Successfully authenticated to Azure Key Vault using client credentials",
                        name=self.integration_name,
                    )
                return token
        except Exception as e:
            logger.warning(
                "Client credentials authentication to Azure Key Vault failed",
                name=self.integration_name,
                error_type=type(e).__name__,
            )
            return None

    # Internal secret methods

    def _get_secret_via_cli(self, secret_name: str, timeout: int = 60) -> Optional[str]:
        """
        Retrieve a secret from Azure Key Vault using the Azure CLI.

        Args:
            secret_name: The name of the secret in Key Vault
            timeout: Command timeout in seconds

        Returns:
            The secret value if found, None otherwise
        """
        try:
            # Extract vault name from URL
            vault_name = self.keyvault_url.replace("https://", "").split(".")[0]

            result = self._run_integration(
                args=[
                    "keyvault",
                    "secret",
                    "show",
                    "--vault-name",
                    vault_name,
                    "--name",
                    secret_name,
                    "--query",
                    "value",
                    "-o",
                    "tsv",
                ],
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()

            return None

        except Exception:
            return None

    def _get_secret_via_api(self, secret_name: str, use_cli_token: bool = False) -> Optional[str]:
        """
        Retrieve a secret from Azure Key Vault using HTTPS API call.

        Args:
            secret_name: The name of the secret in Key Vault
            use_cli_token: If True, get token via Azure CLI; if False, use client credentials

        Returns:
            The secret value, or ``None`` only for a confirmed HTTP 404 (secret
            genuinely not found).

        Raises:
            SecretStoreUnavailableError: no access token could be obtained via
                this specific method, or any other failure (network, non-404
                HTTP status) occurred. The caller (``get_secret``) treats this
                as "try the next method", not a final answer.
        """
        access_token = self._get_access_token_via_cli() if use_cli_token else self._get_access_token_via_api()
        if not access_token:
            raise SecretStoreUnavailableError(
                self.integration_name,
                f"could not obtain access token via {'Azure CLI' if use_cli_token else 'client credentials'}",
            )

        secret_url = f"{self.keyvault_url}secrets/{secret_name}?api-version=7.4"
        try:
            req = urllib.request.Request(secret_url)
            req.add_header("Authorization", f"Bearer {access_token}")

            with urllib.request.urlopen(req, timeout=10) as response:
                secret_data = json.loads(response.read().decode("utf-8"))
                return secret_data.get("value")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # confirmed: secret does not exist
            logger.warning(
                "Azure Key Vault API secret retrieval failed",
                secret_name=secret_name,
                http_status=e.code,
                name=self.integration_name,
            )
            raise SecretStoreUnavailableError(
                self.integration_name, f"HTTP {e.code} from Key Vault API", cause=e
            ) from e
        except Exception as e:
            logger.warning(
                "Azure Key Vault API secret retrieval failed",
                secret_name=secret_name,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            raise SecretStoreUnavailableError(self.integration_name, f"{type(e).__name__}: {e}", cause=e) from e

    def _list_secrets_via_cli(self, timeout: int = 60) -> List[str]:
        """
        List all secret names in the Key Vault using Azure CLI.

        Args:
            timeout: Command timeout in seconds

        Returns:
            List of secret names, or empty list if failed
        """
        try:
            # Extract vault name from URL
            vault_name = self.keyvault_url.replace("https://", "").split(".")[0]

            result = self._run_integration(
                args=[
                    "keyvault",
                    "secret",
                    "list",
                    "--vault-name",
                    vault_name,
                    "--query",
                    "[].name",
                    "-o",
                    "tsv",
                ],
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                # Split output by newlines and filter out empty strings
                secrets = [s.strip() for s in result.stdout.split("\n") if s.strip()]
                return secrets

            return []

        except Exception:
            return []

    def _list_secrets_via_api(self, use_cli_token: bool = False) -> List[str]:
        """
        List all secret names in the Key Vault using HTTPS API.

        Args:
            use_cli_token: If True, get token via Azure CLI; if False, use client credentials

        Returns:
            List of secret names, or empty list if failed
        """
        try:
            # Get access token
            if use_cli_token:
                access_token = self._get_access_token_via_cli()
            else:
                access_token = self._get_access_token_via_api()

            if not access_token:
                return []

            # Call Key Vault API
            list_url = f"{self.keyvault_url}secrets?api-version=7.4"
            req = urllib.request.Request(list_url)
            req.add_header("Authorization", f"Bearer {access_token}")

            with urllib.request.urlopen(req, timeout=10) as response:
                list_data = json.loads(response.read().decode("utf-8"))
                # Extract secret names from the response
                secrets = []
                for item in list_data.get("value", []):
                    if "id" in item:
                        # Secret ID format: https://vault.vault.azure.net/secrets/secret-name
                        secret_name = item["id"].rstrip("/").split("/")[-1]
                        secrets.append(secret_name)
                return secrets
        except Exception:
            return []
