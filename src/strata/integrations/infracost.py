"""Infracost integration for infrastructure cost estimation."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from packaging import version as version_lib

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.models.capabilities import ICostEstimator
from strata.models.integration_model import IntegrationModel
from strata.utils.system import run_command

logger = get_logger(__name__)

# This integration drives the 0.10.x ("legacy") Infracost CLI command surface
# (`breakdown`, `diff`). Infracost 2.0 moved to a separate CLI (infracost/cli)
# with a different command set (`auth login`, `setup`, `scan`, `inspect`,
# `update`) and is not supported by this integration.
_MAX_SUPPORTED_VERSION = "0.99.99"


class InfracostIntegration(BaseIntegration):
    """
    Infracost integration for infrastructure cost estimation.

    Provides cost breakdown and diff capabilities for Terraform configurations.
    Supports Azure, AWS, and GCP resources natively.

    Invoked as a CLI binary. There is no bundled pricing database and no
    anonymous/offline mode — every estimate is a live call to
    ``pricing.api.infracost.io``, which requires an Infracost account and an
    ``INFRACOST_API_KEY`` (or a token from ``infracost auth login``). A
    self-hosted Cloud Pricing API can be used instead via
    ``INFRACOST_PRICING_API_ENDPOINT``.

    Only the 0.10.x ("legacy") CLI is supported — Infracost 2.0 replaced
    ``breakdown``/``diff`` with a different command set (``scan``,
    ``inspect``, etc.) that this integration does not drive.

    Install: https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh
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
            "install_url": "https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh",
            "env_vars": [
                {
                    "name": "INFRACOST_API_KEY",
                    "purpose": "Infracost Cloud Pricing API authentication",
                    "required": True,
                },
            ],
            "auth_methods": [
                {
                    "method": "infracost auth login",
                    "description": "Interactive login; stores a token in ~/.config/infracost/credentials.yml.",
                },
                {
                    "method": "INFRACOST_API_KEY",
                    "description": "Non-interactive/CI authentication via a free Infracost account API key.",
                },
                {
                    "method": "INFRACOST_PRICING_API_ENDPOINT",
                    "description": (
                        "Points at a self-hosted Cloud Pricing API instead of "
                        "pricing.api.infracost.io (useful where that host is unreachable)."
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
                '    min_version: "0.10.0"\n'
                f'    max_version: "{_MAX_SUPPORTED_VERSION}"  # reject Infracost 2.x (unsupported CLI)'
            ),
        }

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure infracost binary is available, on a supported (0.10.x) version,
        and has a resolvable API key.

        Returns:
            Tuple of (success, error_message)
        """
        if not self.is_available():
            msg = (
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install the 0.10.x CLI from: "
                "https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh"
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

        current_version = self.get_version()
        if current_version and self._is_unsupported_v2(current_version):
            msg = (
                f"{self.integration_name} version {current_version} is Infracost 2.x, which replaced "
                "the 'breakdown'/'diff' commands this integration relies on with 'scan'/'inspect'. "
                "Install a 0.10.x release instead: "
                "https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh"
            )
            self._info = msg
            logger.warning("Infracost major version unsupported", name=self.integration_name, version=current_version)
            return False, msg

        key_ok, key_error = self._has_resolvable_api_key()
        if not key_ok:
            self._info = key_error
            logger.warning("Infracost API key not resolvable", name=self.integration_name)
            return False, key_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug("Infracost is available", name=self.integration_name, version=self.get_version())
        return True, ""

    def _is_unsupported_v2(self, version_str: str) -> bool:
        """Return True if ``version_str`` is Infracost 2.0+, which this integration cannot drive."""
        try:
            return version_lib.parse(version_str) > version_lib.parse(_MAX_SUPPORTED_VERSION)
        except Exception:
            return False

    def _has_resolvable_api_key(self) -> Tuple[bool, str]:
        """Check for an ``INFRACOST_API_KEY`` env var or an ``infracost auth login`` credentials file.

        Infracost has no anonymous/offline mode — every estimate is a live call to
        the (or a self-hosted) Cloud Pricing API, so a missing key must surface
        here at pre-flight rather than as a ``RuntimeError`` from a failed
        subprocess mid-deploy.
        """
        if self._get_env_var("INFRACOST_API_KEY"):
            return True, ""
        if self._get_env_var("INFRACOST_PRICING_API_ENDPOINT"):
            # Self-hosted Cloud Pricing API — may not require a key at all.
            return True, ""
        if (Path.home() / ".config" / "infracost" / "credentials.yml").exists():
            return True, ""
        msg = (
            f"{self.integration_name} has no resolvable API key. Run 'infracost auth login', "
            "set INFRACOST_API_KEY, or point INFRACOST_PRICING_API_ENDPOINT at a self-hosted "
            "Cloud Pricing API."
        )
        return False, msg

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

        if not result.is_successful:
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

        if not result.is_successful:
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
