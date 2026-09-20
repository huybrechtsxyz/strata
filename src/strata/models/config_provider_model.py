#!/usr/bin/env python3
"""Provider registry models for solution-wide configuration.

Models a known provider type's registry entry: its valid regions and supported
resource types. Used by `ConfigurationSpecModel.providers` and cross-checked
against by `ProviderService`'s Phase 2 dynamic validation.
"""

from typing import Any, Union

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName, check_unique_names


class ConfigurationSchemaField(PlatformBaseModel):
    """A configuration schema field with pattern and required flag.

    All field values are validated as strings against the `pattern` regex.
    There is no native boolean type — use `pattern: "^(true|false)$"` and pass
    `"true"` or `"false"` as the value in resource configuration.
    """

    pattern: str = Field(description="Regex pattern that field values must match")
    required: bool = Field(True, description="Whether this field is required in resource configuration")
    description: str | None = Field(None, description="Description of what this field represents")


class ConfigurationProviderResourceModel(PlatformBaseModel):
    """A provider's supported resource type configuration."""

    name: PlatformName = Field(description="Unique name for the configuration resource.")
    category: str | None = Field(None, description="Resource category (e.g., compute, storage, networking)")
    subcategory: str | None = Field(
        None,
        description="Resource subcategory (e.g., virtualmachine, blob, api_gateway)",
    )
    description: str | None = Field(None, description="Description of the resource type")
    additional_configurations: bool = Field(
        False,
        description="Allow configuration fields not listed in the schema for this resource",
    )
    configuration: dict[str, str | ConfigurationSchemaField] | None = Field(
        None,
        description="Resource-specific configuration schema (pattern string or structured field)",
    )


class ConfigurationProviderModel(PlatformBaseModel):
    """A known provider type's registry entry: valid regions and resource types."""

    name: PlatformName = Field(description="Provider name (e.g., kamatera, azure)")
    description: str = Field(description="Description of the provider")
    version: str | None = Field(None, description="Provider version constraint (e.g., ~>3.0, >=1.0)")
    additional_regions: bool = Field(
        False,
        description="Allow regions not listed in the configuration for this provider",
    )
    regions: list[Union[str, dict[str, Any]]] | None = Field(
        None, description="List of supported regions for this provider"
    )
    additional_resources: bool = Field(
        False,
        description="Allow resource types not listed in the configuration for this provider",
    )
    resources: list[ConfigurationProviderResourceModel] | None = Field(
        None, description="List of supported resource types for this provider"
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "ConfigurationProviderModel":
        """Validate provider configuration requirements and uniqueness."""
        if not self.additional_regions and (self.regions is None or len(self.regions) == 0):
            raise ValueError("If additional_regions is False, regions must be provided and non-empty")
        if not self.additional_resources and (self.resources is None or len(self.resources) == 0):
            raise ValueError("If additional_resources is False, resources must be provided and non-empty")

        if self.regions:
            region_names = []
            for region in self.regions:
                if isinstance(region, dict) and "name" in region:
                    region_names.append(region["name"])
                elif isinstance(region, str):
                    region_names.append(region)
            check_unique_names(region_names, f"regions in provider '{self.name}'")

        if self.resources:
            check_unique_names([res.name for res in self.resources], f"resources in provider '{self.name}'")

        return self
