#!/usr/bin/env python3
"""Service for loading and validating resource configurations."""

import re

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel, ProviderConfigSpecModel
from strata.models.resource_model import ResourceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics


class ResourceService(BaseService[ResourceModel]):
    """Service for handling resource configurations.

    Phase 2 here only checks that `spec.properties.provider_type` is a
    *registered* provider type name (a pointer existing in
    `configuration_model.spec.providers`) — same reasoning as
    `ProviderService` (ADR-0014): `configuration_model.spec.providers`
    entries are `{name, file}` pointers now, so the deep resource-type/
    configuration-schema check needs an actually-loaded `ProviderConfigModel`,
    which `_validate_dynamic()`'s fixed signature has no slot for. That check
    is `validate_against_provider_config()` below.
    """

    def _get_model_class(self) -> type[ResourceModel]:
        """Return the ResourceModel class for validation."""
        return ResourceModel

    def _validate_dynamic(self, configuration_model: ConfigurationModel | None = None) -> Diagnostics:
        """Phase 2: check that `spec.properties.provider_type` is a registered provider type name."""
        diagnostics = Diagnostics()
        if configuration_model is None or self.model is None:
            return diagnostics

        provider_type = self.model.spec.properties.provider_type
        registered_names = set(configuration_model.spec.providers or [])

        if provider_type not in registered_names:
            available = sorted(registered_names)
            diagnostics.error(
                f"Provider type '{provider_type}' not found in configuration. Available providers: {available}",
                location="spec.properties.provider_type",
                code="unregistered_provider_type",
            )

        return diagnostics

    def validate_against_provider_config(self, provider_config: ProviderConfigModel) -> Diagnostics:
        """Cross-check the resource type and its configuration fields against a
        loaded ProviderConfig document.

        Validates:
        - Resource type exists for the provider (when `additional_resources=False`)
        - Configuration fields match schema patterns declared in the provider's resource entry

        Args:
            provider_config: The already-loaded `ProviderConfigModel` document
                named by `configuration_model.spec.providers[]`.
        """
        diagnostics = Diagnostics()
        if self.model is None:
            diagnostics.error("Resource model is not initialized")
            return diagnostics

        provider_type = self.model.spec.properties.provider_type
        resource_type = self.model.spec.properties.resource_type
        spec = provider_config.spec

        if not spec.additional_resources:
            if not spec.resources:
                diagnostics.error(
                    f"Provider '{provider_type}' has no resources defined in its provider config, "
                    f"but additional_resources is False",
                    location="spec.properties.resource_type",
                    code="no_resources_defined",
                )
                return diagnostics

            valid_resource_types = [res.name for res in spec.resources]
            if resource_type not in valid_resource_types:
                diagnostics.error(
                    f"Resource type '{resource_type}' is not valid for provider '{provider_type}'. "
                    f"Valid resource types: {valid_resource_types}",
                    location="spec.properties.resource_type",
                    code="invalid_resource_type",
                )
                return diagnostics

        if self.model.spec.configuration:
            diagnostics.extend(
                self._validate_configuration_schema(spec, resource_type, self.model.spec.configuration)
            )

        return diagnostics

    def _validate_configuration_schema(
        self,
        provider_config_spec: ProviderConfigSpecModel,
        resource_type: str,
        configuration: dict[str, object],
    ) -> Diagnostics:
        """Validate configuration fields against schema patterns declared in the provider registry.

        When `additional_configurations=False`, only fields in the schema are allowed.
        Required schema fields absent from `configuration` are reported as errors.
        """
        diagnostics = Diagnostics()
        config_resource = None
        if provider_config_spec.resources:
            for res in provider_config_spec.resources:
                if res.name == resource_type:
                    config_resource = res
                    break

        if config_resource is None or config_resource.configuration is None:
            return diagnostics

        schema = config_resource.configuration
        additional_allowed = config_resource.additional_configurations

        for field_name, field_value in configuration.items():
            if field_name not in schema:
                if not additional_allowed:
                    diagnostics.error(
                        f"Configuration field '{field_name}' is not allowed for resource type '{resource_type}'. "
                        f"additional_configurations is False. Valid fields: {list(schema.keys())}",
                        location=f"spec.configuration.{field_name}",
                        code="configuration_field_not_allowed",
                    )
                continue

            schema_def = schema[field_name]
            pattern = schema_def if isinstance(schema_def, str) else schema_def.pattern
            value_str = str(field_value)

            try:
                if not re.match(pattern, value_str):
                    diagnostics.error(
                        f"Configuration field '{field_name}' value '{value_str}' does not match "
                        f"required pattern '{pattern}' for resource type '{resource_type}'",
                        location=f"spec.configuration.{field_name}",
                        code="configuration_pattern_mismatch",
                    )
            except re.error as e:
                diagnostics.error(
                    f"Invalid regex pattern '{pattern}' for field '{field_name}' in configuration schema: {e}",
                    location=f"spec.configuration.{field_name}",
                    code="invalid_schema_pattern",
                )

        for schema_field, schema_def in schema.items():
            if schema_field in configuration:
                continue

            is_required = schema_def.required if not isinstance(schema_def, str) else True
            if is_required:
                pattern = schema_def if isinstance(schema_def, str) else schema_def.pattern
                diagnostics.error(
                    f"Required configuration field '{schema_field}' is missing "
                    f"for resource type '{resource_type}'. Pattern: {pattern}",
                    location=f"spec.configuration.{schema_field}",
                    code="configuration_field_missing",
                )

        return diagnostics

    def get_provider_type(self) -> str:
        """Return the resource's provider type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.provider_type

    def get_resource_type(self) -> str:
        """Return the resource's type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.resource_type

    def get_unit_cost(self) -> float | None:
        """Return the resource's unit cost."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.unit_cost

    def get_category_and_subcategory(self) -> tuple[str | None, str | None]:
        """Return (category, subcategory) for this resource."""
        self._ensure_validated()
        assert self.model is not None
        return (self.model.spec.properties.category, self.model.spec.properties.subcategory)
