#!/usr/bin/env python3
"""Service for loading and validating provider configurations."""

from typing import List, Optional, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_model import ProviderModel
from strata.services.base_service import BaseService


class ProviderService(BaseService["ProviderModel"]):
    """Service for handling provider configurations."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the ProviderService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the ProviderModel class for validation."""
        return ProviderModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        This method performs business logic validation that requires dynamic configuration,
        such as validating provider types and regions against the configuration model.

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if configuration_model is None:
            # No configuration provided, skip dynamic validation
            return True, []

        if self.model is None:
            return False, ["Provider model is not initialized"]

        # Get provider type and region from this provider
        provider_type = self.model.spec.properties.type
        provider_region = self.model.spec.properties.region

        # Find matching provider configuration
        config_provider = None
        if configuration_model.spec.providers:
            for provider in configuration_model.spec.providers:
                if provider.name == provider_type:
                    config_provider = provider
                    break

        if config_provider is None:
            available = (
                [p.name for p in configuration_model.spec.providers] if configuration_model.spec.providers else []
            )
            return False, [f"Provider type '{provider_type}' not found in configuration. Available: {available}"]

        # Validate region when the provider does not allow arbitrary regions
        if not config_provider.additional_regions:
            if not config_provider.regions:
                return False, [
                    f"Provider '{provider_type}' has no regions defined in configuration "
                    f"and additional_regions is False"
                ]

            valid_regions = [
                r if isinstance(r, str) else r.get("name", str(r)) if isinstance(r, dict) else str(r)
                for r in config_provider.regions
            ]

            if provider_region not in valid_regions:
                return False, [
                    f"Region '{provider_region}' is not valid for provider '{provider_type}'. "
                    f"Valid regions: {valid_regions}"
                ]

        return True, []

    # Service methods for accessing provider details

    def get_provider_type(self) -> str:
        """Get the provider type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.type

    def get_provider_region(self) -> str:
        """Get the provider region."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.region
