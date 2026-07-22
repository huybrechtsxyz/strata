"""Infracost integration for infrastructure cost estimation."""

import json
import re
from typing import Any, Dict, List, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ICostEstimator
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel
from strata.utils.system import run_command

logger = get_logger(__name__)


class InfracostIntegration(BaseIntegration):
    """
    Infracost integration for infrastructure cost estimation.

    Provides cost breakdown and diff capabilities for Terraform configurations.
    Supports Azure, AWS, and GCP resources natively.

    Invoked as a CLI binary — no API key required for basic estimation
    (uses bundled pricing database). Network access required for fresh
    price lookups; results are cached locally by Infracost.

    Install: https://www.infracost.io/docs/install
    """

    COMMAND = "infracost"
    CAPABILITIES = [ICostEstimator]

    def __init__(self, config: IntegrationModel):
        """Initialize Infracost integration."""
        super().__init__(config)
        logger.debug("Infracost integration initialized", name=self.integration_name)

    # ------------------------------------------------------------------
    # BaseIntegration abstract methods
    # ------------------------------------------------------------------

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve infracost version."""
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version from infracost output.

        Args:
            version_output: Raw output (e.g., "Infracost v0.10.40")

        Returns:
            Version string (e.g., "0.10.40")
        """
        match = re.search(r"v?(\d+\.\d+\.\d+)", version_output)
        return match.group(1) if match else version_output.strip()

    def get_setup_info(self) -> dict:
        """Return setup metadata for infracost."""
        return {
            "name": "infracost",
            "command": "infracost",
            "install_url": "https://www.infracost.io/docs/install",
            "env_vars": [],
            "auth_methods": [
                {
                    "method": "Cloud credentials",
                    "description": (
                        "Uses the same cloud credentials as Terraform "
                        "(Azure CLI, AWS env vars, GCP application credentials). "
                        "No additional authentication required."
                    ),
                },
            ],
            "yaml_example": (
                "- name: infracost\n"
                "  type: infracost\n"
                "  capabilities: [cost]\n"
                "  required: false\n"
                "  validation:\n"
                "    command: infracost --version\n"
                '    min_version: "0.10.0"'
            ),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure infracost binary is available.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            msg = (
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from: https://www.infracost.io/docs/install"
            )
            self._info = msg
            logger.warning("Infracost CLI not found", name=self.integration_name)
            return False, msg

        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Infracost version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug("Infracost is available", name=self.integration_name, version=self.get_version())
        return True, ""

    # ------------------------------------------------------------------
    # ICostEstimator implementation
    # ------------------------------------------------------------------

    def breakdown(self, terraform_path: str, **kwargs) -> Dict[str, Any]:
        """
        Get cost breakdown for a terraform configuration.

        Runs: infracost breakdown --path <terraform_path> --format json

        Args:
            terraform_path: Path to terraform directory (must contain .terraform/)
            **kwargs:
                currency (str): Currency code override (e.g., "EUR", "GBP")

        Returns:
            Parsed Infracost JSON output with monthly cost estimates per resource.

        Raises:
            RuntimeError: If infracost command fails.
        """
        cmd = [
            self.command,
            "breakdown",
            "--path",
            terraform_path,
            "--format",
            "json",
            "--no-color",
        ]

        currency = kwargs.get("currency")
        if currency:
            cmd.extend(["--currency", currency])

        logger.debug("Running infracost breakdown", path=terraform_path)
        result = run_command(cmd, timeout=180)

        if not result.success:
            logger.error(
                "Infracost breakdown failed",
                path=terraform_path,
                stderr=result.stderr,
            )
            raise RuntimeError(f"infracost breakdown failed: {result.stderr}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse infracost output: {e}") from e

    def diff(self, terraform_path: str, plan_file: str, **kwargs) -> Dict[str, Any]:
        """
        Get cost diff between current state and a terraform plan.

        Runs: infracost diff --path <terraform_path> --terraform-plan-json <plan_file> --format json

        Args:
            terraform_path: Path to terraform directory
            plan_file: Path to terraform plan JSON file (from: terraform plan -out=plan.tfplan
                       followed by: terraform show -json plan.tfplan > plan.json)
            **kwargs:
                currency (str): Currency code override (e.g., "EUR", "GBP")

        Returns:
            Parsed Infracost JSON output with before/after costs and monthly delta.

        Raises:
            RuntimeError: If infracost command fails.
        """
        cmd = [
            self.command,
            "diff",
            "--path",
            terraform_path,
            "--terraform-plan-json",
            plan_file,
            "--format",
            "json",
            "--no-color",
        ]

        currency = kwargs.get("currency")
        if currency:
            cmd.extend(["--currency", currency])

        logger.debug("Running infracost diff", path=terraform_path, plan=plan_file)
        result = run_command(cmd, timeout=180)

        if not result.success:
            logger.error(
                "Infracost diff failed",
                path=terraform_path,
                plan=plan_file,
                stderr=result.stderr,
            )
            raise RuntimeError(f"infracost diff failed: {result.stderr}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse infracost output: {e}") from e
