#!/usr/bin/env python3
"""Provider type registry, as a standalone kind.

Promoted out of `ConfigurationSpecModel` (was a bare embedded list,
`config_provider_model.py`) into its own kind — same reasoning as
`Integration`'s promotion: a platform with many provider types (azure, aws,
kamatera, gcp...) shouldn't have to grow one shared file for every new type;
each gets its own file, own PR, own reviewer, referenced from
`Configuration` by name+file (mirrors every other kind Workspace already
references this way).

Kind name `providerconfig` (not `provider-config`/`config-provider`) matches
the existing no-separator convention every `PlatformKind` value already
follows, and reads as "the config for this provider [type]" rather than
being mistaken for Crossplane's `ProviderConfig` (which means an *instance's*
credentials — closer to this codebase's existing `Provider` kind — not a
type-level policy registry; naming collision accepted, "Config" judged most
obvious to a general audience over the more precise but less familiar
Kubernetes "Class" convention, e.g. `StorageClass`).
"""

from typing import Any, Union

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names


class ProviderConfigSchemaField(PlatformBaseModel):
    """A configuration schema field with pattern and required flag.

    All field values are validated as strings against the `pattern` regex.
    There is no native boolean type — use `pattern: "^(true|false)$"` and pass
    `"true"` or `"false"` as the value in resource configuration.
    """

    pattern: str = Field(description="Regex pattern that field values must match")
    required: bool = Field(True, description="Whether this field is required in resource configuration")
    description: str | None = Field(None, description="Description of what this field represents")


class ProviderConfigResourceModel(PlatformBaseModel):
    """A provider type's supported resource type configuration."""

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
    configuration: dict[str, Union[str, ProviderConfigSchemaField]] | None = Field(
        None,
        description="Resource-specific configuration schema (pattern string or structured field)",
    )


class ProviderConfigSpecModel(PlatformBaseModel):
    """A provider type's registry entry: valid regions and resource types."""

    description: str = Field(description="Description of the provider type")
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
    resources: list[ProviderConfigResourceModel] | None = Field(
        None, description="List of supported resource types for this provider"
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "ProviderConfigSpecModel":
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
            check_unique_names(region_names, "regions in provider config")

        if self.resources:
            check_unique_names([res.name for res in self.resources], "resources in provider config")

        return self


class ProviderConfigMetaModel(PlatformBaseModel):
    """Provider config metadata (name, annotations, labels, tags).

    `name` is the provider type key (e.g. 'kamatera', 'azure') — matched
    against a real `Provider` document's `spec.properties.type`.
    """

    name: PlatformName = Field(description="Provider type name (e.g. kamatera, azure)")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class ProviderConfigModel(PlatformBaseModel):
    """Root model for a provider type registry entry."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for provider config",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PROVIDERCONFIG,
        frozen=True,
        description="Platform kind (always 'providerconfig')",
    )
    meta: ProviderConfigMetaModel = Field(description="Provider config metadata (name, annotations, labels, tags)")
    spec: ProviderConfigSpecModel = Field(description="Provider config specification (regions, resources)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.PROVIDERCONFIG)
