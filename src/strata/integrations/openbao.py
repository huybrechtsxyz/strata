"""OpenBao integration — open-source Vault fork (Linux Foundation, MPL-2.0)."""

from typing import Tuple

from strata.integrations.hashicorp_vault import VaultIntegration
from strata.logger import get_logger

logger = get_logger(__name__)


class OpenBaoIntegration(VaultIntegration):
    """
    OpenBao integration for secrets management and key-value storage.

    OpenBao is the Linux Foundation fork of HashiCorp Vault, released under MPL-2.0.
    It maintains full API and authentication compatibility (AppRole, Kubernetes, token).
    Use ``type: openbao`` in your integration config to target the ``bao`` binary
    instead of the HashiCorp ``vault`` binary.

    https://openbao.org
    """

    COMMAND = "bao"

    def get_setup_info(self) -> dict:
        """Return setup metadata for openbao."""
        return {
            "name": "openbao",
            "command": "bao",
            "install_url": "https://openbao.org/docs/install/",
            "env_vars": [
                {"name": "VAULT_TOKEN", "purpose": "OpenBao authentication token", "required": True},
                {
                    "name": "VAULT_ADDR",
                    "purpose": "OpenBao server address (derived from endpoints.address if set)",
                    "required": False,
                },
            ],
            "auth_methods": [
                {"method": "Token", "description": "Set VAULT_TOKEN. Most common method for automation."},
                {
                    "method": "AppRole",
                    "description": "Obtain token via bao write auth/approle/login; then set VAULT_TOKEN.",
                },
            ],
            "yaml_example": "type: openbao\nspec:\n  endpoints:\n    address: https://bao.example.com",
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure OpenBao is available with proper configuration.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning("OpenBao CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from: https://openbao.org/docs/install/",
            )

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "OpenBao version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        if not self.vault_addr:
            self._info = f"{self.integration_name} address not configured."
            logger.warning("OpenBao address not configured", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} address not configured. "
                "Set endpoints.address in your integration config or VAULT_ADDR env var.",
            )

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug("OpenBao is available", name=self.integration_name, version=self.get_version())
        return True, ""
