"""Azure Key Vault integration (secrets via az CLI or REST API)."""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.integrations.capabilities import ISecretStore
from xyz_platform.integrations.store_integration import StoreIntegration
from xyz_platform.models.integration_model import IntegrationModel

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
        self.tenant_id = self._get_env_var(
            self._get_auth_var_name("tenant_id", "AZURE_TENANT_ID")
        )
        self.client_id = self._get_env_var(
            self._get_auth_var_name("client_id", "AZURE_CLIENT_ID")
        )
        self.client_secret = self._get_env_var(
            self._get_auth_var_name("client_secret", "AZURE_CLIENT_SECRET")
        )
        self.subscription_id = self._get_env_var(
            "AZURE_SUBSCRIPTION_ID"
        )  # no model equivalent

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

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available with proper configuration.

        Returns:
            Tuple of (success, error_message)
        """
        # Check integration availability
        if not self.is_available():
            self._info = (
                f"{self.integration_name} CLI (az) is not installed or not in PATH."
            )
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

        # Check authentication configuration
        has_oidc = self.tenant_id and self.client_id and self.subscription_id
        has_secret = self.tenant_id and self.client_id and self.client_secret

        if not has_oidc and not has_secret:
            self._info = f"{self.integration_name} authentication not configured."
            logger.warning("Azure authentication not configured", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} authentication not configured. Set either:\n"
                "  OIDC: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_SUBSCRIPTION_ID\n"
                "  Secret: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug("Azure Key Vault is available and configured", name=self.integration_name, version=self.get_version())
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["keyvault_url"] = self.keyvault_url
        info["has_oidc"] = bool(
            self.tenant_id and self.client_id and self.subscription_id
        )
        info["has_secret"] = bool(
            self.tenant_id and self.client_id and self.client_secret
        )
        return info

    # Secret management methods (ISecretStore implementation)

    def get_secret(
        self, key: str, prefer_cli: bool = True, timeout: int = 60, **kwargs
    ) -> Optional[str]:
        """
        Retrieve a secret from Azure Key Vault.

        Implements the unified store interface from StoreIntegration.

        Args:
            key: The name of the secret in Key Vault (secret_name)
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            The secret value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot retrieve secret from Azure Key Vault", name=self.integration_name, error=error)
            return None

        logger.debug(
            "Retrieving secret from Azure Key Vault",
            name=self.integration_name,
            secret_name=key,
            prefer_cli=prefer_cli,
        )

        if prefer_cli:
            # Try Azure CLI first
            result = self._get_secret_via_cli(key, timeout)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via CLI", name=self.integration_name, secret_name=key)
                return result

            # Fall back to API with CLI token
            result = self._get_secret_via_api(key, use_cli_token=True)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via API (CLI token)", name=self.integration_name, secret_name=key)
                return result

            # Fall back to API with client credentials
            result = self._get_secret_via_api(key, use_cli_token=False)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via API (client credentials)", name=self.integration_name, secret_name=key)
            return result
        else:
            # Try API with client credentials first
            result = self._get_secret_via_api(key, use_cli_token=False)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via API (client credentials)", name=self.integration_name, secret_name=key)
                return result

            # Fall back to API with CLI token
            result = self._get_secret_via_api(key, use_cli_token=True)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via API (CLI token)", name=self.integration_name, secret_name=key)
                return result

            # Fall back to Azure CLI
            result = self._get_secret_via_cli(key, timeout)
            if result:
                logger.info("Secret retrieved from Azure Key Vault via CLI", name=self.integration_name, secret_name=key)
            return result

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
                logger.info("Secrets listed from Azure Key Vault via CLI", name=self.integration_name, count=len(result), prefix=prefix)
                return result

            # Fall back to API with CLI token
            result = self._list_secrets_via_api(use_cli_token=True)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info("Secrets listed from Azure Key Vault via API (CLI token)", name=self.integration_name, count=len(result), prefix=prefix)
                return result

            # Fall back to API with client credentials
            result = self._list_secrets_via_api(use_cli_token=False)
            # Filter by prefix
            if prefix:
                result = [s for s in result if s.startswith(prefix)]
            logger.info("Secrets listed from Azure Key Vault via API (client credentials)", name=self.integration_name, count=len(result), prefix=prefix)
            return result
        else:
            # Try API with client credentials first
            result = self._list_secrets_via_api(use_cli_token=False)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info("Secrets listed from Azure Key Vault via API (client credentials)", name=self.integration_name, count=len(result), prefix=prefix)
                return result

            # Fall back to API with CLI token
            result = self._list_secrets_via_api(use_cli_token=True)
            if result is not None:
                # Filter by prefix
                if prefix:
                    result = [s for s in result if s.startswith(prefix)]
                logger.info("Secrets listed from Azure Key Vault via API (CLI token)", name=self.integration_name, count=len(result), prefix=prefix)
                return result

            # Fall back to Azure CLI
            result = self._list_secrets_via_cli(timeout)
            # Filter by prefix
            if prefix:
                result = [s for s in result if s.startswith(prefix)]
            logger.info("Secrets listed from Azure Key Vault via CLI", name=self.integration_name, count=len(result), prefix=prefix)
            return result

    # Authentication methods

    def _get_access_token_via_cli(self) -> Optional[str]:
        """
        Get Azure access token using Azure CLI.
        Works with both subscription (OIDC) and client secret auth.

        Returns:
            Access token string or None if failed
        """
        try:
            result = self._run_integration(
                args=[
                    "account",
                    "get-access-token",
                    "--resource",
                    "https://vault.azure.net",
                ],
                timeout=30,
            )

            if result.returncode == 0 and result.stdout:
                token_data = json.loads(result.stdout)
                token = token_data.get("accessToken")
                if token:
                    logger.debug("Successfully obtained Azure access token via CLI", name=self.integration_name)
                return token

            logger.warning("Failed to get Azure access token via CLI", name=self.integration_name)
            return None

        except Exception as e:
            logger.warning("Error getting Azure access token via CLI", name=self.integration_name, error_type=type(e).__name__)
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
            token_url = (
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            )
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
                    logger.info("Successfully authenticated to Azure Key Vault using client credentials", name=self.integration_name)
                return token
        except Exception as e:
            logger.warning("Client credentials authentication to Azure Key Vault failed", name=self.integration_name, error_type=type(e).__name__)
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

    def _get_secret_via_api(
        self, secret_name: str, use_cli_token: bool = False
    ) -> Optional[str]:
        """
        Retrieve a secret from Azure Key Vault using HTTPS API call.

        Args:
            secret_name: The name of the secret in Key Vault
            use_cli_token: If True, get token via Azure CLI; if False, use client credentials

        Returns:
            The secret value if found, None otherwise
        """
        try:
            # Get access token
            if use_cli_token:
                access_token = self._get_access_token_via_cli()
            else:
                access_token = self._get_access_token_via_api()

            if not access_token:
                return None

            # Call Key Vault API
            secret_url = f"{self.keyvault_url}secrets/{secret_name}?api-version=7.4"
            req = urllib.request.Request(secret_url)
            req.add_header("Authorization", f"Bearer {access_token}")

            with urllib.request.urlopen(req, timeout=10) as response:
                secret_data = json.loads(response.read().decode("utf-8"))
                return secret_data.get("value")
        except Exception:
            return None

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
