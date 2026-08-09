"""Helm integration for Kubernetes chart operations."""

import re
from typing import List, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.models.capabilities import IInfrastructureTool
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class HelmIntegration(BaseIntegration):
    """
    Helm integration for Kubernetes chart operations.

    Wraps the helm CLI for deploying charts to Kubernetes clusters.
    Implements singleton pattern per config.
    """

    # Command executable name
    COMMAND = "helm"

    # Declare supported capabilities
    CAPABILITIES = [IInfrastructureTool]

    # Singleton instance keying based on config name
    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key based on integration name."""
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"
        return config.name or "default"

    def __init__(self, config: IntegrationModel):
        """Initialize Helm integration."""
        super().__init__(config)
        logger.debug("Helm integration initialized", name=self.integration_name)

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve helm version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """Parse version string from helm version output.

        Example output: 'version.BuildInfo{Version:"v3.14.0",...}'
        """
        match = re.search(r"v?(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        # Fallback: plain semver without 'v' prefix
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for helm."""
        return {
            "name": "helm",
            "command": "helm",
            "install_url": "https://helm.sh/docs/intro/install/",
            "env_vars": [
                {
                    "name": "KUBECONFIG",
                    "purpose": "Path to the kubeconfig file used for Kubernetes authentication",
                    "required": False,
                },
            ],
            "auth_methods": [
                {
                    "method": "kubeconfig",
                    "description": "Helm uses the kubeconfig file (KUBECONFIG env var or ~/.kube/config).",
                },
                {
                    "method": "helm registry login",
                    "description": "Run 'helm registry login <registry>' to authenticate to a private chart registry.",
                },
            ],
            "yaml_example": (
                "type: helm\n"
                "spec:\n"
                "  source:\n"
                "    chart_repository: https://charts.example.com\n"
                "    chart_name: my-chart\n"
                '    chart_version: "1.0.0"'
            ),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """Ensure integration is available."""
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning("Helm CLI not found", name=self.integration_name)
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                f"Install from: https://helm.sh/docs/intro/install/",
            )

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Helm version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Helm is available",
            name=self.integration_name,
            version=self.get_version(),
        )
        return True, ""
