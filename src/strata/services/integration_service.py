"""Service for managing platform integrations (Centralized Singleton pattern).

Coordinates integration loading, registration, and access. Bridges the
configuration layer and runtime integration instances.

Usage::

    service = IntegrationService.get_instance()
    service.initialize_integrations()
    git = service.get_integration("git")
    stores = service.get_integrations_with_capability(ISecretStore)

    # Scope the required-integration availability check to the capability the
    # caller actually needs (ADR-0069) -- avoids failing on an unrelated
    # required integration (e.g. `terraform`) when the caller only needs, say,
    # `IIdentityProvider`. Pass ``capabilities=None`` for the unscoped,
    # check-everything behavior.
    ok, errors = service.validate_required_integrations(capabilities={ISecretStore})
"""

import threading
from typing import List, Optional, Set, Tuple, Type

from strata.logger import get_logger

logger = get_logger(__name__)


class IntegrationService:
    """Service for managing platform integrations (Centralized Singleton pattern)."""

    _instance: Optional["IntegrationService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Create or return existing singleton instance (thread-safe)."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cls._instance

    def __init__(self):
        """Initialize integration service (only once)."""
        if self._initialized:
            return

        self._initialized = True
        self._integrations_loaded = False

        # Lazy import to avoid circular dependencies
        from strata.integrations.registry import IntegrationRegistry
        from strata.services.configuration_service import ConfigurationService

        self.config_service = ConfigurationService.get_instance()
        self.registry = IntegrationRegistry.get_instance()

        logger.debug("IntegrationService initialized")

    @classmethod
    def get_instance(cls) -> "IntegrationService":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset(cls):
        """Reset singleton instance (useful for testing)."""
        with cls._lock:
            if cls._instance:
                cls._instance._integrations_loaded = False
            cls._instance = None
            # Also reset registry
            from strata.integrations.registry import IntegrationRegistry

            IntegrationRegistry.reset()

    # Integration lifecycle methods

    def initialize_integrations(self, force_reload: bool = False) -> Tuple[bool, List[str]]:
        """
        Load and register integrations from configuration.

        This is the main entry point for setting up the integration system.
        Should be called once at platform startup.

        Args:
            force_reload: If True, reload even if already loaded

        Returns:
            Tuple of (success, list of error messages)
        """
        if self._integrations_loaded and not force_reload:
            logger.debug("Integrations already loaded")
            return True, []

        logger.info("Initializing platform integrations")
        errors = []

        # Ensure configuration is loaded
        if not self.config_service.is_validated():
            logger.warning("Configuration not validated, attempting to load")
            # ConfigurationService should be loaded before IntegrationService
            errors.append("Configuration must be loaded before initializing integrations")
            return False, errors

        # Get integration specs from configuration
        config_model = self.config_service.get_model()
        if not config_model or not config_model.spec.integrations:
            logger.warning("No integrations defined in configuration")
            self._integrations_loaded = True
            return True, []

        integration_specs = config_model.spec.integrations
        logger.info("Loading integrations", count=len(integration_specs))

        # Import factory here to avoid circular import
        from strata.integrations.factory import IntegrationFactory

        # Load each integration
        loaded_count = 0
        for spec in integration_specs:
            # Skip disabled integrations
            if not spec.enabled:
                logger.debug("Skipping disabled integration", name=spec.name, type=spec.type)
                continue

            try:
                # Create integration instance from spec
                integration = IntegrationFactory.create(spec)

                # Register in registry
                self.registry.register_integration(spec.name, integration)

                logger.debug(
                    "Loaded integration",
                    name=spec.name,
                    type=spec.type,
                    capabilities=spec.capabilities,
                )
                loaded_count += 1

            except Exception as e:
                logger.error(
                    "Integration load failed",
                    name=spec.name,
                    type=spec.type,
                    error=str(e),
                    exc_info=True,
                )

                # Required integrations cause failure
                if spec.required:
                    errors.append(f"Failed to load integration '{spec.name}': {str(e)}")
                else:
                    logger.warning("Optional integration failed", name=spec.name, error=str(e))

        logger.info("Integration initialization complete", loaded=loaded_count, total=len(integration_specs))

        self._integrations_loaded = True

        # NOTE (ADR-0069): required-integration *availability* validation is
        # deliberately NOT run here anymore. This method is a process-wide
        # singleton gate (`_integrations_loaded`) — running an unscoped
        # `validate_required_integrations()` here would only ever check
        # whatever capability the *first* caller in the process happened to
        # need, silently skipping the check for every other caller for the
        # rest of the process lifetime. Callers that need the required-
        # integration check must call `validate_required_integrations()`
        # explicitly with the capability set they actually care about (or
        # `None` for the full, unscoped check — e.g. a future "check
        # everything" report).
        return len(errors) == 0, errors

    def validate_required_integrations(self, capabilities: Optional[Set[Type]] = None) -> Tuple[bool, List[str]]:
        """
        Validate that required integrations are loaded and available.

        Args:
            capabilities: Optional set of capability protocol classes (e.g.
                ``{IIdentityProvider}``). When provided, only ``required: true``
                specs whose declared ``spec.capabilities`` intersect this set
                are checked — scoping the check to what the calling command
                actually needs (ADR-0069). ``None`` checks every required
                integration regardless of capability (the original, unscoped
                behavior).

        Returns:
            Tuple of (success, list of error messages)
        """
        logger.debug("Validating required integrations", capabilities=capabilities)
        errors = []

        # Get required integrations from config
        config_model = self.config_service.get_model()
        if not config_model or not config_model.spec.integrations:
            return True, []

        required_integrations = [spec for spec in config_model.spec.integrations if spec.required and spec.enabled]

        if capabilities is not None:
            from strata.models.capabilities import get_capability_protocol

            required_integrations = [
                spec
                for spec in required_integrations
                if any(get_capability_protocol(name) in capabilities for name in (spec.capabilities or []))
            ]

        logger.debug("Checking required integrations", count=len(required_integrations))

        for spec in required_integrations:
            # Check if registered
            if not self.registry.is_integration_registered(spec.name):
                error_msg = f"Required integration '{spec.name}' is not registered"
                errors.append(error_msg)
                logger.error("Required integration missing", name=spec.name)
                continue

            # Check if available
            if not self.registry.is_integration_available(spec.name):
                # integration = self.registry.get_integration(spec.name)
                error_msg = (
                    f"Required integration '{spec.name}' is not available. "
                    f"Please install {spec.type} and ensure it's in your PATH."
                )
                errors.append(error_msg)
                logger.error("Required integration not available", name=spec.name, type=spec.type)

        if errors:
            logger.warning("Required integration validation failed", error_count=len(errors))
        else:
            logger.info("All required integrations validated successfully")

        return len(errors) == 0, errors

    # Integration access methods

    def get_integration(self, name: str):
        """
        Get integration instance by name.

        Args:
            name: Integration name (from config)

        Returns:
            Integration instance or None if not found
        """
        return self.registry.get_integration(name)

    def get_integrations_with_capability(self, capability: Type) -> List[str]:
        """
        Get all integration names that support a capability.

        Args:
            capability: Capability protocol class (e.g., ISecretStore)

        Returns:
            List of integration names
        """
        return self.registry.get_integrations_with_capability(capability)

    def get_integration_with_capability(self, capability: Type):
        """
        Get first available integration that supports a capability.

        Args:
            capability: Capability protocol class

        Returns:
            Integration instance or None
        """
        integrations = self.get_integrations_with_capability(capability)
        if not integrations:
            logger.warning("No integrations found with capability", capability=capability.__name__)
            return None

        # Return first available integration
        for name in integrations:
            integration = self.get_integration(name)
            if integration and self.registry.is_integration_available(name):
                return integration

        logger.warning(
            "No available integrations found with capability", capability=capability.__name__, checked=len(integrations)
        )
        return None

    def is_integration_available(self, name: str) -> bool:
        """
        Check if integration is available.

        Args:
            name: Integration name

        Returns:
            True if integration is registered and available
        """
        return self.registry.is_integration_available(name)

    def list_integrations(self) -> List[str]:
        """
        List all registered integration names.

        Returns:
            List of integration names
        """
        return self.registry.list_integrations()

    def get_integration_status(self) -> dict:
        """
        Get status of all registered integrations.

        Returns:
            Dictionary with integration status information
        """
        return self.registry.get_integration_status()

    def get_capability_matrix(self) -> dict:
        """
        Get matrix of integrations and their capabilities.

        Returns:
            Dictionary mapping integration names to capability lists
        """
        return self.registry.get_capability_matrix()

    # State query methods

    def is_initialized(self) -> bool:
        """Check if integrations have been loaded."""
        return self._integrations_loaded

    def get_info(self) -> dict:
        """
        Get service information.

        Returns:
            Dictionary with service state and statistics
        """
        return {
            "initialized": self._integrations_loaded,
            "integration_count": len(self.list_integrations()),
            "integrations": self.list_integrations(),
            "status": self.get_integration_status(),
        }
