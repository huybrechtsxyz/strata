#!/usr/bin/env python3
"""Service for loading and validating resource configurations."""

import re

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel, ProviderConfigSpecModel
from strata.models.resource_model import ResourceModel
from strata.services.base_service import BaseService


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

    def _validate_dynamic(self, configuration_model: ConfigurationModel | None = None) -> tuple[bool, list[str]]:
        """Phase 2: check that `spec.properties.provider_type` is a registered provider type name."""
        if configuration_model is None or self.model is None:
            return True, []

        provider_type = self.model.spec.properties.provider_type
        registered_names = {p.name for p in configuration_model.spec.providers} if configuration_model.spec.providers else set()

        if provider_type not in registered_names:
            available = sorted(registered_names)
            return False, [f"Provider type '{provider_type}' not found in configuration. Available providers: {available}"]

        return True, []

    def validate_against_provider_config(self, provider_config: ProviderConfigModel) -> tuple[bool, list[str]]:
        """Cross-check the resource type and its configuration fields against a
        loaded ProviderConfig document.

        Validates:
        - Resource type exists for the provider (when `additional_resources=False`)
        - Configuration fields match schema patterns declared in the provider's resource entry

        Args:
            provider_config: The already-loaded `ProviderConfigModel` document
                that `configuration_model.spec.providers[].file` pointed at.
        """
        if self.model is None:
            return False, ["Resource model is not initialized"]

        errors: list[str] = []
        provider_type = self.model.spec.properties.provider_type
        resource_type = self.model.spec.properties.resource_type
        spec = provider_config.spec

        if not spec.additional_resources:
            if not spec.resources:
                errors.append(
                    f"Provider '{provider_type}' has no resources defined in its provider config, "
                    f"but additional_resources is False"
                )
                return False, errors

            valid_resource_types = [res.name for res in spec.resources]
            if resource_type not in valid_resource_types:
                errors.append(
                    f"Resource type '{resource_type}' is not valid for provider '{provider_type}'. "
                    f"Valid resource types: {valid_resource_types}"
                )
                return False, errors

        if self.model.spec.configuration:
            errors.extend(self._validate_configuration_schema(spec, resource_type, self.model.spec.configuration))

        return len(errors) == 0, errors

    def _validate_configuration_schema(
        self,
        provider_config_spec: ProviderConfigSpecModel,
        resource_type: str,
        configuration: dict[str, object],
    ) -> list[str]:
        """Validate configuration fields against schema patterns declared in the provider registry.

        When `additional_configurations=False`, only fields in the schema are allowed.
        Required schema fields absent from `configuration` are reported as errors.
        """
        errors: list[str] = []
        config_resource = None
        if provider_config_spec.resources:
            for res in provider_config_spec.resources:
                if res.name == resource_type:
                    config_resource = res
                    break

        if config_resource is None or config_resource.configuration is None:
            return errors

        schema = config_resource.configuration
        additional_allowed = config_resource.additional_configurations

        for field_name, field_value in configuration.items():
            if field_name not in schema:
                if not additional_allowed:
                    errors.append(
                        f"Configuration field '{field_name}' is not allowed for resource type '{resource_type}'. "
                        f"additional_configurations is False. Valid fields: {list(schema.keys())}"
                    )
                continue

            schema_def = schema[field_name]
            pattern = schema_def if isinstance(schema_def, str) else schema_def.pattern
            value_str = str(field_value)

            try:
                if not re.match(pattern, value_str):
                    errors.append(
                        f"Configuration field '{field_name}' value '{value_str}' does not match "
                        f"required pattern '{pattern}' for resource type '{resource_type}'"
                    )
            except re.error as e:
                errors.append(
                    f"Invalid regex pattern '{pattern}' for field '{field_name}' in configuration schema: {e}"
                )

        for schema_field, schema_def in schema.items():
            if schema_field in configuration:
                continue

            is_required = schema_def.required if not isinstance(schema_def, str) else True
            if is_required:
                pattern = schema_def if isinstance(schema_def, str) else schema_def.pattern
                errors.append(
                    f"Required configuration field '{schema_field}' is missing "
                    f"for resource type '{resource_type}'. Pattern: {pattern}"
                )

        return errors

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
