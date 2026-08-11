"""Infisical integration for secrets management (open-source, MPL-2.0)."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from strata.exceptions import SecretStoreUnavailableError
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.capabilities import ISecretStore, IVariableStore
from strata.models.integration_model import IntegrationModel
from strata.utils.secret_metadata import SecretMetadata

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

        # Lazy per-process cache of resolved secret values (key -> value) for a given
        # (project_id, environment, secret_path) scope, populated by one bulk list
        # call on first get_secret() call instead of one Infisical call per declared
        # secret. Mirrors FlagsmithIntegration._flags_cache / EtcdIntegration._kv_cache.
        # Only consulted on a HIT — a miss always falls through to the authoritative
        # live per-key lookup (never treated as a confirmed "not found") since a false
        # negative here could trigger unwanted generate-on-missing secret creation in
        # ValueController. Invalidated on any successful write.
        self._secrets_cache: Optional[Dict[str, str]] = None
        self._secrets_cache_scope: Optional[Tuple[str, str, str]] = None

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
        """Return a valid bearer token (service token or cached universal-auth token).

        Raises:
            SecretStoreUnavailableError: universal-auth credentials are present
                but the login attempt failed (bad credentials, wrong project,
                network error, wrong endpoints.address). This is distinct from
                "no credentials configured" (returns ``None``, handled upstream
                by ``ensure_available()``).
        """
        if self.infisical_token:
            return self.infisical_token
        if self._access_token:
            return self._access_token
        if self.client_id and self.client_secret:
            token = self._login_via_universal_auth()
            if token is None:
                raise SecretStoreUnavailableError(
                    self.integration_name,
                    "universal-auth login failed (see logs for details)",
                )
            self._access_token = token
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

        For universal-auth (client id/secret), this also performs a real login
        attempt so a bad credential/network problem is caught here rather than
        surfacing later as an ambiguous "secret not found". Service-token auth
        is presence-checked only — there is no cheap way to verify a bare token
        without also resolving a specific secret.

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

        if auth == "universal-auth":
            try:
                self._get_access_token()
            except SecretStoreUnavailableError as exc:
                self._info = f"{self.integration_name} login check failed."
                return False, f"{self.integration_name} authentication check failed: {exc}"

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
            Secret value, or ``None`` if the key does not exist in the store.

        Raises:
            SecretStoreUnavailableError: Infisical is not configured, or the
                store cannot be reached/authenticated. Never returns ``None``
                for this case — see the ``ISecretStore`` Protocol contract.
        """
        project_id: str = kwargs.get("project_id") or self.project_id or ""
        environment: str = kwargs.get("environment") or self.environment or ""
        secret_path: str = kwargs.get("secret_path") or "/"
        prefer_cli = kwargs.get("prefer_cli", True)
        timeout = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            raise SecretStoreUnavailableError(self.integration_name, error)

        # Fast path: served from the lazily-warmed bulk cache for this scope (see
        # __init__). Only consulted on a HIT — a miss (key not in the bulk listing,
        # or the bulk fetch itself failed) always falls through to the authoritative
        # per-key lookup below, never short-circuited to "not found" here.
        scope = (project_id, environment, secret_path)
        if self._secrets_cache is None or self._secrets_cache_scope != scope:
            fetched = self._fetch_all_secret_values(project_id, environment, secret_path)
            if fetched is not None:
                self._secrets_cache = fetched
                self._secrets_cache_scope = scope
        if self._secrets_cache is not None and self._secrets_cache_scope == scope and key in self._secrets_cache:
            logger.info("Secret retrieved from Infisical via bulk cache", key=key, name=self.integration_name)
            return self._secrets_cache[key]

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
        project_id: str = kwargs.get("project_id") or self.project_id or ""
        environment: str = kwargs.get("environment") or self.environment or ""
        secret_path: str = kwargs.get("secret_path") or "/"

        available, error = self.ensure_available()
        if not available:
            return False

        ok = self._set_secret_via_api(key, value, project_id, environment, secret_path)
        if ok:
            self._secrets_cache = None  # invalidate — next read re-warms with the new value included
        return ok

    def _fetch_all_secret_values(
        self, project_id: Optional[str], environment: str, secret_path: str
    ) -> Optional[Dict[str, str]]:
        """Bulk-fetch every secret's value for this (project, environment, path) scope in one call.

        Backs the cache used by :meth:`get_secret`. Infisical's raw-secrets list
        endpoint (``GET /api/v3/secrets/raw``) already returns a ``secretValue``
        per entry in the same call ``list_secrets()`` uses to list names — this was
        previously discarded, keeping only ``secretKey``. Returns ``None`` (not an
        empty dict) on any failure so callers never mistake "fetch failed" for
        "scope is genuinely empty".
        """
        token = self._get_access_token()
        if not token:
            return None
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
                return {s["secretKey"]: s.get("secretValue", "") for s in data.get("secrets", []) if s.get("secretKey")}
        except Exception as e:
            logger.debug(
                "Infisical bulk secret fetch failed",
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            return None

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
        project_id: str = kwargs.get("project_id") or self.project_id or ""
        environment: str = kwargs.get("environment") or self.environment or ""
        secret_path: str = kwargs.get("secret_path") or "/"

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

    def get_secret_metadata(self, key: str, **kwargs) -> Optional[SecretMetadata]:
        """Return creation/update timestamps for an Infisical secret."""
        project_id: str = kwargs.get("project_id") or self.project_id or ""
        environment: str = kwargs.get("environment") or self.environment or ""
        secret_path: str = kwargs.get("secret_path") or "/"

        available, _ = self.ensure_available()
        if not available:
            return None

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
        except Exception:
            return None

        secret = data.get("secret", {})
        if not secret:
            return None

        meta = SecretMetadata()
        if secret.get("createdAt"):
            try:
                meta.created_at = datetime.fromisoformat(secret["createdAt"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if secret.get("updatedAt"):
            try:
                meta.updated_at = datetime.fromisoformat(secret["updatedAt"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if secret.get("version"):
            meta.version = str(secret["version"])
        return meta

    def update_secret(self, key: str, value: str, **kwargs) -> bool:
        """Overwrite an existing secret in Infisical (rotation only).

        Delegates to the same PATCH API used by set_secret — Infisical's PATCH
        endpoint is inherently an upsert, so this just calls through.
        """
        project_id: str = kwargs.get("project_id") or self.project_id or ""
        environment: str = kwargs.get("environment") or self.environment or ""
        secret_path: str = kwargs.get("secret_path") or "/"

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot update secret in Infisical", name=self.integration_name, error=error)
            return False

        ok = self._set_secret_via_api(key, value, project_id, environment, secret_path)
        if ok:
            logger.info("Secret updated in Infisical", name=self.integration_name, secret_key=key)
        return ok

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
        """Fetch *key* via the Infisical REST API.

        Returns ``None`` only for a confirmed HTTP 404 (key genuinely not
        found). Any other failure (auth, network, non-404 HTTP status) raises
        ``SecretStoreUnavailableError`` — see the ``ISecretStore`` contract.
        """
        token = self._get_access_token()  # may raise SecretStoreUnavailableError
        if not token:
            # No credentials at all. ensure_available() should already have
            # caught this in get_secret(); guarded defensively here too.
            raise SecretStoreUnavailableError(self.integration_name, "no authentication credentials available")
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
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # confirmed: key does not exist
            logger.warning(
                "Infisical API secret retrieval failed",
                key=key,
                http_status=e.code,
                name=self.integration_name,
            )
            raise SecretStoreUnavailableError(
                self.integration_name, f"HTTP {e.code} from Infisical API", cause=e
            ) from e
        except Exception as e:
            logger.warning(
                "Infisical API secret retrieval failed",
                key=key,
                error_type=type(e).__name__,
                name=self.integration_name,
            )
            raise SecretStoreUnavailableError(self.integration_name, f"{type(e).__name__}: {e}", cause=e) from e

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
