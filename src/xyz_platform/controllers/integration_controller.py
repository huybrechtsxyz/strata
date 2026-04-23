"""Controller for managing external integrations — availability, validation, and registration."""

from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.controllers.base_controller import BaseController
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.integrations.registry import IntegrationRegistry
from xyz_platform.models.integration_model import IntegrationModel


class IntegrationController(BaseController):
    """Controller for managing external integrations."""

    def __init__(self) -> None:
        """Initialize the integration controller."""
        super().__init__()
        self._registry = IntegrationRegistry.get_instance()
        self._factory = IntegrationFactory

    def _ensure_integration_registered(self, name: str, integration_type: Optional[str] = None) -> bool:
        """
        Ensure an integration is registered, creating it if necessary.

        Args:
            name: Integration name (e.g., 'git', 'terraform')
            integration_type: Integration type (defaults to name if not provided)

        Returns:
            bool: True if integration is registered or was successfully created
        """
        if self._registry.is_integration_registered(name):
            return True

        # Integration not registered, try to create it
        if not integration_type:
            integration_type = name

        try:
            # Create minimal config for the integration
            config = IntegrationModel(
                name=name,
                type=integration_type,
                required=False,
                enabled=True,
                description="",
                validation=None,
                authentication=None,
                endpoints=None,
                lifecycle=None,
            )

            # Create integration instance using factory
            integration = self._factory.create(config)

            # Register it
            self._registry.register_integration(name, integration)

            self.logger.debug(
                "Auto-registered integration",
                integration_name=name,
                type=integration_type,
            )

            return True

        except Exception as e:
            self.logger.warning(
                "Failed to auto-register integration",
                integration_name=name,
                type=integration_type,
                error=str(e),
            )
            return False

    # Integration status methods

    def get_integration_status(self, name: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Get status of a specific integration.

        Args:
            name: Integration name (e.g., 'git', 'terraform', 'docker')

        Returns:
            Tuple[bool, Dict]: (success, status_dict)

            status_dict contains:
            - name: Integration name
            - available: Boolean availability status
            - version: Version string or None
            - info: Additional info string
        """
        self._errors.clear()
        self._messages.clear()

        try:
            integration = self._registry.get_integration(name)

            if not integration:
                error_msg = f"Integration '{name}' not registered"
                self.logger.warning(
                    "Integration not registered",
                    integration=name,
                )
                self._errors.append(error_msg)
                return False, {
                    "name": name,
                    "available": False,
                    "version": None,
                    "info": error_msg,
                    "required": False,
                    "enabled": False,
                }

            # Get integration info
            info_dict = integration.get_info() or {}

            status: Dict[str, Any] = {
                "name": info_dict.get("name", name),
                "available": info_dict.get("available", False),
                "version": info_dict.get("version"),
                "info": info_dict.get("info", ""),
                "required": info_dict.get("required", False),
                "enabled": info_dict.get("enabled", True),
            }

            self.logger.debug(
                "Retrieved integration status",
                integration=name,
                available=status["available"],
                version=status["version"],
            )

            return True, status

        except Exception as e:
            error_msg = f"Failed to get status for integration '{name}': {str(e)}"
            self.logger.error(
                "Failed to get integration status",
                integration=name,
                error=str(e),
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False, {
                "name": name,
                "available": False,
                "version": None,
                "info": error_msg,
                "required": False,
                "enabled": False,
            }

    def get_all_integrations_status(self) -> Tuple[bool, Dict[str, Dict]]:
        """
        Get status of all registered integrations.

        Returns:
            Tuple[bool, Dict]: (success, status_dict)

            status_dict format:
            {
                "git": {
                    "name": "git",
                    "available": True,
                    "version": "2.40.0",
                    "info": "Git has 2.40.0 installed"
                },
                ...
            }
        """
        self._errors.clear()
        self._messages.clear()

        status = {}
        errors = []

        try:
            integrations = self._registry.get_all_integrations()

            for name, _ in integrations.items():
                try:
                    success, integration_status = self.get_integration_status(name)
                    if success:
                        status[name] = integration_status
                    else:
                        # Add placeholder for failed integrations
                        status[name] = {
                            "name": name,
                            "available": False,
                            "version": None,
                            "info": f"Failed to get status: {self._errors[-1] if self._errors else 'Unknown error'}",
                        }
                        errors.extend(self._errors)

                except Exception as e:
                    error_msg = f"Failed to get status for integration '{name}': {str(e)}"
                    self.logger.warning(
                        "Failed to get integration status",
                        integration=name,
                        error=str(e),
                    )
                    status[name] = {
                        "name": name,
                        "available": False,
                        "version": None,
                        "info": error_msg,
                    }
                    errors.append(error_msg)

            self._errors = errors
            return True, status

        except Exception as e:
            error_msg = f"Failed to retrieve integrations status: {str(e)}"
            self.logger.error(
                "Failed to retrieve integrations status",
                error=str(e),
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False, {}

    # Integration validation methods

    def is_integration_available(self, name: str) -> bool:
        """
        Check if an integration is available.

        Args:
            name: Integration name

        Returns:
            bool: True if integration is available
        """
        try:
            return self._registry.is_integration_available(name)
        except Exception as e:
            self.logger.warning(
                "Error checking integration availability",
                integration=name,
                error=str(e),
            )
            return False

    def ensure_integration_available(self, name: str, operation: Optional[str] = None) -> Tuple[bool, str]:
        """
        Ensure an integration is available and meets requirements.

        Args:
            name: Integration name
            operation: Optional operation name for context in error messages

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        self._errors.clear()
        self._messages.clear()

        try:
            # Ensure integration is registered (create if needed)
            if not self._ensure_integration_registered(name):
                error_msg = f"Integration '{name}' could not be registered"
                if operation:
                    error_msg += f" (required for operation: {operation})"
                self.logger.error(
                    "Integration could not be registered",
                    integration=name,
                    operation=operation,
                )
                self._errors.append(error_msg)
                return False, error_msg

            integration = self._registry.get_integration(name)

            if not integration:
                error_msg = f"Integration '{name}' not found after registration attempt"
                if operation:
                    error_msg += f" (required for operation: {operation})"
                self.logger.error(
                    "Integration not found after registration attempt",
                    integration=name,
                    operation=operation,
                )
                self._errors.append(error_msg)
                return False, error_msg

            # Check availability and version requirements
            success, error = integration.ensure_available()

            if not success:
                if operation:
                    error += f" (required for operation: {operation})"
                self.logger.error(
                    "Integration not available",
                    integration=name,
                    operation=operation,
                    error=error,
                )
                self._errors.append(error)
                return False, error

            self.logger.debug(
                "Integration is available and validated",
                integration=name,
                operation=operation,
            )
            return True, ""

        except Exception as e:
            error_msg = f"Failed to validate integration '{name}': {str(e)}"
            if operation:
                error_msg += f" (required for operation: {operation})"
            self.logger.error(
                "Failed to validate integration",
                integration=name,
                operation=operation,
                error=str(e),
                exc_info=True,
            )
            self._errors.append(error_msg)
            return False, error_msg

    def validate_integrations_for_operation(
        self, operation: str, required_integrations: List[str]
    ) -> Tuple[bool, List[str]]:
        """
        Validate that all required integrations are available for an operation.

        Args:
            operation: Operation name (e.g., 'add_repo', 'deploy', 'config_fetch')
            required_integrations: List of integration names required

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        self._errors.clear()
        self._messages.clear()

        if not required_integrations:
            self.logger.debug(
                "No required integrations for operation",
                operation=operation,
            )
            return True, []

        errors = []

        for integration_name in required_integrations:
            success, error = self.ensure_integration_available(integration_name, operation)
            if not success:
                errors.append(error)

        if errors:
            self.logger.error(
                "Required integrations not available for operation",
                operation=operation,
                errors=errors,
            )
            self._errors = errors
            return False, errors

        self.logger.debug(
            "All required integrations available for operation",
            operation=operation,
            integrations=required_integrations,
        )
        return True, []

    def get_integration(self, name: str, operation: Optional[str] = None) -> Optional[Any]:
        """
        Get a validated integration instance.

        Args:
            name: Integration name
            operation: Optional operation context for validation errors

        Returns:
            Integration instance if available and validated, otherwise None
        """
        success, _ = self.ensure_integration_available(name, operation)
        if not success:
            return None

        return self._registry.get_integration(name)

    def resolve_required_integrations(
        self, required_integrations: Dict[str, str]
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        """
        Resolve and return all required integrations for dependency injection.

        Args:
            required_integrations: Mapping of integration name to operation description

        Returns:
            Tuple[bool, Dict[str, Any], List[str]]:
                - success: True if all required integrations resolved
                - integrations: Mapping of integration name to integration instance
                - errors: List of validation/resolution errors
        """
        self._errors.clear()
        self._messages.clear()

        if not required_integrations:
            return True, {}, []

        resolved_integrations: Dict[str, Any] = {}
        errors: List[str] = []

        for integration_name, operation in required_integrations.items():
            integration = self.get_integration(integration_name, operation)
            if integration is None:
                error_msg = self._errors[-1] if self._errors else f"Failed to resolve integration '{integration_name}'"
                errors.append(error_msg)
                continue

            resolved_integrations[integration_name] = integration

        if errors:
            self._errors = errors
            self.logger.error(
                "Failed to resolve all required integrations",
                required=list(required_integrations.keys()),
                resolved=list(resolved_integrations.keys()),
                errors=errors,
            )
            return False, {}, errors

        self.logger.debug(
            "Resolved required integrations",
            integrations=list(resolved_integrations.keys()),
        )
        return True, resolved_integrations, []

    # Convenience methods for common integrations

    def check_git_available(self) -> Tuple[bool, str]:
        """
        Check if Git is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("git", "repository operations")

    def check_docker_available(self) -> Tuple[bool, str]:
        """
        Check if Docker is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("docker", "container operations")

    def check_terraform_available(self) -> Tuple[bool, str]:
        """
        Check if Terraform is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("terraform", "infrastructure provisioning")

    def check_bitwarden_available(self) -> Tuple[bool, str]:
        """
        Check if Bitwarden is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("bitwarden", "secret management")

    def check_azure_keyvault_available(self) -> Tuple[bool, str]:
        """
        Check if Azure Key Vault is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("azure-keyvault", "Azure secret management")

    def check_azure_appconfig_available(self) -> Tuple[bool, str]:
        """
        Check if Azure App Configuration is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("azure-appconfig", "Azure configuration management")

    def check_consul_available(self) -> Tuple[bool, str]:
        """
        Check if HashiCorp Consul is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("consul", "service discovery and configuration")

    def check_vault_available(self) -> Tuple[bool, str]:
        """
        Check if HashiCorp Vault is available.

        Returns:
            Tuple[bool, str]: (is_available, error_message)
        """
        return self.ensure_integration_available("vault", "secret management")

    def get_git_version(self) -> Optional[str]:
        """
        Get Git version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("git")
        if integration:
            return integration.get_version()
        return None

    def get_docker_version(self) -> Optional[str]:
        """
        Get Docker version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("docker")
        if integration:
            return integration.get_version()
        return None

    def get_terraform_version(self) -> Optional[str]:
        """
        Get Terraform version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("terraform")
        if integration:
            return integration.get_version()
        return None

    def get_bitwarden_version(self) -> Optional[str]:
        """
        Get Bitwarden version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("bitwarden")
        if integration:
            return integration.get_version()
        return None

    def get_azure_keyvault_version(self) -> Optional[str]:
        """
        Get Azure Key Vault version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("azure-keyvault")
        if integration:
            return integration.get_version()
        return None

    def get_azure_appconfig_version(self) -> Optional[str]:
        """
        Get Azure App Configuration version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("azure-appconfig")
        if integration:
            return integration.get_version()
        return None

    def get_consul_version(self) -> Optional[str]:
        """
        Get HashiCorp Consul version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("consul")
        if integration:
            return integration.get_version()
        return None

    def get_vault_version(self) -> Optional[str]:
        """
        Get HashiCorp Vault version.

        Returns:
            Version string or None
        """
        integration = self._registry.get_integration("vault")
        if integration:
            return integration.get_version()
        return None
