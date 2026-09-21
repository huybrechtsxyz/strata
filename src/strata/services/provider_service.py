#!/usr/bin/env python3
"""Service for loading and validating provider configurations."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.models.provider_model import ProviderModel
from strata.services.base_service import BaseService


class ProviderService(BaseService[ProviderModel]):
    """Service for handling provider configurations.

    Phase 2 here only checks that `spec.properties.type` is a *registered*
    provider type name (a pointer existing in `configuration_model.spec.providers`)
    — `configuration_model.spec.providers` entries are just `{name, file}`
    pointers to standalone `ProviderConfigModel` documents now (ADR-0014), so
    the deep region/resource check needs an actually-loaded `ProviderConfigModel`,
    which `_validate_dynamic()`'s fixed signature has no slot for. That check
    is `validate_against_provider_config()` below — a caller resolves the
    pointer's file itself and calls it once loaded (same "assume the caller
    already loaded it" shape as `WorkspaceService.validate_topology_components()`).
    """

    def _get_model_class(self) -> type[ProviderModel]:
        """Return the ProviderModel class for validation."""
        return ProviderModel

    def _validate_dynamic(self, configuration_model: ConfigurationModel | None = None) -> tuple[bool, list[str]]:
        """Phase 2: check that `spec.properties.type` is a registered provider type name.

        Skipped (returns valid) when no `configuration_model` is supplied.
        """
        if configuration_model is None:
            return True, []
        if self.model is None:
            return False, ["Provider model is not initialized"]

        provider_type = self.model.spec.properties.type
        registered_names = {p.name for p in configuration_model.spec.providers} if configuration_model.spec.providers else set()

        if provider_type not in registered_names:
            available = sorted(registered_names)
            return False, [f"Provider type '{provider_type}' not found in configuration. Available: {available}"]

        return True, []

    def validate_against_provider_config(self, provider_config: ProviderConfigModel) -> tuple[bool, list[str]]:
        """Cross-check `spec.properties.region` against a loaded ProviderConfig document's regions.

        Args:
            provider_config: The already-loaded `ProviderConfigModel` document
                that `configuration_model.spec.providers[].file` pointed at.
        """
        if self.model is None:
            return False, ["Provider model is not initialized"]

        provider_type = self.model.spec.properties.type
        provider_region = self.model.spec.properties.region
        spec = provider_config.spec

        if not spec.additional_regions:
            if not spec.regions:
                return False, [
                    f"Provider '{provider_type}' has no regions defined in its provider config "
                    f"and additional_regions is False"
                ]

            valid_regions = [r if isinstance(r, str) else str(r.get("name", str(r))) for r in spec.regions]

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
