"""Terraform integration for infrastructure provisioning operations."""

import re
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.capabilities import IInfrastructureTool
from xyz_platform.logger import get_logger
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.utils.system import CommandResult

logger = get_logger(__name__)


class TerraformIntegration(BaseIntegration):
    """
    Terraform integration for infrastructure provisioning.

    Implements singleton pattern per config - multiple instances possible
    for different Terraform configurations (e.g., different backends, workspaces).
    """

    # Command executable name
    COMMAND = "terraform"

    # Declare supported capabilities
    CAPABILITIES = [IInfrastructureTool]

    # Singleton instance keying based on config name
    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """
        Get instance key based on integration name.

        Creates separate singleton instances per configuration.

        Args:
            class_ref: The class being instantiated
            *args: Constructor positional arguments
            **kwargs: Constructor keyword arguments

        Returns:
            Integration name or "default"
        """
        config = kwargs.get("config") or (args[0] if args else None)
        if not config:
            return "default"

        return config.name or "default"

    # Initializer

    def __init__(self, config: IntegrationModel):
        """
        Initialize Terraform integration.

        Args:
            config: Integration configuration model
        """
        super().__init__(config)

        logger.debug(
            "Terraform integration initialized",
            name=self.integration_name,
        )

    # Base integration methods

    def get_version_command(self) -> List[str]:
        """Get the command to retrieve terraform version."""
        return [self.command, "version"]

    def parse_version(self, version_output: str) -> str:
        """
        Parse version string from terraform output.

        Args:
            version_output: Raw output (e.g., "Terraform v1.5.7")

        Returns:
            Version string (e.g., "1.5.7")
        """
        # Extract version number from "Terraform vX.Y.Z"
        match = re.search(r"v(\d+\.\d+\.\d+)", version_output)
        if match:
            return match.group(1)
        return version_output.strip()

    def ensure_available(self) -> Tuple[bool, str]:
        """
        Ensure integration is available.

        Returns:
            Tuple of (success, error_message)
        """
        # Check integration availability
        if not self.is_available():
            self._info = f"{self.integration_name} CLI is not installed or not in PATH."
            logger.warning(
                "Terraform CLI not found",
                name=self.integration_name,
            )
            return (
                False,
                f"{self.integration_name} CLI is not installed or not in PATH. "
                "Install from: https://www.terraform.io/downloads",
            )

        # Validate version requirements
        version_valid, version_error = self.validate_version()
        if not version_valid:
            self._info = version_error
            logger.warning(
                "Terraform version validation failed",
                name=self.integration_name,
                error=version_error,
            )
            return False, version_error

        self._info = f"{self.integration_name} {self.get_version()} is available"
        logger.debug(
            "Terraform is available",
            name=self.integration_name,
            version=self.get_version(),
        )
        return True, ""

    # Terraform command methods (IInfrastructureTool implementation)

    def init(
        self,
        working_dir: str,
        backend_config: Optional[Dict[str, str]] = None,
        upgrade: bool = False,
        reconfigure: bool = False,
        timeout: int = 300,
        **kwargs,
    ) -> Any:
        """
        Initialize a Terraform working directory.

        Implements IInfrastructureTool.init interface.

        Args:
            working_dir: Working directory containing Terraform configuration
            backend_config: Optional backend configuration key-value pairs
            upgrade: Whether to upgrade modules and plugins
            reconfigure: Whether to reconfigure the backend
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform init fails
        """
        available, error = self.ensure_available()
        if not available:
            logger.error(
                "Cannot run terraform init",
                error=error,
                name=self.integration_name,
            )
            raise RuntimeError(f"Terraform not available: {error}")

        logger.info(
            "Initializing Terraform",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["init"]

        if backend_config:
            for key, value in backend_config.items():
                args.extend(["-backend-config", f"{key}={value}"])

        if upgrade:
            args.append("-upgrade")

        if reconfigure:
            args.append("-reconfigure")

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.info(
                "Terraform initialization completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform init failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform init failed: {e}") from e

    def validate(
        self,
        working_dir: str,
        json_output: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        """
        Validate Terraform configuration files.

        Args:
            working_dir: Working directory containing Terraform configuration
            json_output: Whether to output validation results as JSON
            timeout: Command timeout in seconds

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform validate fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Terraform not available: {error}")

        logger.info(
            "Validating Terraform configuration",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["validate"]

        if json_output:
            args.append("-json")

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.info(
                "Terraform validation completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform validate failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform validate failed: {e}") from e

    def plan(
        self,
        working_dir: str,
        var_file: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        out_file: Optional[str] = None,
        destroy: bool = False,
        target: Optional[List[str]] = None,
        timeout: int = 600,
        **kwargs,
    ) -> Any:
        """
        Generate and show Terraform execution plan.

        Implements IInfrastructureTool.plan interface.

        Args:
            working_dir: Working directory containing Terraform configuration
            var_file: Path to variable file (.tfvars)
            variables: Dictionary of variables to set
            out_file: Path to save the generated plan
            destroy: Generate a plan to destroy all resources
            target: List of specific resources to target
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform plan fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Terraform not available: {error}")

        logger.info(
            "Creating Terraform plan",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["plan"]

        if var_file:
            args.extend(["-var-file", var_file])

        if variables:
            for key, value in variables.items():
                args.extend(["-var", f"{key}={value}"])

        if out_file:
            args.extend(["-out", out_file])

        if destroy:
            args.append("-destroy")

        if target:
            for resource in target:
                args.extend(["-target", resource])

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.info(
                "Terraform plan completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform plan failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform plan failed: {e}") from e

    def apply(
        self,
        working_dir: str,
        plan_file: Optional[str] = None,
        var_file: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        auto_approve: bool = False,
        target: Optional[List[str]] = None,
        timeout: int = 1800,
        **kwargs,
    ) -> CommandResult:
        """
        Apply Terraform configuration changes.

        Implements IInfrastructureTool.apply interface.

        Args:
            working_dir: Working directory containing Terraform configuration
            plan_file: Path to saved plan file to apply
            var_file: Path to variable file (.tfvars)
            variables: Dictionary of variables to set
            auto_approve: Skip interactive approval
            target: List of specific resources to target
            timeout: Command timeout in seconds
            **kwargs: Additional arguments (ignored)

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform apply fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Terraform not available: {error}")

        logger.info(
            "Applying Terraform changes",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["apply"]

        if auto_approve:
            args.append("-auto-approve")

        if plan_file:
            args.append(plan_file)
        else:
            if var_file:
                args.extend(["-var-file", var_file])

            if variables:
                for key, value in variables.items():
                    args.extend(["-var", f"{key}={value}"])

            if target:
                for resource in target:
                    args.extend(["-target", resource])

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.info(
                "Terraform apply completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform apply failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform apply failed: {e}") from e

    def destroy(
        self,
        working_dir: str,
        var_file: Optional[str] = None,
        variables: Optional[Dict[str, str]] = None,
        auto_approve: bool = False,
        target: Optional[List[str]] = None,
        timeout: int = 1800,
    ) -> CommandResult:
        """
        Destroy Terraform-managed infrastructure.

        Args:
            working_dir: Working directory containing Terraform configuration
            var_file: Path to variable file (.tfvars)
            variables: Dictionary of variables to set
            auto_approve: Skip interactive approval
            target: List of specific resources to target
            timeout: Command timeout in seconds

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform destroy fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Terraform not available: {error}")

        logger.warning(
            "Destroying Terraform-managed infrastructure",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["destroy"]

        if auto_approve:
            args.append("-auto-approve")

        if var_file:
            args.extend(["-var-file", var_file])

        if variables:
            for key, value in variables.items():
                args.extend(["-var", f"{key}={value}"])

        if target:
            for resource in target:
                args.extend(["-target", resource])

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.info(
                "Terraform destroy completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform destroy failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform destroy failed: {e}") from e

    def output(
        self,
        working_dir: str,
        output_name: Optional[str] = None,
        json_format: bool = False,
        raw: bool = False,
        timeout: int = 60,
    ) -> CommandResult:
        """
        Read Terraform output values.

        Args:
            working_dir: Working directory containing Terraform state
            output_name: Specific output variable to retrieve
            json_format: Output as JSON
            raw: Output raw value without quotes (for string outputs)
            timeout: Command timeout in seconds

        Returns:
            Command result dict with returncode, stdout, stderr

        Raises:
            RuntimeError: If terraform output fails
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(f"Terraform not available: {error}")

        logger.debug(
            "Reading Terraform outputs",
            working_dir=working_dir,
            name=self.integration_name,
        )

        args = ["output"]

        if json_format:
            args.append("-json")

        if raw:
            args.append("-raw")

        if output_name:
            args.append(output_name)

        try:
            result = self._run_integration(args, cwd=working_dir, timeout=timeout)
            logger.debug(
                "Terraform output completed",
                working_dir=working_dir,
                name=self.integration_name,
            )
            return result
        except Exception as e:
            logger.error(
                "Terraform output failed",
                working_dir=working_dir,
                error_type=type(e).__name__,
                name=self.integration_name,
                exc_info=True,
            )
            raise RuntimeError(f"Terraform output failed: {e}") from e
