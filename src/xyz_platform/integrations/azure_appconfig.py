#!/usr/bin/env python3
"""
===============================================================================
Script Name   : azure_appconfig.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Azure App Configuration integration for XYZ Platform.

Azure App Configuration integration for configuration values and feature flags.
Supports both Azure CLI and direct API authentication.
===============================================================================
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.integrations.capabilities import IFeatureStore, IVariableStore
from xyz_platform.integrations.store_integration import StoreIntegration
from xyz_platform.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class AzureAppConfigIntegration(StoreIntegration):
    """
    Azure App Configuration integration for configuration and feature flags.

    Implements singleton pattern per endpoint - multiple instances
    possible for different Azure App Configuration endpoints.

    Supports multiple authentication methods:
    - Connection String (APPCONFIG_CONNECTION_STRING)
    - Azure CLI (az login)
    - Service Principal with client secret
    - Service Principal with OIDC (federated identity)
    """

    # Command executable name
    COMMAND = "az"

    # Declare supported capabilities
    CAPABILITIES = [IVariableStore, IFeatureStore]

    # Singleton instance keying based on endpoint

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on appconfig endpoint.

        Creates separate singleton instances per endpoint.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Normalized endpoint string or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        # Get endpoint from config
        endpoint = ""
        if config.endpoints and config.endpoints.address:
            endpoint = config.endpoints.address
            # Normalize endpoint
            if endpoint and not endpoint.endswith("/"):
                endpoint += "/"

        return endpoint or config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize Azure App Configuration integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Get endpoint from config
        self.appconfig_endpoint = ""
        if self.config.endpoints and self.config.endpoints.address:
            self.appconfig_endpoint = self._resolve_env_vars(
                self.config.endpoints.address
            )
            if self.appconfig_endpoint and not self.appconfig_endpoint.endswith("/"):
                self.appconfig_endpoint += "/"

        # Get authentication configuration from environment variables
        self.tenant_id = self._get_env_var("AZURE_TENANT_ID")
        self.client_id = self._get_env_var("AZURE_CLIENT_ID")
        self.client_secret = self._get_env_var("AZURE_CLIENT_SECRET")
        self.subscription_id = self._get_env_var("AZURE_SUBSCRIPTION_ID")
        self.connection_string = self._get_env_var("APPCONFIG_CONNECTION_STRING")

        logger.debug(
            "Azure App Configuration integration initialized",
            extra={
                "integration_name": self.integration_name,
                "has_endpoint": bool(self.appconfig_endpoint),
                "has_connection_string": bool(self.connection_string),
            },
        )

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
            logger.warning(
                "Azure CLI not found", extra={"integration_name": self.integration_name}
            )
            return (
                False,
                f"{self.integration_name} CLI (az) is not installed or not in PATH. "
                "Install from https://learn.microsoft.com/cli/azure/install-azure-cli",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Azure CLI version validation failed",
                extra={
                    "integration_name": self.integration_name,
                    "error": version_error,
                },
            )
            return False, version_error

        # Check if connection string is provided (easiest method)
        if self.connection_string:
            self._info = f"{self.integration_name} configured with connection string authentication"
            logger.debug(
                "Azure App Configuration using connection string auth",
                extra={"integration_name": self.integration_name},
            )
            return True, ""

        # Check App Configuration endpoint
        if not self.appconfig_endpoint:
            self._info = f"{self.integration_name} endpoint not configured."
            logger.warning(
                "Azure App Configuration endpoint not configured",
                extra={"integration_name": self.integration_name},
            )
            return (
                False,
                f"{self.integration_name} endpoint not configured. "
                "Set endpoints.address in config or APPCONFIG_ENDPOINT environment variable "
                "(e.g., https://myappconfig.azconfig.io)",
            )

        # Check authentication configuration
        has_oidc = self.tenant_id and self.client_id and self.subscription_id
        has_secret = self.tenant_id and self.client_id and self.client_secret

        if not has_oidc and not has_secret:
            self._info = f"{self.integration_name} authentication not configured."
            logger.warning(
                "Azure authentication not configured",
                extra={"integration_name": self.integration_name},
            )
            return (
                False,
                f"{self.integration_name} authentication not configured. Set either:\n"
                "  Connection String: APPCONFIG_CONNECTION_STRING\n"
                "  OIDC: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_SUBSCRIPTION_ID\n"
                "  Secret: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Azure App Configuration is available and configured",
            extra={
                "integration_name": self.integration_name,
                "version": self.get_version(),
            },
        )
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["appconfig_endpoint"] = self.appconfig_endpoint
        info["has_connection_string"] = bool(self.connection_string)
        info["has_oidc"] = bool(
            self.tenant_id and self.client_id and self.subscription_id
        )
        info["has_secret"] = bool(
            self.tenant_id and self.client_id and self.client_secret
        )
        return info

    # Unified Store Interface Implementation (IVariableStore)

    def get_variable(self, key: str, **kwargs) -> Optional[str]:
        """
        Get a variable value from Azure App Configuration.

        Implements IVariableStore interface.

        Args:
            key: The key name
            **kwargs: Additional arguments (label, prefer_cli, timeout)

        Returns:
            The configuration value if found, None otherwise
        """
        label = kwargs.get("label")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self._get_value(key, label=label, prefer_cli=prefer_cli, timeout=timeout)

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List configuration keys from Azure App Configuration.

        Implements IVariableStore interface.

        Args:
            prefix: Filter keys by prefix (case-sensitive)
            **kwargs: Additional arguments (label, prefer_cli, timeout)

        Returns:
            List of configuration key names
        """
        label = kwargs.get("label")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        # Use key_filter to optimize if prefix provided
        key_filter = f"{prefix}*" if prefix else None

        result = self._list_keys(
            key_filter=key_filter,
            label=label,
            prefer_cli=prefer_cli,
            timeout=timeout,
        )

        # Apply prefix filter on results for exact matching
        if prefix:
            result = [k for k in result if k.startswith(prefix)]

        return result

    # Unified Store Interface Implementation (IFeatureStore)

    def get_feature(self, key: str, **kwargs) -> Optional[bool]:
        """
        Get a feature flag value from Azure App Configuration.

        Implements IFeatureStore interface.

        Args:
            key: The feature flag name
            **kwargs: Additional arguments (label, prefer_cli, timeout)

        Returns:
            Feature flag enabled status (bool) or None if not found
        """
        label = kwargs.get("label")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        result = self._get_flag(
            key, label=label, prefer_cli=prefer_cli, timeout=timeout
        )
        if result is None:
            return None
        # Extract enabled status from feature flag data
        if isinstance(result, dict):
            return result.get("enabled", False)
        return bool(result)

    def list_features(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List all feature flag names in Azure App Configuration.

        Implements IFeatureStore interface.

        Args:
            prefix: Optional prefix to filter feature names
            **kwargs: Additional arguments (label, prefer_cli, timeout)

        Returns:
            List of feature flag names
        """
        label = kwargs.get("label")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        result = self._list_flags(
            label=label,
            prefer_cli=prefer_cli,
            timeout=timeout,
        )

        # Apply prefix filter on results for exact matching
        if prefix:
            result = [k for k in result if k.startswith(prefix)]

        return result

    # App Configuration internal methods

    def _get_value(
        self,
        key: str,
        label: Optional[str] = None,
        prefer_cli: bool = True,
        timeout: int = 60,
    ) -> Optional[str]:
        """
        Retrieve a configuration value from Azure App Configuration.

        Args:
            key: The key name
            label: Optional label filter
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            The configuration value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot retrieve value from Azure App Configuration",
                extra={"error": error, "integration_name": self.integration_name},
            )
            return None

        logger.debug(
            "Retrieving value from Azure App Configuration",
            extra={
                "key": key,
                "label": label,
                "prefer_cli": prefer_cli,
                "integration_name": self.integration_name,
            },
        )

        if prefer_cli:
            # Try Azure CLI first
            result = self._get_value_via_cli(key, label, timeout)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via CLI",
                    extra={"key": key, "integration_name": self.integration_name},
                )
                return result

            # Fall back to API with CLI token
            result = self._get_value_via_api(key, label, use_cli_token=True)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via API (CLI token)",
                    extra={"key": key, "integration_name": self.integration_name},
                )
                return result

            # Fall back to API with client credentials
            result = self._get_value_via_api(key, label, use_cli_token=False)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via API (client credentials)",
                    extra={"key": key, "integration_name": self.integration_name},
                )
            return result
        else:
            # Try API with client credentials first
            result = self._get_value_via_api(key, label, use_cli_token=False)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via API (client credentials)",
                    extra={"key": key, "integration_name": self.integration_name},
                )
                return result

            # Fall back to API with CLI token
            result = self._get_value_via_api(key, label, use_cli_token=True)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via API (CLI token)",
                    extra={"key": key, "integration_name": self.integration_name},
                )
                return result

            # Fall back to Azure CLI
            result = self._get_value_via_cli(key, label, timeout)
            if result:
                logger.info(
                    "Value retrieved from Azure App Configuration via CLI",
                    extra={"key": key, "integration_name": self.integration_name},
                )
            return result

    def _get_flag(
        self,
        feature_name: str,
        label: Optional[str] = None,
        prefer_cli: bool = True,
        timeout: int = 60,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a feature flag from Azure App Configuration.

        Args:
            feature_name: The feature flag name
            label: Optional label filter
            prefer_cli: If True, try CLI first
            timeout: Command timeout in seconds

        Returns:
            Feature flag data (enabled status, conditions, etc.) or None
        """
        # Feature flags are stored with .appconfig.featureflag/ prefix
        feature_key = f".appconfig.featureflag/{feature_name}"
        value = self._get_value(feature_key, label, prefer_cli, timeout)

        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    def _list_keys(
        self,
        key_filter: Optional[str] = None,
        label: Optional[str] = None,
        prefer_cli: bool = True,
        timeout: int = 60,
    ) -> List[str]:
        """
        List all key names in the App Configuration store.

        Args:
            key_filter: Optional key filter pattern (e.g., "app:*")
            label: Optional label filter
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            List of key names, or empty list if failed
        """
        available, error = self.ensure_available()
        if not available:
            return []

        if prefer_cli:
            # Try Azure CLI first
            result = self._list_keys_via_cli(key_filter, label, timeout)
            if result:
                return result

            # Fall back to API with CLI token
            result = self._list_keys_via_api(key_filter, label, use_cli_token=True)
            if result:
                return result

            # Fall back to API with client credentials
            return self._list_keys_via_api(key_filter, label, use_cli_token=False)
        else:
            # Try API with client credentials first
            result = self._list_keys_via_api(key_filter, label, use_cli_token=False)
            if result:
                return result

            # Fall back to API with CLI token
            result = self._list_keys_via_api(key_filter, label, use_cli_token=True)
            if result:
                return result

            # Fall back to Azure CLI
            return self._list_keys_via_cli(key_filter, label, timeout)

    def _list_flags(
        self, label: Optional[str] = None, prefer_cli: bool = True, timeout: int = 60
    ) -> List[str]:
        """
        List all feature flag names in the App Configuration store.

        Args:
            label: Optional label filter
            prefer_cli: If True, try CLI first
            timeout: Command timeout in seconds

        Returns:
            List of feature flag names (without .appconfig.featureflag/ prefix)
        """
        keys = self._list_keys(
            key_filter=".appconfig.featureflag/*",
            label=label,
            prefer_cli=prefer_cli,
            timeout=timeout,
        )
        # Remove the .appconfig.featureflag/ prefix
        return [key.replace(".appconfig.featureflag/", "") for key in keys]

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
                    "https://azconfig.io",
                ],
                timeout=30,
            )

            if result.returncode == 0 and result.stdout:
                token_data = json.loads(result.stdout)
                token = token_data.get("accessToken")
                if token:
                    logger.debug(
                        "Successfully obtained Azure access token via CLI",
                        extra={"integration_name": self.integration_name},
                    )
                return token

            logger.warning(
                "Failed to get Azure access token via CLI",
                extra={"integration_name": self.integration_name},
            )
            return None

        except Exception as e:
            logger.warning(
                "Error getting Azure access token via CLI",
                extra={
                    "error_type": type(e).__name__,
                    "integration_name": self.integration_name,
                },
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
            logger.debug(
                "Authenticating to Azure App Configuration using client credentials",
                extra={"integration_name": self.integration_name},
            )
            token_url = (
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            )
            data = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://azconfig.io/.default",
                }
            ).encode("utf-8")

            req = urllib.request.Request(token_url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            with urllib.request.urlopen(req, timeout=10) as response:
                token_response = json.loads(response.read().decode("utf-8"))
                token = token_response.get("access_token")
                if token:
                    logger.info(
                        "Successfully authenticated to Azure App Configuration using client credentials",
                        extra={"integration_name": self.integration_name},
                    )
                return token
        except Exception as e:
            logger.warning(
                "Client credentials authentication to Azure App Configuration failed",
                extra={
                    "error_type": type(e).__name__,
                    "integration_name": self.integration_name,
                },
            )
            return None

    # Configuration key-value methods

    def _get_value_via_cli(
        self, key: str, label: Optional[str] = None, timeout: int = 60
    ) -> Optional[str]:
        """
        Retrieve a value from Azure App Configuration using the Azure CLI.

        Args:
            key: The key name
            label: Optional label filter
            timeout: Command timeout in seconds

        Returns:
            The configuration value if found, None otherwise
        """
        try:
            # Build command args
            if self.connection_string:
                # Use connection string authentication
                args = [
                    "appconfig",
                    "kv",
                    "show",
                    "--connection-string",
                    self.connection_string,
                    "--key",
                    key,
                ]
            else:
                # Extract appconfig name from endpoint
                appconfig_name = (
                    self.appconfig_endpoint.replace("https://", "")
                    .replace(".azconfig.io", "")
                    .rstrip("/")
                )
                args = [
                    "appconfig",
                    "kv",
                    "show",
                    "--name",
                    appconfig_name,
                    "--key",
                    key,
                ]

            if label:
                args.extend(["--label", label])

            args.extend(["--query", "value", "-o", "tsv"])

            result = self._run_integration(
                args=args,
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()

            return None

        except Exception:
            return None

    def _get_value_via_api(
        self, key: str, label: Optional[str] = None, use_cli_token: bool = False
    ) -> Optional[str]:
        """
        Retrieve a value from Azure App Configuration using HTTPS API call.

        Args:
            key: The key name
            label: Optional label filter
            use_cli_token: If True, get token via Azure CLI; if False, use client credentials

        Returns:
            The configuration value if found, None otherwise
        """
        try:
            # Get access token
            if use_cli_token:
                access_token = self._get_access_token_via_cli()
            else:
                access_token = self._get_access_token_via_api()

            if not access_token:
                return None

            # Build API URL
            encoded_key = urllib.parse.quote(key, safe="")
            kv_url = f"{self.appconfig_endpoint}kv/{encoded_key}?api-version=1.0"
            if label:
                kv_url += f"&label={urllib.parse.quote(label, safe='')}"

            req = urllib.request.Request(kv_url)
            req.add_header("Authorization", f"Bearer {access_token}")

            with urllib.request.urlopen(req, timeout=10) as response:
                kv_data = json.loads(response.read().decode("utf-8"))
                return kv_data.get("value")
        except Exception:
            return None

    def _list_keys_via_cli(
        self,
        key_filter: Optional[str] = None,
        label: Optional[str] = None,
        timeout: int = 60,
    ) -> List[str]:
        """
        List all key names in the App Configuration store using Azure CLI.

        Args:
            key_filter: Optional key filter pattern
            label: Optional label filter
            timeout: Command timeout in seconds

        Returns:
            List of key names, or empty list if failed
        """
        try:
            # Build command args
            if self.connection_string:
                args = [
                    "appconfig",
                    "kv",
                    "list",
                    "--connection-string",
                    self.connection_string,
                ]
            else:
                appconfig_name = (
                    self.appconfig_endpoint.replace("https://", "")
                    .replace(".azconfig.io", "")
                    .rstrip("/")
                )
                args = [
                    "appconfig",
                    "kv",
                    "list",
                    "--name",
                    appconfig_name,
                ]

            if key_filter:
                args.extend(["--key", key_filter])
            if label:
                args.extend(["--label", label])

            args.extend(["--query", "[].key", "-o", "json"])

            result = self._run_integration(
                args=args,
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)

            return []

        except Exception:
            return []

    def _list_keys_via_api(
        self,
        key_filter: Optional[str] = None,
        label: Optional[str] = None,
        use_cli_token: bool = False,
    ) -> List[str]:
        """
        List all key names in the App Configuration store using HTTPS API call.

        Args:
            key_filter: Optional key filter pattern
            label: Optional label filter
            use_cli_token: If True, get token via Azure CLI; if False, use client credentials

        Returns:
            List of key names, or empty list if failed
        """
        try:
            # Get access token
            if use_cli_token:
                access_token = self._get_access_token_via_cli()
            else:
                access_token = self._get_access_token_via_api()

            if not access_token:
                return []

            # Build API URL
            kv_url = f"{self.appconfig_endpoint}kv?api-version=1.0"
            if key_filter:
                kv_url += f"&key={urllib.parse.quote(key_filter, safe='')}"
            if label:
                kv_url += f"&label={urllib.parse.quote(label, safe='')}"

            req = urllib.request.Request(kv_url)
            req.add_header("Authorization", f"Bearer {access_token}")

            with urllib.request.urlopen(req, timeout=10) as response:
                kv_data = json.loads(response.read().decode("utf-8"))
                items = kv_data.get("items", [])
                return [item.get("key") for item in items if item.get("key")]
        except Exception:
            return []
