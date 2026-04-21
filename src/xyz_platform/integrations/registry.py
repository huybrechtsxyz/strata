"""Registry for managing and querying registered integration instances by name and capability."""

from typing import Any, Dict, List, Optional, Set, Tuple, Type
import threading

from xyz_platform.integrations.capabilities import (
    CAPABILITY_REGISTRY,
    IFeatureStore,
    IInfrastructureTool,
    IKVStore,
    IRepositoryTool,
    ISecretStore,
    IVariableStore,
    get_capability_name,
)
from xyz_platform.logger import get_logger

logger = get_logger(__name__)


class IntegrationRegistry:
    """
    Registry for managing external integration dependencies.

    Tracks which integrations are loaded for specific operations and validates
    their availability before execution.

    Singleton Pattern:
    - Thread-safe singleton implementation
    - Single registry instance across the platform
    """

    _instance: Optional["IntegrationRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Create singleton instance."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cls._instance

    def __init__(self):
        """Initialize integration registry (only once)."""
        if self._initialized:
            return
        self._integrations: Dict[str, Any] = {}
        self._requirements: Dict[str, Set[str]] = {}
        self._initialized = True
        logger.debug("IntegrationRegistry initialized")

    @classmethod
    def get_instance(cls) -> "IntegrationRegistry":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset(cls):
        """Reset singleton instance (useful for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance._integrations.clear()
                cls._instance._requirements.clear()
            cls._instance = None

    # Integration registration methods

    def register_integration(self, name: str, integration_instance: Any) -> None:
        """
        Register an integration instance.

        Args:
            name: Integration name (e.g., 'git', 'terraform', 'bitwarden')
            integration_instance: Integration instance (must have is_available() method)
        """
        if not hasattr(integration_instance, "is_available"):
            raise ValueError(
                f"Integration instance for '{name}' must have is_available() method"
            )
        self._integrations[name] = integration_instance
        logger.debug("Integration registered", name=name)

    def get_integration(self, name: str) -> Optional[Any]:
        """
        Get registered integration instance.

        Args:
            name: Integration name

        Returns:
            Integration instance or None if not registered
        """
        return self._integrations.get(name)

    def get_all_integrations(self) -> Dict[str, Any]:
        """
        Get all registered integrations.

        Returns:
            Dictionary of integration name to integration instance
        """
        return dict(self._integrations)

    def is_integration_registered(self, name: str) -> bool:
        """
        Check if integration is registered.

        Args:
            name: Integration name

        Returns:
            True if integration is registered
        """
        return name in self._integrations

    def is_integration_available(self, name: str) -> bool:
        """
        Check if registered integration is available.

        Args:
            name: Integration name

        Returns:
            True if integration is available, False otherwise
        """
        integration = self._integrations.get(name)
        if integration is None:
            return False
        return integration.is_available()

    def list_integrations(self) -> List[str]:
        """
        List all registered integration names.

        Returns:
            List of integration names
        """
        return list(self._integrations.keys())

    def get_integration_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all registered integrations.

        Returns:
            Dictionary with integration status information
        """
        status = {}
        for name, integration in self._integrations.items():
            status[name] = {
                "registered": True,
                "name": (
                    integration.integration_name
                    if hasattr(integration, "integration_name")
                    else name
                ),
                "type": (
                    integration.integration_type
                    if hasattr(integration, "integration_type")
                    else "unknown"
                ),
                "available": integration.is_available(),
                "version": (
                    integration.get_version()
                    if hasattr(integration, "get_version")
                    else None
                ),
                "info": (
                    integration.get_info() if hasattr(integration, "get_info") else {}
                ),
            }
        return status

    def validate_integrations(
        self, integration_names: List[str]
    ) -> Tuple[bool, List[str]]:
        """
        Validate that specific integrations are available.

        Args:
            integration_names: List of integration names to validate

        Returns:
            Tuple of (success, list of error messages)
        """
        errors = []

        for integration_name in integration_names:
            if not self.is_integration_registered(integration_name):
                errors.append(
                    f"Integration '{integration_name}' is not registered in registry"
                )
                continue

            if not self.is_integration_available(integration_name):
                integration = self._integrations[integration_name]
                integration_display = getattr(
                    integration, "integration_name", integration_name
                )
                errors.append(
                    f"Integration '{integration_display}' is not available. "
                    f"Please install it and ensure it's in your PATH."
                )

        return len(errors) == 0, errors

    # Capability-based integration query methods

    def supports_capability(self, integration_name: str, capability: Type) -> bool:
        """
        Check if an integration supports a specific capability.

        Checks explicit CAPABILITIES attribute first, then falls back to
        structural subtyping (Protocol) via isinstance().

        Args:
            integration_name: Integration name
            capability: Capability protocol class (e.g., IVariableStore)

        Returns:
            True if integration supports the capability, False otherwise
        """
        integration = self._integrations.get(integration_name)
        if integration is None:
            return False

        # Check explicit CAPABILITIES declaration first
        if hasattr(integration, "CAPABILITIES"):
            return capability in integration.CAPABILITIES

        # Fall back to isinstance() check for duck typing
        return isinstance(integration, capability)

    def get_integrations_with_capability(self, capability: Type) -> List[str]:
        """
        Get all integrations that support a specific capability.

        Args:
            capability: Capability protocol class (e.g., IVariableStore)

        Returns:
            List of integration names supporting the capability
        """
        return [
            name
            for name in self._integrations.keys()
            if self.supports_capability(name, capability)
        ]

    def get_integrations_with_capabilities(
        self, capabilities: List[Type], match_all: bool = True
    ) -> List[str]:
        """
        Get integrations that support multiple capabilities.

        Args:
            capabilities: List of capability protocol classes
            match_all: If True, integration must support ALL capabilities.
                      If False, integration must support AT LEAST ONE capability.

        Returns:
            List of integration names matching the capability criteria
        """
        result = []
        for name in self._integrations.keys():
            if match_all:
                # Integration must support ALL capabilities
                if all(self.supports_capability(name, cap) for cap in capabilities):
                    result.append(name)
            else:
                # Integration must support AT LEAST ONE capability
                if any(self.supports_capability(name, cap) for cap in capabilities):
                    result.append(name)
        return result

    def get_integration_capabilities(self, integration_name: str) -> List[str]:
        """
        Get all capabilities supported by an integration.

        Args:
            integration_name: Integration name

        Returns:
            List of capability names (e.g., ['IVariableStore', 'ISecretStore'])
        """
        integration = self._integrations.get(integration_name)
        if integration is None:
            return []

        # Check explicit CAPABILITIES declaration first
        if hasattr(integration, "CAPABILITIES"):
            return [get_capability_name(cap) for cap in integration.CAPABILITIES]

        # Fall back to checking all known capabilities
        capabilities = []
        for capability in [
            IVariableStore,
            ISecretStore,
            IFeatureStore,
            IKVStore,
            IRepositoryTool,
            IInfrastructureTool,
        ]:
            if isinstance(integration, capability):
                capabilities.append(get_capability_name(capability))

        return capabilities

    def get_capability_matrix(self) -> Dict[str, List[str]]:
        """
        Get a matrix of integrations and their supported capabilities.

        Returns:
            Dictionary mapping integration names to lists of capability names
        """
        return {
            name: self.get_integration_capabilities(name)
            for name in self._integrations.keys()
        }

    def list_capabilities(self) -> List[Dict[str, Any]]:
        """
        List all available capabilities with metadata.

        Returns:
            List of dictionaries with capability information
        """
        return [
            {
                "name": name,
                "description": info.get("description", ""),
                "methods": info.get("methods", []),
                "examples": info.get("examples", []),
            }
            for name, info in CAPABILITY_REGISTRY.items()
        ]

    # Requirement registration methods

    def register_requirement(self, operation: str, integrations: List[str]) -> None:
        """
        Register integration requirements for an operation.

        Args:
            operation: Operation name (e.g., 'gitops_fetch', 'terraform_provision')
            integrations: List of required integration names
        """
        if operation not in self._requirements:
            self._requirements[operation] = set()
        self._requirements[operation].update(integrations)
        logger.debug(
            "Integration requirements registered",
            operation=operation,
            integrations=integrations,
        )

    def get_requirements(self, operation: str) -> Set[str]:
        """
        Get integration requirements for an operation.

        Args:
            operation: Operation name

        Returns:
            Set of required integration names
        """
        return self._requirements.get(operation, set())

    def validate_operation(self, operation: str) -> Tuple[bool, List[str]]:
        """
        Validate that all required integrations are available for an operation.

        Args:
            operation: Operation name

        Returns:
            Tuple of (success, list of error messages)
        """
        errors = []

        # Check if operation is registered
        if operation not in self._requirements:
            errors.append(
                f"Operation '{operation}' is not registered. "
                f"This is likely a configuration error"
            )
            return False, errors

        required_integrations = self.get_requirements(operation)

        if not required_integrations:
            # Operation registered but no integrations required
            return True, []

        for integration_name in required_integrations:
            if not self.is_integration_registered(integration_name):
                errors.append(
                    f"Integration '{integration_name}' is required but not registered in registry"
                )
                continue

            if not self.is_integration_available(integration_name):
                integration = self._integrations[integration_name]
                integration_display = getattr(
                    integration, "integration_name", integration_name
                )
                errors.append(
                    f"Integration '{integration_display}' is required but not available"
                )

        return len(errors) == 0, errors

    def get_operations_for_integration(self, integration_name: str) -> List[str]:
        """
        Get operations that require a specific integration.

        Args:
            integration_name: Integration name

        Returns:
            List of operation names
        """
        operations = []
        for operation, integrations in self._requirements.items():
            if integration_name in integrations:
                operations.append(operation)
        return operations

    def list_operations(self) -> List[str]:
        """
        List all registered operations.

        Returns:
            List of operation names
        """
        return list(self._requirements.keys())
