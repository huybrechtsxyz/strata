#!/usr/bin/env python3
"""
===============================================================================
Script Name   : environment_model.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Pydantic model for environment configuration validation.
===============================================================================
"""

from typing import List, Dict, Any, Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from xyz_platform.models.common_models import (
    CommonLifecycleModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_slot_type,
)
from xyz_platform.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
    validate_unique_variable_keys,
    validate_unique_secret_keys,
    validate_unique_feature_keys,
)


class EnvironmentResourceOverrideModel(BaseModel):
    """
    Environment-specific resource overrides.

    Mirrors WorkspaceResourceModel fields (all optional except resource identifier).
    Values are merged with workspace resource configuration.
    """

    resource: PlatformName = Field(
        description="Resource name to override (must match a resource in the workspace)"
    )
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
    role: Optional[PlatformName] = Field(
        None, description="Override role of the resource"
    )
    count: Optional[int] = Field(
        None, ge=1, le=100, description="Override resource instance count"
    )
    depends_on: Optional[List[str]] = Field(
        None,
        description="Override resource dependencies",
    )
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
    custom: Optional[Dict[str, Any]] = Field(
        None, description="Environment-specific custom properties"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Override resource labels",
    )
    tags: Optional[List[Any]] = Field(None, description="Override resource tags")


class EnvironmentModuleOverrideModel(BaseModel):
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


class EnvironmentProviderOverrideModel(BaseModel):
    """
    Environment-specific provider overrides.

    Mirrors WorkspaceProviderModel fields (all optional except provider identifier).
    Values are merged with workspace provider configuration.
    Note: Provider configuration is in the provider file itself, not in workspace.
    """

    provider: PlatformName = Field(
        description="Provider name to override (must match a provider in the workspace)"
    )
    description: Optional[str] = Field(
        None,
        description="Override provider description",
    )


class EnvironmentOverridesModel(BaseModel):
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
    properties: Optional[Dict[str, Any]] = Field(
        None, description="Override workspace properties for this environment"
    )

    @model_validator(mode="after")
    def validate_unique_overrides(self) -> "EnvironmentOverridesModel":
        """Validate that override references are unique."""
        errors = []

        # Validate unique resource override names
        if self.resources:
            resource_names = [res.resource for res in self.resources]
            duplicates = [
                name for name in resource_names if resource_names.count(name) > 1
            ]
            if duplicates:
                errors.append(
                    f"Duplicate resource overrides found: {', '.join(set(duplicates))}"
                )

        # Validate unique module overrides (resource+module+slot_type combination)
        if self.modules:
            module_keys = [
                f"{mod.resource}:{mod.module}:{mod.slot_type or 'main'}"
                for mod in self.modules
            ]
            duplicates = [key for key in module_keys if module_keys.count(key) > 1]
            if duplicates:
                errors.append(
                    f"Duplicate module overrides found: {', '.join(set(duplicates))}"
                )

        # Validate unique provider override names
        if self.providers:
            provider_names = [prov.provider for prov in self.providers]
            duplicates = [
                name for name in provider_names if provider_names.count(name) > 1
            ]
            if duplicates:
                errors.append(
                    f"Duplicate provider overrides found: {', '.join(set(duplicates))}"
                )

        if errors:
            raise ValueError("; ".join(errors))

        return self


class EnvironmentSpecModel(BaseModel):
    """Model for environment specification with workspace overrides."""

    lifecycle: Optional[CommonLifecycleModel] = Field(
        None, description="Environment lifecycle phases"
    )
    properties: Optional[Dict[str, Any]] = Field(
        None, description="Environment-specific properties and configurations"
    )
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


class EnvironmentMetaModel(BaseModel):
    """Model for environment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique environment name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        description="Labels for classification/filtering",
    )
    tags: Optional[List[Any]] = Field(
        None, description="Optional tags (list of values for categorization)"
    )


class EnvironmentModel(BaseModel):
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
    meta: EnvironmentMetaModel = Field(
        description="Environment metadata (name, annotations, labels, tags)"
    )
    spec: EnvironmentSpecModel = Field(
        description="Environment specification (properties, workspace, environment)"
    )
