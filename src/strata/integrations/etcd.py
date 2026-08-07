"""etcd integration for distributed key-value storage."""

import base64
import json
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.capabilities import IKVStore, IVariableStore
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class EtcdIntegration(StoreIntegration):
    """
    etcd integration for distributed key-value storage.

    etcd is a strongly consistent, distributed KV store used heavily in
    Kubernetes and other cloud-native platforms. Operates via the etcdctl
    CLI (preferred) or the v3 gRPC-gateway HTTP API (fallback).

    https://etcd.io
    """

    # Command executable name
    COMMAND = "etcdctl"

    # Declare supported capabilities
    CAPABILITIES = [IVariableStore, IKVStore]

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key based on etcd endpoint address."""
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"
        addr = ""
        if config.endpoints and config.endpoints.address:
            addr = config.endpoints.address.rstrip("/")
        return addr or config.name or "default"

    def __init__(self, config: IntegrationModel):
        """
        Initialize etcd integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Endpoint — config takes priority, then env vars, then localhost default
        self.etcd_endpoints = "http://127.0.0.1:2379"
        if self.config.endpoints and self.config.endpoints.address:
            self.etcd_endpoints = self._resolve_env_vars(self.config.endpoints.address).rstrip("/")
        else:
            env_endpoints = self._get_env_var("ETCD_ENDPOINTS") or self._get_env_var("ETCDCTL_ENDPOINTS")
            if env_endpoints:
                self.etcd_endpoints = env_endpoints

        # Auth: basic (username / password) — field names mirror the oauth2 model convention
        self.etcd_username = self._get_env_var(self._get_auth_var_name("client_id", "ETCD_USERNAME"))
        self.etcd_password = self._get_env_var(self._get_auth_var_name("client_secret", "ETCD_PASSWORD"))

        # TLS — no model equivalent; always from env vars
        self.etcd_cacert = self._get_env_var("ETCD_CA_FILE") or self._get_env_var("ETCDCTL_CACERT")
        self.etcd_cert = self._get_env_var("ETCD_CERT_FILE") or self._get_env_var("ETCDCTL_CERT")
        self.etcd_key_file = self._get_env_var("ETCD_KEY_FILE") or self._get_env_var("ETCDCTL_KEY")

        logger.debug(
            "etcd integration initialized",
            name=self.integration_name,
            endpoints=self.etcd_endpoints,
            has_auth=bool(self.etcd_username),
            has_tls=bool(self.etcd_cacert),
        )

    # Auth helpers

    def _get_auth_var_name(self, field: str, default: str) -> str:
        """Return env-var name for a credential field (uses oauth2 sub-model convention)."""
        auth = self.config.authentication
        if auth and auth.method == "oauth2" and auth.oauth2:
            val = getattr(auth.oauth2, field, None)
            if val:
                return val
        return default

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve etcdctl version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from etcdctl output.

        Args:
            version_output: Raw output (e.g., "etcdctl version: 3.5.9")

        Returns:
            Version string (e.g., "3.5.9")
        """
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return match.group(1) if match else version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for etcd."""
        return {
            "name": "etcd",
            "command": "etcdctl",
            "install_url": "https://etcd.io/docs/latest/install/",
            "env_vars": [
                {
                    "name": "ETCD_ENDPOINTS",
                    "purpose": "Comma-separated etcd endpoint URLs",
                    "required": False,
                },
                {
                    "name": "ETCD_USERNAME",
                    "purpose": "Username for basic authentication",
                    "required": False,
                },
                {
                    "name": "ETCD_PASSWORD",
                    "purpose": "Password for basic authentication",
                    "required": False,
                },
                {
                    "name": "ETCD_CA_FILE",
                    "purpose": "CA certificate file path for TLS",
                    "required": False,
                },
                {
                    "name": "ETCD_CERT_FILE",
                    "purpose": "Client certificate file for mTLS",
                    "required": False,
                },
                {
                    "name": "ETCD_KEY_FILE",
                    "purpose": "Client key file for mTLS",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "Basic auth",
                    "description": "Set ETCD_USERNAME + ETCD_PASSWORD.",
                },
                {
                    "method": "mTLS",
                    "description": "Set ETCD_CA_FILE + ETCD_CERT_FILE + ETCD_KEY_FILE.",
                },
                {
                    "method": "Anonymous",
                    "description": "No auth — development / trusted network only.",
                },
            ],
            "yaml_example": ("type: etcd\nspec:\n  endpoints:\n    address: http://etcd.example.com:2379"),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure etcd CLI is available.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning("etcd CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI (etcdctl) is not installed or not in PATH. "
                "Install from: https://etcd.io/docs/latest/install/",
            )

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "etcd version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "etcd is available",
            name=self.integration_name,
            version=self.get_version(),
            endpoints=self.etcd_endpoints,
        )
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """Return integration information including configuration status."""
        info = super().get_info()
        info["endpoints"] = self.etcd_endpoints
        info["has_auth"] = bool(self.etcd_username)
        info["has_tls"] = bool(self.etcd_cacert)
        return info

    # Unified Store Interface Implementation (IVariableStore)

    def get_variable(self, key: str, **kwargs) -> Optional[Any]:
        """Get a variable value from etcd (delegates to get_keyvalue)."""
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self.get_keyvalue(key, prefer_cli=prefer_cli, timeout=timeout)

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """Set a variable value in etcd (delegates to put_keyvalue)."""
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self.put_keyvalue(key, str(value), prefer_cli=prefer_cli, timeout=timeout)

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """List variable keys in etcd (delegates to list_keys)."""
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)
        return self.list_keys(prefix, prefer_cli=prefer_cli, timeout=timeout)

    # KV store methods (IKVStore implementation)

    def get_keyvalue(self, key: str, prefer_cli: bool = True, timeout: int = 60) -> Optional[str]:
        """
        Retrieve a key-value from etcd.

        Implements IKVStore interface.

        Args:
            key: The key (e.g., "/config/myapp/database")
            prefer_cli: Try CLI first; fall back to HTTP API
            timeout: Command timeout in seconds

        Returns:
            Value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot retrieve key from etcd", error=error, name=self.integration_name)
            return None

        logger.debug("Retrieving key from etcd", key=key, name=self.integration_name)

        if prefer_cli:
            result = self._get_kv_via_cli(key, timeout)
            if result is not None:
                logger.info("Key retrieved from etcd via CLI", key=key, name=self.integration_name)
                return result
            return self._get_kv_via_api(key)
        else:
            result = self._get_kv_via_api(key)
            if result is not None:
                logger.info("Key retrieved from etcd via API", key=key, name=self.integration_name)
                return result
            return self._get_kv_via_cli(key, timeout)

    def put_keyvalue(self, key: str, value: str, prefer_cli: bool = True, timeout: int = 60) -> bool:
        """
        Write a key-value to etcd.

        Implements IKVStore interface.

        Args:
            key: The key
            value: The value
            prefer_cli: Try CLI first; fall back to HTTP API
            timeout: Command timeout in seconds

        Returns:
            True if successful, False otherwise
        """
        available, error = self.ensure_available()
        if not available:
            return False

        if prefer_cli:
            return self._put_kv_via_cli(key, value, timeout)
        else:
            return self._put_kv_via_api(key, value)

    def list_keys(self, prefix: str, prefer_cli: bool = True, timeout: int = 60) -> List[str]:
        """
        List keys at a given prefix in etcd.

        Implements IKVStore interface.

        Args:
            prefix: Key prefix to list
            prefer_cli: Try CLI first; fall back to HTTP API
            timeout: Command timeout in seconds

        Returns:
            List of matching keys, or empty list if failed
        """
        available, _ = self.ensure_available()
        if not available:
            return []

        if prefer_cli:
            result = self._list_keys_via_cli(prefix, timeout)
            if result is not None:
                return result
            return self._list_keys_via_api(prefix) or []
        else:
            result = self._list_keys_via_api(prefix)
            if result is not None:
                return result
            return self._list_keys_via_cli(prefix, timeout) or []

    # CLI helpers

    def _base_args(self) -> List[str]:
        """Build base etcdctl args with endpoint, auth, and TLS flags."""
        args = [f"--endpoints={self.etcd_endpoints}"]
        if self.etcd_username and self.etcd_password:
            args.append(f"--user={self.etcd_username}:{self.etcd_password}")
        if self.etcd_cacert:
            args.append(f"--cacert={self.etcd_cacert}")
        if self.etcd_cert:
            args.append(f"--cert={self.etcd_cert}")
        if self.etcd_key_file:
            args.append(f"--key={self.etcd_key_file}")
        return args

    def _get_kv_via_cli(self, key: str, timeout: int = 60) -> Optional[str]:
        try:
            args = self._base_args() + ["get", key, "--print-value-only"]
            result = self._run_integration(args=args, timeout=timeout)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return None
        except Exception:
            return None

    def _put_kv_via_cli(self, key: str, value: str, timeout: int = 60) -> bool:
        try:
            args = self._base_args() + ["put", key, value]
            result = self._run_integration(args=args, timeout=timeout)
            return result.returncode == 0
        except Exception:
            return False

    def _list_keys_via_cli(self, prefix: str, timeout: int = 60) -> Optional[List[str]]:
        try:
            base = prefix if prefix else "/"
            args = self._base_args() + ["get", base, "--prefix", "--keys-only"]
            result = self._run_integration(args=args, timeout=timeout)
            if result.returncode == 0:
                return [k for k in result.stdout.splitlines() if k.strip()]
            return None
        except Exception:
            return None

    # HTTP API helpers (etcd v3 gRPC-gateway — keys and values are base64-encoded)

    def _api_url(self, path: str) -> str:
        return f"{self.etcd_endpoints}{path}"

    def _api_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.etcd_username and self.etcd_password:
            creds = base64.b64encode(f"{self.etcd_username}:{self.etcd_password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def _get_kv_via_api(self, key: str) -> Optional[str]:
        try:
            key_b64 = base64.b64encode(key.encode()).decode()
            payload = json.dumps({"key": key_b64}).encode("utf-8")
            req = urllib.request.Request(self._api_url("/v3/kv/range"), data=payload, method="POST")
            for k, v in self._api_headers().items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                kvs = data.get("kvs", [])
                if kvs:
                    return base64.b64decode(kvs[0]["value"]).decode("utf-8")
            return None
        except Exception as e:
            logger.debug(
                "etcd API get failed",
                key=key,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

    def _put_kv_via_api(self, key: str, value: str) -> bool:
        try:
            key_b64 = base64.b64encode(key.encode()).decode()
            val_b64 = base64.b64encode(value.encode()).decode()
            payload = json.dumps({"key": key_b64, "value": val_b64}).encode("utf-8")
            req = urllib.request.Request(self._api_url("/v3/kv/put"), data=payload, method="POST")
            for k, v in self._api_headers().items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(
                "etcd API put failed",
                key=key,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return False

    def _list_keys_via_api(self, prefix: str) -> Optional[List[str]]:
        """
        List keys by prefix via the gRPC-gateway.

        etcd prefix range: range_end = prefix bytes with last byte incremented.
        """
        try:
            prefix_bytes = prefix.encode() if prefix else b"\x00"
            range_end_bytes = bytes([*prefix_bytes[:-1], prefix_bytes[-1] + 1]) if prefix else b"\xff"
            key_b64 = base64.b64encode(prefix_bytes).decode()
            range_end_b64 = base64.b64encode(range_end_bytes).decode()
            payload = json.dumps({"key": key_b64, "range_end": range_end_b64, "keys_only": True}).encode("utf-8")
            req = urllib.request.Request(self._api_url("/v3/kv/range"), data=payload, method="POST")
            for k, v in self._api_headers().items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [base64.b64decode(kv["key"]).decode("utf-8") for kv in data.get("kvs", [])]
        except Exception as e:
            logger.debug(
                "etcd API list failed",
                prefix=prefix,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None
