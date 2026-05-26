"""Infisical integration for secrets management (open-source, MPL-2.0)."""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.capabilities import ISecretStore, IVariableStore
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class InfisicalIntegration(StoreIntegration):
    """
    Infisical integration for secrets management.

    Infisical is an open-source secrets manager (MPL-2.0), compatible with
    self-hosted and Infisical Cloud deployments.

    Supports two authentication methods:
    - Direct service token (INFISICAL_TOKEN)
    - Universal auth / machine identity (INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET)

    https://infisical.com
    """

    # Command executable name
    COMMAND = "infisical"

    # Declare supported capabilities
    CAPABILITIES = [ISecretStore, IVariableStore]

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key based on Infisical server address."""
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"
        addr = ""
        if config.endpoints and config.endpoints.address:
            addr = config.endpoints.address.rstrip("/")
        return addr or config.name or "default"

    def __init__(self, config: IntegrationModel):
        """
        Initialize Infisical integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Server address
        self.infisical_addr = "https://app.infisical.com"
        if self.config.endpoints and self.config.endpoints.address:
            self.infisical_addr = self._resolve_env_vars(self.config.endpoints.address).rstrip("/")

        # Auth: service token (api_key) or universal auth (oauth2 client_id / client_secret)
        self.infisical_token = self._get_env_var(self._get_token_var_name())
        self.client_id = self._get_env_var(self._get_auth_var_name("client_id", "INFISICAL_CLIENT_ID"))
        self.client_secret = self._get_env_var(self._get_auth_var_name("client_secret", "INFISICAL_CLIENT_SECRET"))

        # Workspace / project targeting
        self.project_id = self._get_env_var("INFISICAL_PROJECT_ID")
        self.environment = self._get_env_var("INFISICAL_ENVIRONMENT", "prod")

        # Cached access token from universal auth login
        self._access_token: Optional[str] = None

        logger.debug(
            "Infisical integration initialized",
            name=self.integration_name,
            address=self.infisical_addr,
            auth_method=self._get_auth_method(),
        )

    # Auth helpers

    def _get_token_var_name(self) -> str:
        """Return the env-var name for the Infisical service token."""
        auth = self.config.authentication
        if auth and auth.method == "api_key" and auth.api_key:
            return auth.api_key.api_key or "INFISICAL_TOKEN"
        return "INFISICAL_TOKEN"

    def _get_auth_var_name(self, field: str, default: str) -> str:
        """Return the env-var name for a universal auth credential field."""
        auth = self.config.authentication
        if auth and auth.method == "oauth2" and auth.oauth2:
            val = getattr(auth.oauth2, field, None)
            if val:
                return val
        return default

    def _get_auth_method(self) -> Optional[str]:
        """Return the active authentication method name, or None if unconfigured."""
        if self.infisical_token:
            return "token"
        if self.client_id and self.client_secret:
            return "universal-auth"
        return None

    def _get_access_token(self) -> Optional[str]:
        """Return a valid bearer token (service token or cached universal-auth token)."""
        if self.infisical_token:
            return self.infisical_token
        if self._access_token:
            return self._access_token
        if self.client_id and self.client_secret:
            self._access_token = self._login_via_universal_auth()
        return self._access_token

    def _login_via_universal_auth(self) -> Optional[str]:
        """Authenticate via universal auth and return the access token."""
        try:
            url = f"{self.infisical_addr}/api/v1/auth/universal-auth/login"
            payload = json.dumps({"clientId": self.client_id, "clientSecret": self.client_secret}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                token = data.get("accessToken")
                if token:
                    logger.info(
                        "Authenticated to Infisical via universal auth",
                        name=self.integration_name,
                    )
                return token
        except Exception as e:
            logger.warning(
                "Infisical universal auth login failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve infisical version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from infisical output.

        Args:
            version_output: Raw output (e.g., "infisical v0.28.0")

        Returns:
            Version string (e.g., "0.28.0")
        """
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return match.group(1) if match else version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for infisical."""
        return {
            "name": "infisical",
            "command": "infisical",
            "install_url": "https://infisical.com/docs/cli/overview",
            "env_vars": [
                {
                    "name": "INFISICAL_TOKEN",
                    "purpose": "Service token for direct authentication (legacy)",
                    "required": False,
                },
                {
                    "name": "INFISICAL_CLIENT_ID",
                    "purpose": "Client ID for universal auth (machine identity)",
                    "required": False,
                },
                {
                    "name": "INFISICAL_CLIENT_SECRET",
                    "purpose": "Client secret for universal auth",
                    "required": False,
                },
                {
                    "name": "INFISICAL_PROJECT_ID",
                    "purpose": "Project (workspace) ID",
                    "required": True,
                },
                {
                    "name": "INFISICAL_ENVIRONMENT",
                    "purpose": "Environment slug (e.g. prod, dev, staging)",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "Service token",
                    "description": "Set INFISICAL_TOKEN. Simple, suitable for CI/CD.",
                },
                {
                    "method": "Universal auth",
                    "description": "Set INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET. Recommended for machine identities.",
                },
            ],
            "yaml_example": ("type: infisical\nspec:\n  endpoints:\n    address: https://app.infisical.com"),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure Infisical is configured with project ID and authentication.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.project_id:
            self._info = f"{self.integration_name} project ID not configured."
            return False, f"{self.integration_name} project ID not configured. Set INFISICAL_PROJECT_ID."

        auth = self._get_auth_method()
        if not auth:
            self._info = f"{self.integration_name} authentication not configured."
            return (
                False,
                f"{self.integration_name} authentication not configured. "
                "Set INFISICAL_TOKEN or INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET.",
            )

        self._info = f"{self.integration_name} is configured ({auth})"
        logger.debug("Infisical is available", name=self.integration_name, auth_method=auth)
        return True, ""

    def get_info(self) -> Dict[str, Any]:
        """Return integration information including configuration status."""
        info = super().get_info()
        info["infisical_addr"] = self.infisical_addr
        info["auth_method"] = self._get_auth_method()
        info["project_id"] = self.project_id
        info["environment"] = self.environment
        return info

    # Unified Store Interface Implementation (ISecretStore)

    def get_secret(self, key: str, **kwargs) -> Optional[str]:
        """
        Get a secret from Infisical.

        Implements ISecretStore interface.

        Args:
            key: Secret name
            **kwargs: project_id, environment, secret_path, prefer_cli, timeout

        Returns:
            Secret value if found, None otherwise
        """
        project_id = kwargs.get("project_id", self.project_id)
        environment = kwargs.get("environment", self.environment)
        secret_path = kwargs.get("secret_path", "/")
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.warning(
                "Cannot retrieve secret from Infisical",
                error=error,
                name=self.integration_name,
            )
            return None

        logger.debug(
            "Retrieving secret from Infisical",
            key=key,
            environment=environment,
            name=self.integration_name,
        )

        if prefer_cli and self.is_available():
            result = self._get_secret_via_cli(key, project_id, environment, secret_path, timeout)
            if result is not None:
                logger.info("Secret retrieved from Infisical via CLI", key=key, name=self.integration_name)
                return result

        result = self._get_secret_via_api(key, project_id, environment, secret_path)
        if result is not None:
            logger.info("Secret retrieved from Infisical via API", key=key, name=self.integration_name)
        return result

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """
        Set a secret in Infisical via the API.

        Implements ISecretStore interface.

        Args:
            key: Secret name
            value: Secret value
            **kwargs: project_id, environment, secret_path, timeout

        Returns:
            True if successful, False otherwise
        """
        project_id = kwargs.get("project_id", self.project_id)
        environment = kwargs.get("environment", self.environment)
        secret_path = kwargs.get("secret_path", "/")

        available, error = self.ensure_available()
        if not available:
            return False

        return self._set_secret_via_api(key, value, project_id, environment, secret_path)

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List secret names in Infisical.

        Implements ISecretStore interface.

        Args:
            prefix: Optional name prefix filter
            **kwargs: project_id, environment, secret_path

        Returns:
            List of secret names
        """
        project_id = kwargs.get("project_id", self.project_id)
        environment = kwargs.get("environment", self.environment)
        secret_path = kwargs.get("secret_path", "/")

        available, _ = self.ensure_available()
        if not available:
            return []

        return self._list_secrets_via_api(project_id, environment, secret_path, prefix)

    # Unified Store Interface Implementation (IVariableStore)

    def get_variable(self, key: str, **kwargs) -> Optional[Any]:
        """Get a variable from Infisical (delegates to get_secret)."""
        return self.get_secret(key, **kwargs)

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """Set a variable in Infisical (delegates to set_secret)."""
        return self.set_secret(key, str(value), **kwargs)

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """List variable names from Infisical (delegates to list_secrets)."""
        return self.list_secrets(prefix=prefix, **kwargs)

    # CLI implementations

    def _get_secret_via_cli(
        self,
        key: str,
        project_id: Optional[str],
        environment: str,
        secret_path: str,
        timeout: int,
    ) -> Optional[str]:
        try:
            args = ["secrets", "get", key, "--plain", "--silent"]
            if project_id:
                args.extend(["--projectId", project_id])
            if environment:
                args.extend(["--env", environment])
            if secret_path and secret_path != "/":
                args.extend(["--path", secret_path])
            result = self._run_integration(args=args, timeout=timeout)
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    # API implementations

    def _get_secret_via_api(
        self,
        key: str,
        project_id: Optional[str],
        environment: str,
        secret_path: str,
    ) -> Optional[str]:
        token = self._get_access_token()
        if not token:
            return None
        try:
            params: Dict[str, str] = {"environment": environment, "secretPath": secret_path}
            if project_id:
                params["workspaceId"] = project_id
            query = urllib.parse.urlencode(params)
            url = f"{self.infisical_addr}/api/v3/secrets/raw/{urllib.parse.quote(key)}?{query}"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("secret", {}).get("secretValue")
        except Exception as e:
            logger.debug(
                "Infisical API secret retrieval failed",
                key=key,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

    def _set_secret_via_api(
        self,
        key: str,
        value: str,
        project_id: Optional[str],
        environment: str,
        secret_path: str,
    ) -> bool:
        token = self._get_access_token()
        if not token:
            return False
        try:
            url = f"{self.infisical_addr}/api/v3/secrets/raw/{urllib.parse.quote(key)}"
            payload = json.dumps(
                {
                    "workspaceId": project_id,
                    "environment": environment,
                    "secretPath": secret_path,
                    "secretValue": value,
                }
            ).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="PATCH")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            logger.debug(
                "Infisical API secret set failed",
                key=key,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return False

    def _list_secrets_via_api(
        self,
        project_id: Optional[str],
        environment: str,
        secret_path: str,
        prefix: str = "",
    ) -> List[str]:
        token = self._get_access_token()
        if not token:
            return []
        try:
            params: Dict[str, str] = {"environment": environment, "secretPath": secret_path}
            if project_id:
                params["workspaceId"] = project_id
            query = urllib.parse.urlencode(params)
            url = f"{self.infisical_addr}/api/v3/secrets/raw?{query}"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                keys = [s["secretKey"] for s in data.get("secrets", [])]
                return [k for k in keys if k.startswith(prefix)] if prefix else keys
        except Exception as e:
            logger.debug(
                "Infisical API list secrets failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return []
