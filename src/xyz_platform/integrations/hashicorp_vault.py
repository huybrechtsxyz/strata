"""HashiCorp Vault integration for secrets management and key-value storage."""

import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.integrations.capabilities import (
    IKVStore,
    ISecretStore,
    IVariableStore,
)
from xyz_platform.integrations.store_integration import StoreIntegration
from xyz_platform.logger import get_logger
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.utils.system import CommandResult

logger = get_logger(__name__)


class VaultIntegration(StoreIntegration):
    """
    HashiCorp Vault integration for secrets management.

    Implements singleton pattern per vault server address - multiple instances
    possible for different Vault servers.

    Supports multiple authentication methods:
    - Direct token (VAULT_TOKEN)
    - AppRole (VAULT_ROLE_ID + VAULT_SECRET_ID)
    - Kubernetes (VAULT_K8S_ROLE)
    """

    # Command executable name
    COMMAND = "vault"

    # Declare supported capabilities
    CAPABILITIES = [IVariableStore, ISecretStore, IKVStore]

    # Singleton instance keying based on endpoint

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on Vault server address.

        Creates separate singleton instances per Vault server.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Normalized Vault address or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        # Get vault address from config
        vault_addr = ""
        if config.endpoints and config.endpoints.address:
            vault_addr = config.endpoints.address
            # Normalize address
            if vault_addr and vault_addr.endswith("/"):
                vault_addr = vault_addr.rstrip("/")

        return vault_addr or config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize HashiCorp Vault integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Get Vault address from config
        self.vault_addr = ""
        if self.config.endpoints and self.config.endpoints.address:
            self.vault_addr = self._resolve_env_vars(self.config.endpoints.address)
            if self.vault_addr and self.vault_addr.endswith("/"):
                self.vault_addr = self.vault_addr.rstrip("/")

        # Get authentication configuration from environment variables.
        # Fields in api_key / oauth2 sub-models are env-var name references.
        #   method=api_key  → api_key.api_key  = env-var name for VAULT_TOKEN
        #   method=oauth2   → oauth2.client_id = env-var name for VAULT_ROLE_ID
        #                     oauth2.client_secret = env-var name for VAULT_SECRET_ID
        self.vault_token = self._get_env_var(self._get_auth_var_name("api_key", "VAULT_TOKEN"))
        self.vault_namespace = self._get_env_var("VAULT_NAMESPACE")  # no model equivalent

        # AppRole authentication
        self.vault_role_id = self._get_env_var(self._get_auth_var_name("client_id", "VAULT_ROLE_ID"))
        self.vault_secret_id = self._get_env_var(self._get_auth_var_name("client_secret", "VAULT_SECRET_ID"))

        # Kubernetes authentication (no model equivalent; keep defaults)
        self.vault_k8s_role = self._get_env_var("VAULT_K8S_ROLE")
        self.vault_k8s_jwt_path = self._get_env_var(
            "VAULT_K8S_JWT_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"
        )

        logger.debug(
            "HashiCorp Vault integration initialized",
            name=self.integration_name,
            address=self.vault_addr,
            auth_method=self._get_auth_method(),
        )

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve vault version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from vault output.

        Args:
            version_output: Raw output (e.g., "Vault v1.15.0")

        Returns:
            Version string (e.g., "1.15.0")
        """
        # Extract version number from "Vault vX.Y.Z"
        match = re.search(r"v(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for hashicorp_vault."""
        return {
            "name": "hashicorp_vault",
            "command": "vault",
            "install_url": "https://developer.hashicorp.com/vault/install",
            "env_vars": [
                {"name": "VAULT_TOKEN", "purpose": "Vault authentication token", "required": True},
                {"name": "VAULT_ADDR", "purpose": "Vault server address (derived from endpoints.address if set)", "required": False},
            ],
            "auth_methods": [
                {"method": "Token", "description": "Set VAULT_TOKEN. Most common method for automation."},
                {"method": "AppRole", "description": "Obtain token via vault write auth/approle/login; then set VAULT_TOKEN."},
            ],
            "yaml_example": "type: hashicorp_vault\nspec:\n  endpoints:\n    address: https://vault.example.com",
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available with proper configuration.

        Returns:
            Tuple of (success, error_message)
        """
        # Check integration availability
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning(
                "HashiCorp Vault CLI not found",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from https://www.vaultproject.io/downloads",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "HashiCorp Vault version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        # Check Vault address
        if not self.vault_addr:
            self._info = f"{self.integration_name} address not configured."
            logger.warning(
                "HashiCorp Vault address not configured",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} address not configured. "
                "Set endpoints.address in config or VAULT_ADDR environment variable "
                "(e.g., https://vault.example.com:8200)",
            )

        # Check authentication
        auth_method = self._get_auth_method()
        if not auth_method:
            self._info = f"{self.integration_name} has no authentication configured."
            logger.warning(
                "HashiCorp Vault authentication not configured",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} authentication not configured. Set one of: "
                "VAULT_TOKEN, VAULT_ROLE_ID+VAULT_SECRET_ID, or VAULT_K8S_ROLE",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available ({auth_method})"
        logger.debug(
            "HashiCorp Vault is available and configured",
            name=self.integration_name,
            version=self.get_version(),
            address=self.vault_addr,
            auth_method=auth_method,
        )
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["vault_addr"] = self.vault_addr
        info["auth_method"] = self._get_auth_method()
        info["namespace"] = self.vault_namespace
        return info

    # Unified Store Interface Implementation (ISecretStore)

    def get_secret(self, key: str, **kwargs) -> Optional[str]:
        """
        Get a secret value from HashiCorp Vault.

        Implements ISecretStore interface.

        Args:
            key: The path to the secret (e.g., "secret/myapp/config")
            **kwargs: Additional arguments (field, prefer_cli, timeout)

        Returns:
            The secret value if found, None otherwise
        """
        field = kwargs.get("field")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self._get_secretvalue(secret_path=key, field=field, prefer_cli=prefer_cli, timeout=timeout)

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available secret keys at a given path.

        Implements ISecretStore interface.

        Args:
            prefix: The path prefix to list secrets from (e.g., "secret/myapp")
            **kwargs: Additional arguments (prefer_cli, timeout)

        Returns:
            List of secret keys, empty list if not supported/failed
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self._list_secretkeys(path=prefix, prefer_cli=prefer_cli, timeout=timeout)

    # Auth helpers

    def _get_auth_var_name(self, field: str, default: str) -> str:
        """
        Return the env-var name for a Vault credential field.

        - ``method == "api_key"``  and *field* == ``"api_key"``  → ``api_key.api_key``
          (env-var holding the Vault token, e.g. ``"VAULT_TOKEN"``)
        - ``method == "oauth2"``   → ``oauth2.<field>``
          (env-var holding AppRole ``client_id`` / ``client_secret``)
        Falls back to *default* for backward compatibility and K8s fields
        that have no model equivalent.
        """
        auth = self.config.authentication
        if auth and auth.method == "api_key" and auth.api_key and field == "api_key":
            return auth.api_key.api_key or default
        if auth and auth.method == "oauth2" and auth.oauth2:
            val = getattr(auth.oauth2, field, None)
            if val:
                return val
        return default

    # Authentication methods

    def _get_auth_method(self) -> Optional[str]:
        """
        Determine the configured authentication method.

        Returns:
            String describing auth method or None if not configured
        """
        if self.vault_token:
            return "token"
        if self.vault_role_id and self.vault_secret_id:
            return "approle"
        if self.vault_k8s_role:
            return "kubernetes"
        return None

    def _get_token_via_approle(self) -> Optional[str]:
        """
        Authenticate to Vault using AppRole and get a token.

        Returns:
            Vault token string or None if failed
        """
        if not all([self.vault_addr, self.vault_role_id, self.vault_secret_id]):
            return None

        try:
            logger.debug(
                "Authenticating to HashiCorp Vault using AppRole",
                name=self.integration_name,
            )
            auth_url = f"{self.vault_addr}/v1/auth/approle/login"
            data = json.dumps({"role_id": self.vault_role_id, "secret_id": self.vault_secret_id}).encode("utf-8")

            req = urllib.request.Request(auth_url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if self.vault_namespace:
                req.add_header("X-Vault-Namespace", self.vault_namespace)

            with urllib.request.urlopen(req, timeout=10) as response:
                auth_response = json.loads(response.read().decode("utf-8"))
                token = auth_response.get("auth", {}).get("client_token")
                if token:
                    logger.info(
                        "Successfully authenticated to HashiCorp Vault using AppRole",
                        name=self.integration_name,
                    )
                return token
        except Exception as e:
            logger.warning(
                "AppRole authentication to HashiCorp Vault failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

    def _get_token_via_kubernetes(self) -> Optional[str]:
        """
        Authenticate to Vault using Kubernetes auth and get a token.

        Returns:
            Vault token string or None if failed
        """
        if not all([self.vault_addr, self.vault_k8s_role]):
            return None

        try:
            logger.debug(
                "Authenticating to HashiCorp Vault using Kubernetes auth",
                name=self.integration_name,
            )
            # Read Kubernetes service account JWT
            jwt_path = self.vault_k8s_jwt_path or ""
            if not jwt_path or not os.path.exists(jwt_path):
                logger.warning(
                    "Kubernetes JWT not found",
                    jwt_path=self.vault_k8s_jwt_path,
                    name=self.integration_name,
                )
                return None

            with open(jwt_path, "r") as f:
                jwt = f.read().strip()

            auth_url = f"{self.vault_addr}/v1/auth/kubernetes/login"
            data = json.dumps({"role": self.vault_k8s_role, "jwt": jwt}).encode("utf-8")

            req = urllib.request.Request(auth_url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if self.vault_namespace:
                req.add_header("X-Vault-Namespace", self.vault_namespace)

            with urllib.request.urlopen(req, timeout=10) as response:
                auth_response = json.loads(response.read().decode("utf-8"))
                token = auth_response.get("auth", {}).get("client_token")
                if token:
                    logger.info(
                        "Successfully authenticated to HashiCorp Vault using Kubernetes auth",
                        name=self.integration_name,
                    )
                return token
        except Exception as e:
            logger.warning(
                "Kubernetes authentication to HashiCorp Vault failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

    def _get_token(self) -> Optional[str]:
        """
        Get a Vault token using available authentication method.
        Tries in order: explicit token, AppRole, Kubernetes auth.

        Returns:
            Vault token or None if authentication fails
        """
        # Try explicit token first
        if self.vault_token:
            return self.vault_token

        # Try AppRole authentication
        token = self._get_token_via_approle()
        if token:
            return token

        # Try Kubernetes authentication
        return self._get_token_via_kubernetes()

    # Secret retrieval methods

    def _get_secretvalue(
        self,
        secret_path: str,
        field: Optional[str] = None,
        prefer_cli: bool = True,
        timeout: int = 60,
    ) -> Optional[str]:
        """
        Retrieve a secret from HashiCorp Vault.

        Args:
            secret_path: The path to the secret (e.g., "secret/myapp/config")
            field: Optional field name to extract from the secret
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            The secret value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot retrieve secret from HashiCorp Vault",
                error=error,
                name=self.integration_name,
            )
            return None

        logger.debug(
            "Retrieving secret from HashiCorp Vault",
            secret_path=secret_path,
            field=field,
            prefer_cli=prefer_cli,
            name=self.integration_name,
        )

        if prefer_cli:
            # Try Vault CLI first
            result = self._get_secret_via_cli(secret_path, field, timeout)
            if result is not None:
                logger.info(
                    "Secret retrieved from HashiCorp Vault via CLI",
                    secret_path=secret_path,
                    name=self.integration_name,
                )
                return result

            # Fall back to API
            result = self._get_secret_via_api(secret_path, field)
            if result is not None:
                logger.info(
                    "Secret retrieved from HashiCorp Vault via API",
                    secret_path=secret_path,
                    name=self.integration_name,
                )
            return result
        else:
            # Try API first
            result = self._get_secret_via_api(secret_path, field)
            if result is not None:
                logger.info(
                    "Secret retrieved from HashiCorp Vault via API",
                    secret_path=secret_path,
                    name=self.integration_name,
                )
                return result

            # Fall back to Vault CLI
            result = self._get_secret_via_cli(secret_path, field, timeout)
            if result is not None:
                logger.info(
                    "Secret retrieved from HashiCorp Vault via CLI",
                    secret_path=secret_path,
                    name=self.integration_name,
                )
            return result

    def _get_secret_via_cli(self, secret_path: str, field: Optional[str] = None, timeout: int = 60) -> Optional[str]:
        """
        Retrieve a secret from HashiCorp Vault using the Vault CLI.

        Args:
            secret_path: The path to the secret (e.g., "secret/data/myapp/config")
            field: Optional field name to extract from the secret
            timeout: Command timeout in seconds

        Returns:
            The secret value if found, None otherwise
        """
        try:
            args = ["kv", "get", "-format=json"]

            # Add field selector if specified
            if field:
                args.extend(["-field", field])

            args.append(secret_path)

            result = self._run_integration_with_env(
                args=args,
                timeout=timeout,
            )

            if result.returncode != 0:
                return None

            if field:
                # CLI returns just the field value
                return result.stdout.strip() if result.stdout else None
            else:
                # Parse JSON response
                secret_data = json.loads(result.stdout)
                return secret_data.get("data", {}).get("data", {})
        except Exception:
            return None

    def _get_secret_via_api(self, secret_path: str, field: Optional[str] = None) -> Optional[str]:
        """
        Retrieve a secret from HashiCorp Vault using HTTPS API call.

        Args:
            secret_path: The path to the secret (e.g., "secret/data/myapp/config")
            field: Optional field name to extract from the secret

        Returns:
            The secret value if found, None otherwise
        """
        try:
            # Get authentication token
            token = self._get_token()
            if not token:
                return None

            # Build secret URL
            # Handle both KV v1 and v2 paths
            if "/data/" not in secret_path and not secret_path.startswith("secret/data/"):
                # Assume KV v2 and insert /data/ if not present
                parts = secret_path.split("/", 1)
                if len(parts) == 2:
                    secret_path = f"{parts[0]}/data/{parts[1]}"

            secret_url = f"{self.vault_addr}/v1/{secret_path}"

            req = urllib.request.Request(secret_url)
            req.add_header("X-Vault-Token", token)
            if self.vault_namespace:
                req.add_header("X-Vault-Namespace", self.vault_namespace)

            with urllib.request.urlopen(req, timeout=10) as response:
                secret_response = json.loads(response.read().decode("utf-8"))
                secret_data = secret_response.get("data", {}).get("data", {})

                if field:
                    # Return specific field
                    return secret_data.get(field)
                else:
                    # Return all data as dict
                    return secret_data
        except Exception:
            return None

    # List secrets methods

    def _list_secretkeys(self, path: str, prefer_cli: bool = True, timeout: int = 60) -> List[str]:
        """
        List secrets at a given path.

        Args:
            path: The path to list (e.g., "secret/myapp")
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            List of secret names, or empty list if failed
        """
        available, error = self.ensure_available()
        if not available:
            return []

        if prefer_cli:
            # Try Vault CLI first
            result = self._list_secrets_via_cli(path, timeout)
            if result is not None:
                return result

            # Fall back to API
            return self._list_secrets_via_api(path) or []
        else:
            # Try API first
            result = self._list_secrets_via_api(path)
            if result is not None:
                return result

            # Fall back to Vault CLI
            return self._list_secrets_via_cli(path, timeout) or []

    def _list_secrets_via_cli(self, path: str, timeout: int = 60) -> Optional[List[str]]:
        """
        List secrets at a given path using Vault CLI.

        Args:
            path: The path to list (e.g., "secret/myapp")
            timeout: Command timeout in seconds

        Returns:
            List of secret names or None if failed
        """
        try:
            result = self._run_integration_with_env(
                args=["kv", "list", "-format=json", path],
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)

            return None

        except Exception:
            return None

    def _list_secrets_via_api(self, path: str) -> Optional[List[str]]:
        """
        List secrets at a given path using API.

        Args:
            path: The path to list (e.g., "secret/metadata/myapp")

        Returns:
            List of secret names or None if failed
        """
        try:
            token = self._get_token()
            if not token:
                return None

            # Ensure path has metadata for KV v2
            if "/metadata/" not in path:
                parts = path.split("/", 1)
                if len(parts) == 2:
                    path = f"{parts[0]}/metadata/{parts[1]}"

            list_url = f"{self.vault_addr}/v1/{path}?list=true"

            req = urllib.request.Request(list_url)
            req.add_header("X-Vault-Token", token)
            if self.vault_namespace:
                req.add_header("X-Vault-Namespace", self.vault_namespace)

            with urllib.request.urlopen(req, timeout=10) as response:
                list_response = json.loads(response.read().decode("utf-8"))
                return list_response.get("data", {}).get("keys", [])
        except Exception:
            return None

    # Command execution method override to inject environment variables

    def _run_integration_with_env(
        self,
        args: List[str],
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """
        Run integration command with Vault environment variables injected.

        This method ensures Vault CLI commands have the proper environment
        variables set for address, token, and namespace.

        Args:
            args: Command arguments (without command name)
            timeout: Command timeout in seconds

        Returns:
            Dict with returncode, stdout, stderr
        """
        # Inject Vault environment variables
        env = {**os.environ}
        if self.vault_addr:
            env["VAULT_ADDR"] = self.vault_addr
        if self.vault_token:
            env["VAULT_TOKEN"] = self.vault_token
        if self.vault_namespace:
            env["VAULT_NAMESPACE"] = self.vault_namespace

        # Store original environment
        original_env = os.environ.copy()
        os.environ.update(env)

        try:
            if timeout is None:
                timeout = 60
            result = self._run_integration(args=args, timeout=timeout)
            return result
        finally:
            # Restore original environment
            os.environ.clear()
            os.environ.update(original_env)
