"""Bitwarden Secrets Manager integration (bws CLI)."""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.capabilities import ISecretStore
from strata.integrations.store_integration import StoreIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class BitwardenIntegration(StoreIntegration):
    """
    Bitwarden Secrets Manager integration.

    Implements singleton pattern per access token - multiple instances
    possible for different Bitwarden access tokens.
    """

    # Command executable name
    COMMAND = "bws"

    # Declare supported capabilities
    CAPABILITIES = [ISecretStore]

    # Singleton instance keying based on access token

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on access token.

        Creates separate singleton instances per access token.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Access token string or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        # Get access token from config (api_key.api_key holds the env-var name)
        if config.authentication and config.authentication.method == "api_key" and config.authentication.api_key:
            access_token = os.getenv(config.authentication.api_key.api_key, "")
            return access_token or "default"

        return "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize Bitwarden Secrets Manager integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        # Get access token from config (api_key.api_key holds the env-var name)
        self.access_token = None
        token_var = self._get_token_var_name()
        if token_var:
            self.access_token = self._get_env_var(token_var)
            logger.debug(
                "Bitwarden access token configuration",
                name=self.integration_name,
                has_token=bool(self.access_token),
            )

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve bws version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from bws output.

        Args:
            version_output: Raw output (e.g., "bws 1.0.0")

        Returns:
            Version string (e.g., "1.0.0")
        """
        # Extract version number
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for bitwarden."""
        return {
            "name": "bitwarden",
            "command": "bws",
            "install_url": "https://bitwarden.com/help/secrets-manager-cli/",
            "env_vars": [
                {
                    "name": "BWS_ACCESS_TOKEN",
                    "purpose": "Machine account access token for Bitwarden Secrets Manager",
                    "required": True,
                },
            ],
            "auth_methods": [
                {
                    "method": "Machine account token",
                    "description": "Set BWS_ACCESS_TOKEN (default) or configure api_key.api_key in the integration spec.",
                },
            ],
            "yaml_example": "type: bitwarden\nspec:\n  project_id: <project-uuid>",
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
            logger.warning("Bitwarden CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                f"Install from: {self.config.description if self.config.description else 'https://bitwarden.com/help/cli'}",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning("Bitwarden version validation failed", name=self.integration_name, error=version_error)
            return False, version_error

        # Check access token
        if not self.access_token:
            token_var = self._get_token_var_name()
            self._info = f"{self.integration_name} access token '{token_var}' not set."
            logger.warning("Bitwarden access token not configured", name=self.integration_name, env_var=token_var)
            return (
                False,
                f"{token_var} environment variable not set. Set it with your Bitwarden Secrets Manager access token.",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Bitwarden is available and configured",
            integration_name=self.integration_name,
            version=self.get_version(),
        )
        return True, ""

    def _get_token_var_name(self) -> str:
        """
        Return the env-var name that holds the BWS access token.

        Reads from ``authentication.api_key.api_key`` (the key reference field
        in the new AuthenticationModel).  Falls back to ``"BWS_ACCESS_TOKEN"``
        so existing configs that omit authentication still work.
        """
        if (
            self.config.authentication
            and self.config.authentication.method == "api_key"
            and self.config.authentication.api_key
        ):
            value = self.config.authentication.api_key.api_key
            if value:
                return value

        return "BWS_ACCESS_TOKEN"

    def get_info(self) -> Dict[str, Any]:
        """
        Get integration information including configuration status.

        Returns:
            Dict with integration information
        """
        info = super().get_info()
        info["access_token"] = bool(self.access_token)
        return info

    # Secret management methods

    def has_access_token(self) -> bool:
        """
        Check if access token is set.

        Returns:
            True if access token is set, False otherwise
        """
        return bool(self.access_token)

    def get_secret(self, key: str, timeout: int = 60, **kwargs) -> Optional[str]:
        """
        Retrieve a secret from Bitwarden Secrets Manager.

        Implements the unified store interface from StoreIntegration.

        Args:
            key: The Bitwarden secret ID (UUID format)
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            The secret value if found, None otherwise
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot retrieve secret", name=self.integration_name, error=error)
            return None

        try:
            logger.debug("Retrieving secret from Bitwarden", name=self.integration_name, secret_id=key)

            # Set up environment with access token
            env = {**os.environ}
            if self.access_token:
                env[self._get_token_var_name()] = self.access_token

            result = self._run_integration(args=["secret", "get", key], timeout=timeout, env=env)

            if result.returncode == 0 and result.stdout:
                # Parse JSON output
                data = json.loads(result.stdout)
                logger.info("Secret retrieved from Bitwarden", name=self.integration_name, secret_id=key)
                return data.get("value")

            logger.error(
                "Failed to get secret from Bitwarden",
                name=self.integration_name,
                secret_id=key,
                stderr=result.stderr,
            )
            return None

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse secret JSON from Bitwarden",
                name=self.integration_name,
                secret_id=key,
                error=str(e),
                exc_info=True,
            )
            return None

        except Exception as e:
            logger.error(
                "Error retrieving secret from Bitwarden",
                name=self.integration_name,
                secret_id=key,
                error_type=type(e).__name__,
                exc_info=True,
            )
            return None

    def _list_secrets_full(self, timeout: int = 60) -> Optional[List[Dict[str, Any]]]:
        """
        List all secrets from Bitwarden Secrets Manager (full details).

        Internal method that returns full secret objects.

        Args:
            timeout: Command timeout in seconds

        Returns:
            List of secret objects or None if failed
        """
        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot list secrets", name=self.integration_name, error=error)
            return None

        try:
            logger.debug("Listing secrets from Bitwarden", name=self.integration_name)

            # Set up environment with access token
            env = {**os.environ}
            if self.access_token:
                env[self._get_token_var_name()] = self.access_token

            result = self._run_integration(args=["secret", "list"], timeout=timeout, env=env)

            if result.returncode == 0 and result.stdout:
                # Parse JSON output - returns array of secrets
                secrets = json.loads(result.stdout)
                logger.info("Listed secrets from Bitwarden", name=self.integration_name, count=len(secrets))
                return secrets

            logger.error("Failed to list secrets from Bitwarden", name=self.integration_name, stderr=result.stderr)
            return None

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse secrets JSON from Bitwarden", name=self.integration_name, error=str(e), exc_info=True
            )
            return None

        except Exception as e:
            logger.error(
                "Error listing secrets from Bitwarden",
                name=self.integration_name,
                error_type=type(e).__name__,
                exc_info=True,
            )
            return None

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List secret IDs from Bitwarden Secrets Manager.

        Implements the unified store interface from StoreIntegration.

        Args:
            prefix: Filter secrets by key prefix (case-sensitive)
            **kwargs: Additional arguments (timeout supported)

        Returns:
            List of secret IDs (UUIDs)
        """
        timeout = kwargs.get("timeout", 60)
        secrets = self._list_secrets_full(timeout=timeout)

        if secrets is None:
            logger.debug("Failed to list secrets from Bitwarden", name=self.integration_name)
            return []

        # Extract secret IDs (keys) and filter by prefix
        secret_ids = [s.get("key", "") for s in secrets if s.get("key")]

        if prefix:
            secret_ids = [sid for sid in secret_ids if sid.startswith(prefix)]

        logger.debug(
            "Listed secret IDs from Bitwarden", name=self.integration_name, count=len(secret_ids), prefix=prefix
        )

        return secret_ids

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """
        Create a secret in Bitwarden Secrets Manager (create-if-not-exists semantics).

        Implements ISecretStore interface.  Never overwrites an existing secret —
        if a secret with the same key already exists the method returns True without writing.

        Note: Bitwarden identifies secrets by UUID, but allows human-readable key names.
        This method creates a new secret entry using ``bw secret create``.

        Args:
            key: Human-readable key / name for the secret
            value: Secret value to store
            **kwargs: project_id, timeout

        Returns:
            True if the secret exists (created now or already present), False on failure
        """
        timeout: int = kwargs.get("timeout", 60)

        available, error = self.ensure_available()
        if not available:
            logger.warning("Cannot write secret to Bitwarden", name=self.integration_name, error=error)
            return False

        # Check existence first — never overwrite.
        # Bitwarden uses UUIDs as primary identifiers; key is the human name.
        # We search by key in the listing.
        existing_secrets = self._list_secrets_full(timeout=timeout) or []
        for s in existing_secrets:
            if s.get("key") == key:
                logger.info(
                    "Secret already exists in Bitwarden — skipping write", name=self.integration_name, secret_key=key
                )
                return True

        logger.debug("Writing secret to Bitwarden", name=self.integration_name, secret_key=key)

        env = {**os.environ}
        if self.access_token:
            env[self._get_token_var_name()] = self.access_token

        try:
            result = self._run_integration(
                args=["secret", "create", key, value],
                timeout=timeout,
                env=env,
            )
            if result.returncode == 0:
                logger.info("Secret written to Bitwarden", name=self.integration_name, secret_key=key)
                return True
            logger.warning(
                "Failed to write secret to Bitwarden", name=self.integration_name, secret_key=key, stderr=result.stderr
            )
            return False
        except Exception as e:
            logger.warning(
                "Error writing secret to Bitwarden", name=self.integration_name, secret_key=key, error=str(e)
            )
            return False
