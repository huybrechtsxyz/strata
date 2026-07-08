"""Factory for creating integration instances from IntegrationModel configuration."""

from typing import Dict, List, Type

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

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

    # Aliases registered in _type_mapping for backwards-compat with YAML configs
    # that use short/hyphenated names (e.g. type: consul). These are excluded from
    # get_known_types() so status output only shows canonical names.
    _BUILTIN_ALIASES: set = {"azure-keyvault", "azure-appconfig", "consul", "vault"}

    # Built-in class map: type string -> (module_path, class_name)
    # This is the canonical source of truth for all built-in integrations.
    # Custom integrations registered at runtime appear in _type_mapping instead.
    _BUILTIN_CLASS_MAP: Dict[str, tuple] = {
        "ansible": ("strata.integrations.ansible", "AnsibleIntegration"),
        "azure_appconfig": ("strata.integrations.azure_appconfig", "AzureAppConfigIntegration"),
        "azure_keyvault": ("strata.integrations.azure_keyvault", "AzureKeyVaultIntegration"),
        "bitwarden": ("strata.integrations.bitwarden", "BitwardenIntegration"),
        "cve_scanner": ("strata.integrations.cve_scanner", "CveScannerIntegration"),
        "docker": ("strata.integrations.docker", "DockerIntegration"),
        "etcd": ("strata.integrations.etcd", "EtcdIntegration"),
        "flagsmith": ("strata.integrations.flagsmith", "FlagsmithIntegration"),
        "git": ("strata.integrations.git", "GitIntegration"),
        "hashicorp_consul": ("strata.integrations.hashicorp_consul", "ConsulIntegration"),
        "hashicorp_vault": ("strata.integrations.hashicorp_vault", "VaultIntegration"),
        "infisical": ("strata.integrations.infisical", "InfisicalIntegration"),
        "openbao": ("strata.integrations.openbao", "OpenBaoIntegration"),
        "helm": ("strata.integrations.helm", "HelmIntegration"),
        "opentofu": ("strata.integrations.opentofu", "OpenTofuIntegration"),
        "terraform": ("strata.integrations.terraform", "TerraformIntegration"),
        # SIEM / audit sinks
        "sentinel": ("strata.integrations.siem.sentinel_integration", "SentinelIntegration"),
        "elk": ("strata.integrations.siem.elk_siem_integration", "ElkSiemIntegration"),
        "otel": ("strata.integrations.siem.otel_siem_integration", "OtelSiemIntegration"),
        "splunk": ("strata.integrations.siem.splunk_siem_integration", "SplunkSiemIntegration"),
    }

    @classmethod
    def register_type(cls, integration_type: str, integration_class: Type[BaseIntegration]):
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

        # Check if type is registered; JIT-load from built-in class map if not
        if integration_type not in cls._type_mapping:
            if integration_type in cls._BUILTIN_CLASS_MAP:
                import importlib as _importlib

                module_path, class_name = cls._BUILTIN_CLASS_MAP[integration_type]
                try:
                    module = _importlib.import_module(module_path)
                    cls.register_type(integration_type, getattr(module, class_name))
                except Exception as _e:
                    logger.error(
                        "Failed to load built-in integration",
                        type=integration_type,
                        error=str(_e),
                        exc_info=True,
                    )
                    raise ValueError(f"Integration type '{integration_type}' could not be loaded: {_e}") from _e
            else:
                logger.error(
                    "Unknown integration type",
                    type=integration_type,
                    available=list(cls._type_mapping.keys()),
                )
                raise ValueError(
                    f"Integration type '{integration_type}' is not registered. "
                    f"Available types: {', '.join(cls.get_known_types())}"
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

    @classmethod
    def get_known_types(cls) -> List[str]:
        """Return all known integration type strings — built-in and custom-registered.

        Built-in types come from ``_BUILTIN_CLASS_MAP``.
        Custom types registered at runtime (e.g. from ``.strata/integrations/``)
        come from ``_type_mapping``, excluding known aliases.
        The union is returned sorted.
        """
        custom_registered = set(cls._type_mapping.keys()) - cls._BUILTIN_ALIASES
        return sorted(set(cls._BUILTIN_CLASS_MAP.keys()) | custom_registered)

    @classmethod
    def is_known_type(cls, type_str: str) -> bool:
        """Return True if *type_str* is a known built-in or registered custom type."""
        return type_str in cls._BUILTIN_CLASS_MAP or type_str in cls._type_mapping

    @classmethod
    def create_by_type(cls, type_str: str) -> BaseIntegration:
        """
        Create a minimal integration instance by friendly type name.

        Intended for status and availability checks — does not require a
        workspace config.  Creates a bare ``IntegrationModel`` with just the
        type string and delegates to the registered class.

        Args:
            type_str: Friendly integration type (e.g. "git", "hashicorp_vault")

        Returns:
            Integration instance

        Raises:
            ValueError: If the type is not known
        """
        import importlib as _importlib

        # Try the registered type_mapping first (supports custom / aliased types)
        if type_str in cls._type_mapping:
            integration_class = cls._type_mapping[type_str]
            config = IntegrationModel(name=type_str, type=type_str)
            return integration_class(config=config)

        # Fall back to the built-in class map
        if type_str in cls._BUILTIN_CLASS_MAP:
            module_path, class_name = cls._BUILTIN_CLASS_MAP[type_str]
            module = _importlib.import_module(module_path)
            integration_class = getattr(module, class_name)
            config = IntegrationModel(name=type_str, type=type_str)
            return integration_class(config=config)

        raise ValueError(f"Unknown integration type: '{type_str}'. Known types: {', '.join(cls.get_known_types())}")


# Auto-registration of built-in integration types
# This happens at module import time


def _auto_register_builtin_integrations():
    """
    Auto-register built-in integration types.

    Derives the registration list directly from ``_BUILTIN_CLASS_MAP`` so the
    two data structures can never diverge.  After registering canonical names,
    registers backwards-compatible aliases so existing YAML configs that use
    short or hyphenated names (e.g. ``type: vault``) continue to work.

    Failures are logged but don’t prevent platform startup.
    """
    import importlib as _importlib

    # Register every canonical built-in type
    for integration_type, (module_path, class_name) in IntegrationFactory._BUILTIN_CLASS_MAP.items():
        try:
            module = _importlib.import_module(module_path)
            integration_class = getattr(module, class_name)
            IntegrationFactory.register_type(integration_type, integration_class)
            logger.debug("Built-in integration auto-registered", type=integration_type, cls=class_name)
        except ImportError as e:
            logger.debug(
                "Built-in integration not available (not yet implemented)", type=integration_type, error=str(e)
            )
        except Exception as e:
            logger.warning(
                "Failed to auto-register built-in integration", type=integration_type, error=str(e), exc_info=True
            )

    # Register backwards-compatible aliases (hyphenated/short names used in older YAML configs)
    _aliases = {
        "azure-keyvault": "azure_keyvault",
        "azure-appconfig": "azure_appconfig",
        "consul": "hashicorp_consul",
        "vault": "hashicorp_vault",
    }
    for alias, canonical in _aliases.items():
        if canonical in IntegrationFactory._type_mapping:
            IntegrationFactory._type_mapping[alias] = IntegrationFactory._type_mapping[canonical]
            logger.debug("Integration alias registered", alias=alias, canonical=canonical)


# Run auto-registration on module import
_auto_register_builtin_integrations()
