"""Base class for store backend integrations (variables, secrets, feature flags)."""

from typing import Any, List, Optional

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.logger import get_logger
from xyz_platform.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class StoreIntegration(BaseIntegration):
    """
    Abstract base class for store backend integrations.

    Extends BaseIntegration with a unified store interface for accessing variables,
    secrets, and feature flags across different backend systems.

    Unified Store Interface:
    - get_variable(key, **kwargs) -> Optional[Any]: Retrieve variable values
    - set_variable(key, value, **kwargs) -> bool: Store variable values
    - list_variables(prefix, **kwargs) -> List[str]: List available variable keys
    - get_secret(key, **kwargs) -> Optional[str]: Retrieve secrets
    - set_secret(key, value, **kwargs) -> bool: Store secrets
    - list_secrets(prefix, **kwargs) -> List[str]: List available secret keys
    - get_feature(key, **kwargs) -> Optional[bool]: Retrieve feature flags
    - set_feature(key, value, **kwargs) -> bool: Store feature flags
    - list_features(prefix, **kwargs) -> List[str]: List available feature flag keys

    Implementation Guidelines:
    - Override only the methods your integration supports
    - Use **kwargs for integration-specific parameters (e.g., field, label, timeout)
    - Return None or False for unsupported operations (default behavior)
    - Log debug messages for unsupported operations (automatic)

    Examples:
        # Integration supporting only secrets (e.g., Azure KeyVault)
        class KeyVaultIntegration(StoreIntegration):
            def get_secret(self, key: str, **kwargs) -> Optional[str]:
                return self._get_secret_from_vault(key)

        # Integration supporting multiple types (e.g., Azure App Configuration)
        class AppConfigIntegration(StoreIntegration):
            def get_variable(self, key: str, **kwargs) -> Optional[str]:
                return self._get_value(key, kwargs.get('label'))

            def get_feature(self, key: str, **kwargs) -> Optional[bool]:
                flag = self._get_flag(key, kwargs.get('label'))
                return flag.get('enabled') if flag else None
    """

    def __init__(self, config: IntegrationModel):
        """Initialize store integration with config."""
        super().__init__(config)

    # Variable operations

    def get_variable(self, key: str, **kwargs) -> Optional[Any]:
        """
        Get a variable value from the store.

        Override this method in subclasses that support variable storage.

        Args:
            key: Variable key
            **kwargs: Additional integration-specific arguments

        Returns:
            Variable value or None if not supported/not found
        """
        logger.debug(
            "Store does not support variable operations", name=self.integration_name, operation="get_variable", key=key
        )
        return None

    def set_variable(self, key: str, value: Any, **kwargs) -> bool:
        """
        Set a variable value in the store.

        Override this method in subclasses that support variable storage.

        Args:
            key: Variable key
            value: Variable value
            **kwargs: Additional integration-specific arguments

        Returns:
            True if successful, False if not supported/failed
        """
        logger.debug(
            "Store does not support variable operations", name=self.integration_name, operation="set_variable", key=key
        )
        return False

    def list_variables(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available variable keys in the store.

        Override this method in subclasses that support listing variables.

        Args:
            prefix: Optional prefix to filter keys
            **kwargs: Additional integration-specific arguments

        Returns:
            List of variable keys, empty list if not supported/failed
        """
        logger.debug(
            "Store does not support listing variables",
            name=self.integration_name,
            operation="list_variables",
            prefix=prefix,
        )
        return []

    # Secret operations

    def get_secret(self, key: str, **kwargs) -> Optional[str]:
        """
        Get a secret value from the store.

        Override this method in subclasses that support secret storage.

        Args:
            key: Secret key
            **kwargs: Additional integration-specific arguments

        Returns:
            Secret value or None if not supported/not found
        """
        logger.debug(
            "Store does not support secret operations", name=self.integration_name, operation="get_secret", key=key
        )
        return None

    def set_secret(self, key: str, value: str, **kwargs) -> bool:
        """
        Set a secret value in the store.

        Override this method in subclasses that support secret storage.

        Args:
            key: Secret key
            value: Secret value
            **kwargs: Additional integration-specific arguments

        Returns:
            True if successful, False if not supported/failed
        """
        logger.debug(
            "Store does not support secret operations", name=self.integration_name, operation="set_secret", key=key
        )
        return False

    def list_secrets(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available secret keys in the store.

        Override this method in subclasses that support listing secrets.

        Args:
            prefix: Optional prefix/path to filter keys
            **kwargs: Additional integration-specific arguments

        Returns:
            List of secret keys, empty list if not supported/failed
        """
        logger.debug(
            "Store does not support listing secrets",
            name=self.integration_name,
            operation="list_secrets",
            prefix=prefix,
        )
        return []

    # Feature flag operations

    def get_feature(self, key: str, **kwargs) -> Optional[bool]:
        """
        Get a feature flag value from the store.

        Override this method in subclasses that support feature flags.

        Args:
            key: Feature flag key
            **kwargs: Additional integration-specific arguments

        Returns:
            Feature flag value (bool) or None if not supported/not found
        """
        logger.debug(
            "Store does not support feature flag operations",
            name=self.integration_name,
            operation="get_feature",
            key=key,
        )
        return None

    def set_feature(self, key: str, value: bool, **kwargs) -> bool:
        """
        Set a feature flag value in the store.

        Override this method in subclasses that support feature flags.

        Args:
            key: Feature flag key
            value: Feature flag value (bool)
            **kwargs: Additional integration-specific arguments

        Returns:
            True if successful, False if not supported/failed
        """
        logger.debug(
            "Store does not support feature flag operations",
            name=self.integration_name,
            operation="set_feature",
            key=key,
        )
        return False

    def list_features(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available feature flag keys in the store.

        Override this method in subclasses that support listing feature flags.

        Args:
            prefix: Optional prefix to filter keys
            **kwargs: Additional integration-specific arguments

        Returns:
            List of feature flag keys, empty list if not supported/failed
        """
        logger.debug(
            "Store does not support listing feature flags",
            name=self.integration_name,
            operation="list_features",
            prefix=prefix,
        )
        return []

    # Key-value operations (generic)

    def get_kv(self, key: str, **kwargs) -> Optional[Any]:
        """
        Get a generic key-value pair from the store.

        Override this method in subclasses that support generic KV storage.

        Args:
            key: Key
            **kwargs: Additional integration-specific arguments

        Returns:
            Value or None if not supported/not found
        """
        logger.debug(
            "Store does not support key-value operations", name=self.integration_name, operation="get_kv", key=key
        )
        return None

    def set_kv(self, key: str, value: Any, **kwargs) -> bool:
        """
        Set a generic key-value pair in the store.

        Override this method in subclasses that support generic KV storage.

        Args:
            key: Key
            value: Value
            **kwargs: Additional integration-specific arguments

        Returns:
            True if successful, False if not supported/failed
        """
        logger.debug(
            "Store does not support key-value operations", name=self.integration_name, operation="set_kv", key=key
        )
        return False

    def list_kv(self, prefix: str = "", **kwargs) -> List[str]:
        """
        List available keys in the store.

        Override this method in subclasses that support listing KV pairs.

        Args:
            prefix: Optional prefix to filter keys
            **kwargs: Additional integration-specific arguments

        Returns:
            List of keys, empty list if not supported/failed
        """
        logger.debug(
            "Store does not support key-value operations",
            name=self.integration_name,
            operation="list_kv",
            prefix=prefix,
        )
        return []
