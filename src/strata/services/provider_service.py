#!/usr/bin/env python3
"""Service for loading and validating provider configurations."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_model import ProviderModel
from strata.services.base_service import BaseService


class ProviderService(BaseService[ProviderModel]):
    """Service for handling provider configurations."""

    def _get_model_class(self) -> type[ProviderModel]:
        """Return the ProviderModel class for validation."""
        return ProviderModel

    def _validate_dynamic(self, configuration_model: ConfigurationModel | None = None) -> tuple[bool, list[str]]:
        """Phase 2: cross-check `spec.properties.type`/`region` against the
        configuration registry's provider entries.

        Skipped (returns valid) when no `configuration_model` is supplied.
        """
        if configuration_model is None:
            return True, []
        if self.model is None:
            return False, ["Provider model is not initialized"]

        provider_type = self.model.spec.properties.type
        provider_region = self.model.spec.properties.region

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

        if not config_provider.additional_regions:
            if not config_provider.regions:
                return False, [
                    f"Provider '{provider_type}' has no regions defined in configuration "
                    f"and additional_regions is False"
                ]

            valid_regions = [
                r if isinstance(r, str) else str(r.get("name", str(r))) for r in config_provider.regions
            ]

            if provider_region not in valid_regions:
                return False, [
                    f"Region '{provider_region}' is not valid for provider '{provider_type}'. "
                    f"Valid regions: {valid_regions}"
                ]

        return True, []

    def get_provider_type(self) -> str:
        """Return the provider's cloud/infrastructure type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.type

    def get_provider_region(self) -> str:
        """Return the provider's region."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.region
