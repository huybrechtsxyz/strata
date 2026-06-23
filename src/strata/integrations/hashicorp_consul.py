"""HashiCorp Consul integration for key-value store and service discovery."""

import base64
import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.capabilities import IKVStore, IVariableStore
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult

logger = get_logger(__name__)


class ConsulIntegration(StoreIntegration):
    """
    HashiCorp Consul integration for key-value store.

    Implements singleton pattern per consul server address - multiple instances
    possible for different Consul servers.

    Supports multiple access methods:
    - Consul CLI (consul)
    - Direct HTTP API access
    - ACL token authentication
    - Consul Enterprise namespaces
    """

    # Command executable name
    COMMAND = "consul"

    # Declare supported capabilities
    CAPABILITIES = [IVariableStore, IKVStore]

    # Singleton instance keying based on endpoint

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on Consul server address.

        Creates separate singleton instances per Consul server.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Normalized Consul address or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        # Get consul address from config
        consul_addr = ""
        if config.endpoints and config.endpoints.address:
            consul_addr = config.endpoints.address
            # Normalize address
            if consul_addr.endswith("/"):
                consul_addr = consul_addr.rstrip("/")

        return consul_addr or config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize HashiCorp Consul integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Get Consul address from config
        self.consul_addr = "http://127.0.0.1:8500"  # default
        if self.config.endpoints and self.config.endpoints.address:
            self.consul_addr = self._resolve_env_vars(self.config.endpoints.address)
            if self.consul_addr.endswith("/"):
                self.consul_addr = self.consul_addr.rstrip("/")

        # Get authentication configuration from environment variables.
        # When authentication.method == "api_key", api_key.api_key holds
        # the env-var name for the ACL token.
        self.consul_token = self._get_env_var(self._get_token_var_name())
        self.consul_namespace = self._get_env_var("CONSUL_NAMESPACE")  # no model equivalent

        logger.debug(
            "HashiCorp Consul integration initialized",
            name=self.integration_name,
            address=self.consul_addr,
            has_token=bool(self.consul_token),
        )

    # Auth helpers

    def _get_token_var_name(self) -> str:
        """
        Return the env-var name for the Consul ACL token.

        When ``authentication.method == "api_key"`` the ``api_key.api_key``
        field holds the env-var name.  Defaults to ``CONSUL_HTTP_TOKEN``.
        """
        auth = self.config.authentication
        if auth and auth.method == "api_key" and auth.api_key:
            return auth.api_key.api_key or "CONSUL_HTTP_TOKEN"
        return "CONSUL_HTTP_TOKEN"

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve consul version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from consul output.

        Args:
            version_output: Raw output (e.g., "Consul v1.16.0")

        Returns:
            Version string (e.g., "1.16.0")
        """
        # Extract version number from "Consul vX.Y.Z"
        match = re.search(r"v(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for hashicorp_consul."""
        return {
            "name": "hashicorp_consul",
            "command": "consul",
            "install_url": "https://developer.hashicorp.com/consul/install",
            "env_vars": [
                {"name": "CONSUL_HTTP_TOKEN", "purpose": "Consul ACL token for authentication", "required": True},
                {
                    "name": "CONSUL_HTTP_ADDR",
                    "purpose": "Consul server HTTP address (derived from endpoints.address if set)",
                    "required": False,
                },
                {"name": "CONSUL_NAMESPACE", "purpose": "Consul namespace (Consul Enterprise only)", "required": False},
            ],
            "auth_methods": [
                {
                    "method": "ACL token",
                    "description": "Set CONSUL_HTTP_TOKEN. Use a policy-scoped token, not the root token.",
                },
            ],
            "yaml_example": "type: hashicorp_consul\nspec:\n  endpoints:\n    address: http://consul.example.com:8500",
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
                "HashiCorp Consul CLI not found",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from https://www.consul.io/downloads",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "HashiCorp Consul version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        # Check Consul address
        if not self.consul_addr:
            self._info = f"{self.integration_name} address not configured."
            logger.warning(
                "HashiCorp Consul address not configured",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} address not configured. "
                "Set endpoints.address in config or CONSUL_HTTP_ADDR environment variable "
                "(e.g., http://127.0.0.1:8500)",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "HashiCorp Consul is available and configured",
            name=self.integration_name,
            version=self.get_version(),
            address=self.consul_addr,
        )
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["consul_addr"] = self.consul_addr
        info["has_token"] = bool(self.consul_token)
        info["namespace"] = self.consul_namespace
        return info

    # Unified Store Interface Implementation (IVariableStore)

    def get_variable(self, key: str, **kwargs) -> Optional[str]:
        """
        Get a variable value from Consul KV store.

        Implements IVariableStore interface.

        Args:
            key: The key path in KV store
            **kwargs: Additional arguments (prefer_cli, timeout)

        Returns:
            The value if found, None otherwise
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self.get_keyvalue(key, prefer_cli=prefer_cli, timeout=timeout)

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available variable keys at a given prefix.

        Implements IVariableStore interface.

        Args:
            prefix: The key prefix to list (e.g., "config/myapp/")
            **kwargs: Additional arguments (prefer_cli, timeout)

        Returns:
            List of variable keys, empty list if not supported/failed
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self.list_keys(prefix, prefer_cli=prefer_cli, timeout=timeout)

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """
        Write a key to Consul KV store (create-if-not-exists semantics).

        Implements IVariableStore interface.  Never overwrites an existing key —
        if the key already exists the method returns True without writing.

        Args:
            key: KV key path (e.g., "config/myapp/log-level")
            value: Value to store (will be coerced to str)
            **kwargs: prefer_cli, timeout

        Returns:
            True if the key exists (created now or already present), False on failure
        """
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout: int = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot write variable to HashiCorp Consul", name=self.integration_name, error=error)
            return False

        # Check existence first — never overwrite
        existing = self.get_variable(key, prefer_cli=prefer_cli, timeout=timeout)
        if existing is not None:
            logger.info(
                "Variable already exists in HashiCorp Consul — skipping write", name=self.integration_name, key=key
            )
            return True

        logger.debug("Writing variable to HashiCorp Consul", name=self.integration_name, key=key)

        ok = self._put_keyvalue(key, str(value), prefer_cli=prefer_cli, timeout=timeout)
        if ok:
            logger.info("Variable written to HashiCorp Consul", name=self.integration_name, key=key)
        else:
            logger.warning("Failed to write variable to HashiCorp Consul", name=self.integration_name, key=key)
        return ok

    def _put_keyvalue(self, key: str, value: str, prefer_cli: bool = True, timeout: int = 60) -> bool:
        """Write a key-value to Consul KV store."""
        if prefer_cli:
            try:
                result = self._run_integration_with_env(args=["kv", "put", key, value], timeout=timeout)
                if result.returncode == 0:
                    return True
            except Exception:
                pass
        # API fallback
        try:
            kv_url = f"{self.consul_addr}/v1/kv/{key}"
            if self.consul_namespace:
                kv_url += f"?ns={self.consul_namespace}"
            body = value.encode("utf-8")
            req = urllib.request.Request(kv_url, data=body, method="PUT")
            if self.consul_token:
                req.add_header("X-Consul-Token", self.consul_token)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status == 200
        except Exception:
            return False

    # KV store methods (IKVStore implementation)

    def get_keyvalue(
        self,
        key: str,
        prefer_cli: bool = True,
        timeout: int = 60,
    ) -> Optional[str]:
        """
        Retrieve a key-value from Consul KV store.

        Implements IKVStore interface.

        Args:
            key: The key path in KV store (e.g., "config/myapp/database")
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            The value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot retrieve KV from HashiCorp Consul",
                error=error,
                name=self.integration_name,
            )
            return None

        logger.debug(
            "Retrieving key from HashiCorp Consul",
            key=key,
            prefer_cli=prefer_cli,
            name=self.integration_name,
        )

        if prefer_cli:
            # Try Consul CLI first
            result = self._get_kv_via_cli(key, timeout)
            if result is not None:
                logger.info(
                    "Key retrieved from HashiCorp Consul via CLI",
                    key=key,
                    name=self.integration_name,
                )
                return result

            # Fall back to API
            result = self._get_kv_via_api(key)
            if result is not None:
                logger.info(
                    "Key retrieved from HashiCorp Consul via API",
                    key=key,
                    name=self.integration_name,
                )
            return result
        else:
            # Try API first
            result = self._get_kv_via_api(key)
            if result is not None:
                logger.info(
                    "Key retrieved from HashiCorp Consul via API",
                    key=key,
                    name=self.integration_name,
                )
                return result

            # Fall back to Consul CLI
            result = self._get_kv_via_cli(key, timeout)
            if result is not None:
                logger.info(
                    "Key retrieved from HashiCorp Consul via CLI",
                    key=key,
                    name=self.integration_name,
                )
            return result

    def list_keys(self, prefix: str, prefer_cli: bool = True, timeout: int = 60) -> List[str]:
        """
        List keys at a given prefix.

        Implements IKVStore interface.

        Args:
            prefix: The key prefix to list (e.g., "config/myapp/")
            prefer_cli: If True, try CLI first; if False, try API first
            timeout: Command timeout in seconds

        Returns:
            List of key names, or empty list if failed
        """
        available, error = self.ensure_available()
        if not available:
            return []

        if prefer_cli:
            # Try Consul CLI first
            result = self._list_keys_via_cli(prefix, timeout)
            if result is not None:
                return result

            # Fall back to API
            return self._list_keys_via_api(prefix) or []
        else:
            # Try API first
            result = self._list_keys_via_api(prefix)
            if result is not None:
                return result

            # Fall back to Consul CLI
            return self._list_keys_via_cli(prefix, timeout) or []

    # KV retrieval implementations

    def _get_kv_via_cli(self, key: str, timeout: int = 60) -> Optional[str]:
        """
        Retrieve a key-value from Consul KV store using the Consul CLI.

        Args:
            key: The key path in KV store (e.g., "config/myapp/database")
            timeout: Command timeout in seconds

        Returns:
            The value if found, None otherwise
        """
        try:
            result = self._run_integration_with_env(
                args=["kv", "get", key],
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()

            return None

        except Exception:
            return None

    def _get_kv_via_api(self, key: str, decode: bool = True) -> Optional[str]:
        """
        Retrieve a key-value from Consul KV store using HTTPS API call.

        Args:
            key: The key path in KV store (e.g., "config/myapp/database")
            decode: If True, base64 decode the value; if False, return raw response

        Returns:
            The value if found, None otherwise
        """
        try:
            # Build KV URL
            kv_url = f"{self.consul_addr}/v1/kv/{key}"
            if self.consul_namespace:
                kv_url += f"?ns={self.consul_namespace}"

            req = urllib.request.Request(kv_url)
            if self.consul_token:
                req.add_header("X-Consul-Token", self.consul_token)

            with urllib.request.urlopen(req, timeout=10) as response:
                kv_response = json.loads(response.read().decode("utf-8"))

                if not kv_response or len(kv_response) == 0:
                    return None

                # Consul returns array with single item for exact key match
                item = kv_response[0]
                value = item.get("Value")

                if value and decode:
                    # Consul stores values base64 encoded
                    return base64.b64decode(value).decode("utf-8")

                return value
        except Exception:
            return None

    def _list_keys_via_cli(self, prefix: str, timeout: int = 60) -> Optional[List[str]]:
        """
        List keys at a given prefix using Consul CLI.

        Args:
            prefix: The key prefix to list (e.g., "config/myapp/")
            timeout: Command timeout in seconds

        Returns:
            List of key names or None if failed
        """
        try:
            result = self._run_integration_with_env(
                args=["kv", "get", "-keys", prefix],
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                return [k.strip() for k in result.stdout.strip().split("\n") if k.strip()]

            return None

        except Exception:
            return None

    def _list_keys_via_api(self, prefix: str) -> Optional[List[str]]:
        """
        List keys at a given prefix using API.

        Args:
            prefix: The key prefix to list (e.g., "config/myapp/")

        Returns:
            List of key names or None if failed
        """
        try:
            list_url = f"{self.consul_addr}/v1/kv/{prefix}?keys"
            if self.consul_namespace:
                list_url += f"&ns={self.consul_namespace}"

            req = urllib.request.Request(list_url)
            if self.consul_token:
                req.add_header("X-Consul-Token", self.consul_token)

            with urllib.request.urlopen(req, timeout=10) as response:
                keys = json.loads(response.read().decode("utf-8"))
                return keys if keys else []
        except Exception:
            return None

    # Command execution method override to inject environment variables

    def _run_integration_with_env(
        self,
        args: List[str],
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """
        Run integration command with Consul environment variables injected.

        This method ensures Consul CLI commands have the proper environment
        variables set for address, token, and namespace.

        Args:
            args: Command arguments (without command name)
            timeout: Command timeout in seconds

        Returns:
            Dict with returncode, stdout, stderr
        """
        # Inject Consul environment variables
        env = {**os.environ}
        if self.consul_addr:
            env["CONSUL_HTTP_ADDR"] = self.consul_addr
        if self.consul_token:
            env["CONSUL_HTTP_TOKEN"] = self.consul_token
        if self.consul_namespace:
            env["CONSUL_NAMESPACE"] = self.consul_namespace

        # Store original environment
        original_env = os.environ.copy()
        os.environ.update(env)

        try:
            result = self._run_integration(args=args, timeout=timeout if timeout is not None else 300)
            return result
        finally:
            # Restore original environment
            os.environ.clear()
            os.environ.update(original_env)
