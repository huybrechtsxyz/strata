#!/usr/bin/env python3
"""
===============================================================================
Script Name   : bitwarden.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Bitwarden Secrets Manager integration for XYZ Platform.
===============================================================================
"""

import json
import os
import re
from typing import Dict, List, Optional, Any, Tuple

from xyz_platform.logger import get_logger
from xyz_platform.integrations.capabilities import ISecretStore
from xyz_platform.integrations.store_integration import StoreIntegration
from xyz_platform.models.integration_model import IntegrationModel

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

        # Get access token from config
        if config.authentication and config.authentication.env_vars:
            access_token = os.getenv(config.authentication.env_vars[0], "")
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

        # Get access token from config
        self.access_token = None
        if self.config.authentication and self.config.authentication.env_vars:
            token_var = self.config.authentication.env_vars[0]  # BWS_ACCESS_TOKEN
            self.access_token = self._get_env_var(token_var)
            logger.debug(
                "Bitwarden access token configuration",
                extra={
                    "name": self.integration_name,
                    "has_token": bool(self.access_token),
                },
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
                "Bitwarden CLI not found", extra={"name": self.integration_name}
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                f"Install from: {self.config.description if self.config.description else 'https://bitwarden.com/help/cli'}",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Bitwarden version validation failed",
                extra={"name": self.integration_name, "error": version_error},
            )
            return False, version_error

        # Check access token
        if not self.access_token:
            token_var = (
                self.config.authentication.env_vars[0]
                if self.config.authentication and self.config.authentication.env_vars
                else "BWS_ACCESS_TOKEN"
            )
            self._info = f"{self.integration_name} access token '{token_var}' not set."
            logger.warning(
                "Bitwarden access token not configured",
                extra={"name": self.integration_name, "env_var": token_var},
            )
            return (
                False,
                f"{token_var} environment variable not set. "
                f"Set it with your Bitwarden Secrets Manager access token.",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Bitwarden is available and configured",
            extra={"name": self.integration_name, "version": self.get_version()},
        )
        return True, ""

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
            logger.warning(
                "Cannot retrieve secret",
                extra={"error": error, "name": self.integration_name},
            )
            return None

        try:
            logger.debug(
                "Retrieving secret from Bitwarden",
                extra={"secret_id": key, "name": self.integration_name},
            )

            # Set up environment with access token
            env = {**os.environ}
            if self.access_token:
                token_var = (
                    self.config.authentication.env_vars[0]
                    if self.config.authentication
                    and self.config.authentication.env_vars
                    else "BWS_ACCESS_TOKEN"
                )
                env[token_var] = self.access_token

            result = self._run_integration(
                args=["secret", "get", key], timeout=timeout, env=env
            )

            if result["returncode"] == 0 and result["stdout"]:
                # Parse JSON output
                data = json.loads(result["stdout"])
                logger.info(
                    "Secret retrieved from Bitwarden",
                    extra={"secret_id": key, "name": self.integration_name},
                )
                return data.get("value")

            logger.error(
                "Failed to get secret from Bitwarden",
                extra={
                    "secret_id": key,
                    "stderr": result["stderr"],
                    "name": self.integration_name,
                },
            )
            return None

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse secret JSON from Bitwarden",
                extra={"secret_id": key, "name": self.integration_name},
                exc_info=True,
            )
            return None

        except Exception as e:
            logger.error(
                "Error retrieving secret from Bitwarden",
                extra={
                    "secret_id": key,
                    "error_type": type(e).__name__,
                    "name": self.integration_name,
                },
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
            logger.warning(
                "Cannot list secrets",
                extra={"error": error, "name": self.integration_name},
            )
            return None

        try:
            logger.debug(
                "Listing secrets from Bitwarden", extra={"name": self.integration_name}
            )

            # Set up environment with access token
            env = {**os.environ}
            if self.access_token:
                token_var = (
                    self.config.authentication.env_vars[0]
                    if self.config.authentication
                    and self.config.authentication.env_vars
                    else "BWS_ACCESS_TOKEN"
                )
                env[token_var] = self.access_token

            result = self._run_integration(
                args=["secret", "list"], timeout=timeout, env=env
            )

            if result["returncode"] == 0 and result["stdout"]:
                # Parse JSON output - returns array of secrets
                secrets = json.loads(result["stdout"])
                logger.info(
                    "Listed secrets from Bitwarden",
                    extra={"count": len(secrets), "name": self.integration_name},
                )
                return secrets

            logger.error(
                "Failed to list secrets from Bitwarden",
                extra={"stderr": result["stderr"], "name": self.integration_name},
            )
            return None

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse secrets JSON from Bitwarden",
                extra={"name": self.integration_name},
                exc_info=True,
            )
            return None

        except Exception as e:
            logger.error(
                "Error listing secrets from Bitwarden",
                extra={"error_type": type(e).__name__, "name": self.integration_name},
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
            logger.debug(
                "Failed to list secrets from Bitwarden",
                extra={"name": self.integration_name},
            )
            return []

        # Extract secret IDs (keys) and filter by prefix
        secret_ids = [s.get("key", "") for s in secrets if s.get("key")]

        if prefix:
            secret_ids = [sid for sid in secret_ids if sid.startswith(prefix)]

        logger.debug(
            "Listed secret IDs from Bitwarden",
            extra={
                "count": len(secret_ids),
                "prefix": prefix,
                "name": self.integration_name,
            },
        )

        return secret_ids
