#!/usr/bin/env python3
"""Service for loading and validating resource configurations."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.models.resource_model import ResourceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics


class ResourceService(BaseService[ResourceModel]):
    """Service for handling resource configurations.

    Phase 2 here only checks that `spec.provider_type` is a
    *registered* provider type name (a pointer existing in
    `configuration_model.spec.providers`) — same reasoning as
    `ProviderService` (ADR-0014): `configuration_model.spec.providers`
    entries are `{name, file}` pointers now, so the deeper resource-type
    check needs an actually-loaded `ProviderConfigModel`, which
    `_validate_dynamic()`'s fixed signature has no slot for. That check
    is `validate_against_provider_config()` below.
    """

    def _get_model_class(self) -> type[ResourceModel]:
        """Return the ResourceModel class for validation."""
        return ResourceModel

    def _validate_dynamic(self, configuration_model: ConfigurationModel | None = None) -> Diagnostics:
        """Phase 2: check that `spec.provider_type` is a registered provider type name."""
        diagnostics = Diagnostics()
        if configuration_model is None or self.model is None:
            return diagnostics

        provider_type = self.model.spec.provider_type
        registered_names = set(configuration_model.spec.providers or [])

        if provider_type not in registered_names:
            available = sorted(registered_names)
            diagnostics.error(
                f"Provider type '{provider_type}' not found in configuration. Available providers: {available}",
                location="spec.provider_type",
                code="unregistered_provider_type",
            )

        return diagnostics

    def validate_against_provider_config(self, provider_config: ProviderConfigModel) -> Diagnostics:
        """Cross-check the resource type against a loaded ProviderConfig document:
        does it exist for this provider (when `additional_resources=False`)?

        Configuration-shape validation (the old `resource.spec.configuration`
        vs. a `providerconfig.spec.resources[].configuration` schema) was
        retired in favor of `attributeconfig` documents (docs/design/
        dynamic-configuration-validation.md, D5 option c) — this is now only
        the name-registry half of the old check.

        Args:
            provider_config: The already-loaded `ProviderConfigModel` document
                named by `configuration_model.spec.providers[]`.
        """
        diagnostics = Diagnostics()
        if self.model is None:
            diagnostics.error("Resource model is not initialized")
            return diagnostics

        provider_type = self.model.spec.provider_type
        resource_type = self.model.spec.resource_type
        spec = provider_config.spec

        if not spec.additional_resources:
            if not spec.resources:
                diagnostics.error(
                    f"Provider '{provider_type}' has no resources defined in its provider config, "
                    f"but additional_resources is False",
                    location="spec.resource_type",
                    code="no_resources_defined",
                )
                return diagnostics

            valid_resource_types = [res.name for res in spec.resources]
            if resource_type not in valid_resource_types:
                diagnostics.error(
                    f"Resource type '{resource_type}' is not valid for provider '{provider_type}'. "
                    f"Valid resource types: {valid_resource_types}",
                    location="spec.resource_type",
                    code="invalid_resource_type",
                )
                return diagnostics

        return diagnostics

    def get_provider_type(self) -> str:
        """Return the resource's provider type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.provider_type

    def get_resource_type(self) -> str:
        """Return the resource's type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.resource_type

    def get_unit_cost(self) -> float | None:
        """Return the resource's unit cost."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.unit_cost

    def get_category_and_subcategory(self) -> tuple[str | None, str | None]:
        """Return (category, subcategory) for this resource."""
        self._ensure_validated()
        assert self.model is not None
        return (self.model.spec.category, self.model.spec.subcategory)
