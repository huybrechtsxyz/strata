#!/usr/bin/env python3
"""
===============================================================================
Script Name   : provider_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Provider service class
===============================================================================
"""

from typing import List, Optional, Tuple

from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.provider_model import ProviderModel
from xyz_platform.services.base_service import BaseService


class ProviderService(BaseService):
    """Service for handling provider configurations."""

    def __init__(self, path: str = None, data: dict = None):
        """Initialize the ProviderService."""
        super().__init__(path=path, data=data)
        self.model: Optional[ProviderModel] = None

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

        errors = []

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
            errors.append(
                f"Provider type '{provider_type}' not found in configuration. "
                f"Available providers: {[p.name for p in configuration_model.spec.providers] if configuration_model.spec.providers else []}"
            )
            return False, errors

        # Validate region if provider doesn't allow additional regions
        if not config_provider.additional_regions:
            if config_provider.regions is None or len(config_provider.regions) == 0:
                errors.append(
                    f"Provider '{provider_type}' has no regions defined in configuration, "
                    f"but additional_regions is False"
                )
                return False, errors

            # Extract region names (handle both string and dict formats)
            valid_regions = []
            for region in config_provider.regions:
                if isinstance(region, str):
                    valid_regions.append(region)
                elif isinstance(region, dict) and "name" in region:
                    valid_regions.append(region["name"])

            if provider_region not in valid_regions:
                errors.append(
                    f"Region '{provider_region}' is not valid for provider '{provider_type}'. "
                    f"Valid regions: {valid_regions}"
                )
                return False, errors

        return len(errors) == 0, errors

    # Service methods for accessing provider details

    def get_provider_type(self) -> str:
        """Get the provider type."""
        self._ensure_validated()
        return self.model.spec.properties.type

    def get_provider_region(self) -> str:
        """Get the provider region."""
        self._ensure_validated()
        return self.model.spec.properties.region
