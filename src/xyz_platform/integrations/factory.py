"""Factory for creating integration instances from IntegrationModel configuration."""

from typing import Dict, Type

from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.logger import get_logger

logger = get_logger(__name__)


class IntegrationFactory:
    """
    Factory for creating integration instances from configuration.

    Maps integration types to concrete integration classes and handles
    instantiation with proper configuration.
    """

    # Type mapping: integration type -> integration class
    # This will be populated as integrations are created
    _type_mapping: Dict[str, Type[BaseIntegration]] = {}

    @classmethod
    def register_type(
        cls, integration_type: str, integration_class: Type[BaseIntegration]
    ):
        """
        Register an integration type mapping.

        Args:
            integration_type: Type string from config (e.g., "git", "terraform")
            integration_class: Integration class to instantiate
        """
        cls._type_mapping[integration_type] = integration_class
        logger.debug("Integration type registered", type=integration_type, cls=integration_class.__name__)

    @classmethod
    def unregister_type(cls, integration_type: str):
        """
        Unregister an integration type mapping.

        Args:
            integration_type: Type string to remove
        """
        if integration_type in cls._type_mapping:
            del cls._type_mapping[integration_type]
            logger.debug("Integration type unregistered", type=integration_type)

    @classmethod
    def create(cls, config: IntegrationModel) -> BaseIntegration:
        """
        Create integration instance from configuration.

        Args:
            config: Integration configuration model

        Returns:
            Integration instance

        Raises:
            ValueError: If integration type is not registered
            Exception: If integration instantiation fails
        """
        integration_type = config.type

        logger.debug("Creating integration", name=config.name, type=integration_type)

        # Check if type is registered
        if integration_type not in cls._type_mapping:
            logger.error(
                "Unknown integration type",
                type=integration_type,
                available=list(cls._type_mapping.keys()),
            )
            raise ValueError(
                f"Integration type '{integration_type}' is not registered. "
                f"Available types: {', '.join(cls._type_mapping.keys())}"
            )

        # Get integration class
        integration_class = cls._type_mapping[integration_type]

        try:
            # Instantiate integration with config
            integration = integration_class(config)

            logger.info(
                "Integration created",
                name=config.name,
                type=integration_type,
                cls=integration_class.__name__,
            )

            return integration

        except Exception as e:
            logger.error(
                "Failed to create integration",
                name=config.name,
                type=integration_type,
                cls=integration_class.__name__,
                error=str(e),
                exc_info=True,
            )
            raise

    @classmethod
    def get_registered_types(cls) -> Dict[str, Type[BaseIntegration]]:
        """
        Get all registered integration type mappings.

        Returns:
            Dictionary of type string to integration class
        """
        return dict(cls._type_mapping)

    @classmethod
    def is_type_registered(cls, integration_type: str) -> bool:
        """
        Check if integration type is registered.

        Args:
            integration_type: Type string

        Returns:
            True if type is registered
        """
        return integration_type in cls._type_mapping

    @classmethod
    def reset(cls):
        """Reset factory (useful for testing)."""
        cls._type_mapping.clear()
        logger.debug("Integration factory reset")


# Auto-registration of built-in integration types
# This happens at module import time


def _auto_register_builtin_integrations():
    """
    Auto-register built-in integration types.

    This function attempts to import and register standard integrations.
    Failures are logged but don't prevent platform startup.
    """
    builtin_integrations = [
        ("git", "xyz_platform.integrations.git", "GitIntegration"),
        ("docker", "xyz_platform.integrations.docker", "DockerIntegration"),
        ("terraform", "xyz_platform.integrations.terraform", "TerraformIntegration"),
        ("bitwarden", "xyz_platform.integrations.bitwarden", "BitwardenIntegration"),
        (
            "azure-keyvault",
            "xyz_platform.integrations.azure_keyvault",
            "AzureKeyVaultIntegration",
        ),
        (
            "azure-appconfig",
            "xyz_platform.integrations.azure_appconfig",
            "AzureAppConfigIntegration",
        ),
        ("consul", "xyz_platform.integrations.consul", "ConsulIntegration"),
        ("vault", "xyz_platform.integrations.vault", "VaultIntegration"),
        # Add more as they are implemented
    ]

    for integration_type, module_path, class_name in builtin_integrations:
        try:
            # Dynamic import
            import importlib

            module = importlib.import_module(module_path)
            integration_class = getattr(module, class_name)

            # Register type
            IntegrationFactory.register_type(integration_type, integration_class)

            logger.debug("Built-in integration auto-registered", type=integration_type, cls=class_name)

        except ImportError as e:
            logger.debug("Built-in integration not available (not yet implemented)", type=integration_type, error=str(e))
        except Exception as e:
            logger.warning("Failed to auto-register built-in integration", type=integration_type, error=str(e), exc_info=True)


# Run auto-registration on module import
_auto_register_builtin_integrations()
