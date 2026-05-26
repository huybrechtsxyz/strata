"""OpenTofu integration — open-source Terraform fork (Linux Foundation, MPL-2.0)."""

from typing import Tuple

from strata.integrations.terraform import TerraformIntegration
from strata.logger import get_logger

logger = get_logger(__name__)


class OpenTofuIntegration(TerraformIntegration):
    """
    OpenTofu integration for infrastructure provisioning.

    OpenTofu is the Linux Foundation fork of Terraform, released under MPL-2.0.
    It is a drop-in replacement with an identical CLI interface and state format.
    Use ``type: opentofu`` in your integration config to target the ``tofu`` binary
    instead of the HashiCorp ``terraform`` binary.

    https://opentofu.org
    """

    COMMAND = "tofu"

    def get_setup_info(self) -> dict:
        """Return setup metadata for opentofu."""
        return {
            "name": "opentofu",
            "command": "tofu",
            "install_url": "https://opentofu.org/docs/intro/install/",
            "env_vars": [
                {
                    "name": "TERRAFORM_API_TOKEN",
                    "purpose": "API token for Terraform Cloud / HCP Terraform authentication",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "Environment variable",
                    "description": "Set TERRAFORM_API_TOKEN. Platform writes a temporary .terraformrc on deploy.",
                },
                {
                    "method": "Credentials file",
                    "description": "~/.terraform.d/credentials.tfrc.json with token for app.terraform.io.",
                },
                {
                    "method": "Interactive login",
                    "description": "Run 'tofu login' once; stored in credentials file.",
                },
            ],
            "yaml_example": "type: opentofu\nspec:\n  source: path/to/module\n  backend: remote",
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure OpenTofu is available.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning("OpenTofu CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from: https://opentofu.org/docs/intro/install/",
            )

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "OpenTofu version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug("OpenTofu is available", name=self.integration_name, version=self.get_version())
        return True, ""
