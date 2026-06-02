#!/usr/bin/env python3
"""Pydantic model for environment configuration validation."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_slot_type,
)
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
    validate_unique_feature_keys,
    validate_unique_secret_keys,
    validate_unique_variable_keys,
)


class IncludeMergeStrategy(str, Enum):
    """Supported merge strategies for terraform file includes."""

    CONCATENATE = "concatenate"
    MERGE = "merge"


class EnvironmentIncludeModel(PlatformBaseModel):
    """
    Terraform file include definition for merging during build.

    Defines which source files to merge, the merge strategy, and the output target
    within the build terraform directory.
    """

    source: str = Field(
        description="Source file path or glob pattern. Supports @repo/ references. "
        "Examples: '@haven/terraform/waf/listeners/*.tf', 'customers/acme/overrides.tfvars'",
        min_length=1,
    )
    target: str = Field(
        description="Output filename in the build terraform directory. "
        "Examples: 'waf_listeners.tf', 'acme.auto.tfvars.json'",
        min_length=1,
    )
    strategy: IncludeMergeStrategy = Field(
        default=IncludeMergeStrategy.CONCATENATE,
        description="Merge strategy: 'concatenate' (raw append) or 'merge' (structural merge)",
    )
    optional: bool = Field(
        default=False,
        description="If true, silently skip when source resolves to no files",
    )
    order: Optional[int] = Field(
        default=None,
        ge=0,
        description="Sort priority when multiple includes share the same target (lower = first)",
    )

    @field_validator("target")
    @classmethod
    def validate_target_no_traversal(cls, v: str) -> str:
        """Prevent path traversal in target."""
        if ".." in v:
            raise ValueError("Target path must not contain '..' (path traversal)")
        return v


class EnvironmentResourceOverrideModel(PlatformBaseModel):
    """
    Environment-specific resource overrides.

    Mirrors WorkspaceResourceModel fields (all optional except resource identifier).
    Values are merged with workspace resource configuration.
    """

    resource: PlatformName = Field(description="Resource name to override (must match a resource in the workspace)")
    description: Optional[str] = Field(
        None,
        description="Override resource description",
    )
    enabled: Optional[bool] = Field(
        None,
        description="Override whether this resource is enabled/deployed in this environment",
    )
    condition: Optional[str] = Field(
        None,
        description="Override conditional expression for resource inclusion",
    )
    role: Optional[PlatformName] = Field(None, description="Override role of the resource")
    count: Optional[int] = Field(None, ge=1, le=100, description="Override resource instance count")
    depends_on: Optional[List[str]] = Field(
        None,
        description="Override resource dependencies",
    )

    @field_validator("depends_on", mode="before")
    @classmethod
    def coerce_depends_on(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    references: Optional[Dict[str, str]] = Field(
        None,
        description="Override cross-resource value references",
    )
    firewalls: Optional[List[str]] = Field(
        None,
        description="Override firewall/NSG references",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Environment-specific configuration overrides (merged with workspace configuration)",
    )
    custom: Optional[Dict[str, Any]] = Field(None, description="Environment-specific custom properties")
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Override resource labels",
    )
    tags: Optional[List[Any]] = Field(None, description="Override resource tags")
    includes: Optional[List[EnvironmentIncludeModel]] = Field(
        None,
        description="Terraform files to merge into build output for this resource",
    )


class EnvironmentModuleOverrideModel(PlatformBaseModel):
    """
    Environment-specific module overrides (for modules within workspace resources).

    Mirrors WorkspaceModuleReferenceModel fields (all optional except identifiers).
    Values are merged with workspace module configuration.
    """

    resource: PlatformName = Field(description="Resource name containing the module")
    module: PlatformName = Field(description="Module name to override")
    slot_type: Optional[str] = Field(
        None,
        description="Override deployment slot type (main, staging, canary, sidecar, init)",
    )
    enabled: Optional[bool] = Field(
        None,
        description="Override whether this module is enabled/deployed in this environment",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Environment-specific module configuration overrides",
    )

    @field_validator("slot_type")
    @classmethod
    def validate_slot_type_value(cls, v: Optional[str]) -> Optional[str]:
        """Validate slot_type using common validator."""
        return validate_slot_type(v)


class EnvironmentProviderOverrideModel(PlatformBaseModel):
    """
    Environment-specific provider overrides.

    Mirrors WorkspaceProviderModel fields (all optional except provider identifier).
    Values are merged with workspace provider configuration.
    Note: Provider configuration is in the provider file itself, not in workspace.
    """

    provider: PlatformName = Field(description="Provider name to override (must match a provider in the workspace)")
    description: Optional[str] = Field(
        None,
        description="Override provider description",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Environment-specific provider configuration overrides (merged with workspace provider config)",
    )


class EnvironmentOverridesModel(PlatformBaseModel):
    """Model for environment overrides on workspace configurations."""

    resources: Optional[List[EnvironmentResourceOverrideModel]] = Field(
        None,
        description="Resource-specific overrides (count, configuration, enabled state)",
    )
    modules: Optional[List[EnvironmentModuleOverrideModel]] = Field(
        None,
        description="Module-specific overrides (enabled, slot_type, configuration)",
    )
    providers: Optional[List[EnvironmentProviderOverrideModel]] = Field(
        None, description="Provider-specific overrides (description)"
    )
    properties: Optional[Dict[str, Any]] = Field(None, description="Override workspace properties for this environment")
    includes: Optional[List[EnvironmentIncludeModel]] = Field(
        None,
        description="Environment-wide terraform file includes (not tied to a specific resource)",
    )

    @model_validator(mode="after")
    def validate_unique_overrides(self) -> "EnvironmentOverridesModel":
        """Validate that override references are unique."""
        errors = []

        # Validate unique resource override names
        if self.resources:
            resource_names = [res.resource for res in self.resources]
            duplicates = [name for name in resource_names if resource_names.count(name) > 1]
            if duplicates:
                errors.append(f"Duplicate resource overrides found: {', '.join(set(duplicates))}")

        # Validate unique module overrides (resource+module+slot_type combination)
        if self.modules:
            module_keys = [f"{mod.resource}:{mod.module}:{mod.slot_type or 'main'}" for mod in self.modules]
            duplicates = [key for key in module_keys if module_keys.count(key) > 1]
            if duplicates:
                errors.append(f"Duplicate module overrides found: {', '.join(set(duplicates))}")

        # Validate unique provider override names
        if self.providers:
            provider_names = [prov.provider for prov in self.providers]
            duplicates = [name for name in provider_names if provider_names.count(name) > 1]
            if duplicates:
                errors.append(f"Duplicate provider overrides found: {', '.join(set(duplicates))}")

        if errors:
            raise ValueError("; ".join(errors))

        return self


class EnvironmentSpecModel(PlatformBaseModel):
    """Model for environment specification with workspace overrides."""

    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Environment lifecycle phases")
    properties: Optional[Dict[str, Any]] = Field(None, description="Environment-specific properties and configurations")
    custom: Optional[Dict[str, Any]] = Field(
        None, description="Environment-specific custom properties and configurations"
    )
    overrides: Optional[EnvironmentOverridesModel] = Field(
        None,
        description="Environment-specific overrides for workspace configurations (resources, modules, providers, properties)",
    )
    variables: Optional[List[VariableStoreModel]] = Field(
        None,
        description="Variable declarations - single source of truth for all platform component variable references",
    )
    secrets: Optional[List[SecretStoreModel]] = Field(
        None,
        description="Secret declarations - single source of truth for all platform component secret references",
    )
    features: Optional[List[FeatureStoreModel]] = Field(
        None,
        description="Feature flag declarations - single source of truth for all platform component feature references",
    )

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "EnvironmentSpecModel":
        """Validate that variable, secret, and feature keys are unique."""
        validate_unique_variable_keys(self.variables)
        validate_unique_secret_keys(self.secrets)
        validate_unique_feature_keys(self.features)
        return self


class EnvironmentMetaModel(PlatformBaseModel):
    """Model for environment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique environment name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        description="Labels for classification/filtering",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class EnvironmentModel(PlatformBaseModel):
    """Root model for a environment configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.ENVIRONMENT,
        frozen=True,
        description="Platform kind (always 'environment')",
    )
    meta: EnvironmentMetaModel = Field(description="Environment metadata (name, annotations, labels, tags)")
    spec: EnvironmentSpecModel = Field(description="Environment specification (properties, workspace, environment)")
