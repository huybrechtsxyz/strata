#!/usr/bin/env python3
"""
===============================================================================
Script Name   : configuration_model.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Pydantic model for provider/resource configuration validation.

How to load the configuration data:
    with importlib.resources.open_text("xyz_platform.data", "providers.yaml") as f:
        config = yaml.safe_load(f)
===============================================================================
"""

from typing import Dict, List, Optional, Any, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from xyz_platform.models.common_models import (
    CommonLifecycleModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)
from xyz_platform.models.integration_model import IntegrationModel
from xyz_platform.models.repository_model import RepositoryModel


class ConfigurationSecurityModel(BaseModel):
    """Model for security policies and constraints."""

    allowed_secret_stores: Optional[List[str]] = Field(
        None,
        description="Allowed secret store types. If None, all stores are allowed (dev mode). For production, restrict to secure stores only.",
    )
    allowed_variable_stores: Optional[List[str]] = Field(
        None,
        description="Allowed variable store types. If None, all stores are allowed.",
    )
    allowed_feature_stores: Optional[List[str]] = Field(
        None,
        description="Allowed feature store types. If None, all stores are allowed.",
    )


class ConfigurationComponentModel(BaseModel):
    """Model for a topology component configuration."""

    role: PlatformName = Field(
        ..., description="Unique role for the topology component."
    )
    description: Optional[str] = Field(
        None, description="Description of the topology component."
    )
    uses_module: Optional[bool] = Field(
        False, description="Indicates if the component requires a module."
    )
    is_control: Optional[bool] = Field(
        False, description="Indicates if the component is a control or manager element."
    )
    required: Optional[bool] = Field(
        True, description="Indicates if the component is required in the topology."
    )
    min_count: Optional[int] = Field(
        0, description="Minimum number of instances for the component."
    )
    max_count: Optional[int] = Field(
        0, description="Maximum number of instances for the component (0 = unlimited)."
    )

    @field_validator("min_count")
    @classmethod
    def validate_min_count(cls, v: Optional[int]) -> Optional[int]:
        """Validate min_count is >= 0."""
        if v is not None and v < 0:
            raise ValueError("min_count must be >= 0")
        return v

    @field_validator("max_count")
    @classmethod
    def validate_max_count(cls, v: Optional[int]) -> Optional[int]:
        """Validate max_count is >= 0."""
        if v is not None and v < 0:
            raise ValueError("max_count must be >= 0")
        return v

    @model_validator(mode="after")
    def validate_count_relationship(self) -> "ConfigurationComponentModel":
        """Validate max_count >= min_count."""
        if self.min_count is not None and self.max_count is not None:
            # 0 means unlimited
            if self.max_count != 0 and self.max_count < self.min_count:
                raise ValueError(
                    f"max_count ({self.max_count}) must be >= min_count ({self.min_count})"
                )
        return self


class ConfigurationTopologyModel(BaseModel):
    """Model for a provider topology configuration."""

    type: PlatformName = Field(
        ..., description="Unique type definition for the topology configuration."
    )
    description: Optional[str] = Field(
        None, description="Description of the topology configuration."
    )
    additional_components: bool = Field(
        False,
        description="Allow components not listed in the configuration for this topology",
    )
    components: Optional[List[ConfigurationComponentModel]] = Field(
        None, description="Topology-specific component configurations."
    )

    @model_validator(mode="after")
    def validate_unique_component_roles(self) -> "ConfigurationTopologyModel":
        """Validate that all component roles are unique within this topology."""
        if self.components:
            roles = [comp.role for comp in self.components]
            duplicates = [role for role in roles if roles.count(role) > 1]
            if duplicates:
                raise ValueError(
                    f"Duplicate component roles in topology '{self.type}': {', '.join(set(duplicates))}"
                )
        return self


class ConfigurationSchemaField(BaseModel):
    """Model for a configuration schema field with pattern and required flag."""

    pattern: str = Field(..., description="Regex pattern that field values must match")
    required: bool = Field(
        True, description="Whether this field is required in resource configuration"
    )
    description: Optional[str] = Field(
        None, description="Description of what this field represents"
    )


class ConfigurationProviderResourceModel(BaseModel):
    """Model for a provider resource configuration."""

    name: PlatformName = Field(
        ..., description="Unique name for the configuration resource."
    )
    category: Optional[str] = Field(
        None, description="Resource category (e.g., compute, storage, networking)"
    )
    subcategory: Optional[str] = Field(
        None,
        description="Resource subcategory (e.g., virtualmachine, blob, api_gateway)",
    )
    description: Optional[str] = Field(
        None, description="Description of the resource type"
    )
    additional_configurations: bool = Field(
        False,
        description="Allow configuration fields not listed in the schema for this resource",
    )
    configuration: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Resource-specific configuration schema (pattern string or structured field)",
    )


class ConfigurationProviderModel(BaseModel):
    """Model for a provider configuration."""

    name: PlatformName = Field(..., description="Provider name (e.g., kamatera, azure)")
    description: str = Field(..., description="Description of the provider")
    engine: Optional[str] = Field(
        None, description="Provider engine/driver (e.g., azurerm, aws, gcp)"
    )
    version: Optional[str] = Field(
        None, description="Provider version constraint (e.g., ~>3.0, >=1.0)"
    )
    additional_regions: bool = Field(
        False,
        description="Allow regions not listed in the configuration for this provider",
    )
    regions: Optional[List[Union[str, Dict[str, Any]]]] = Field(
        None, description="List of supported regions for this provider"
    )
    additional_resources: bool = Field(
        False,
        description="Allow resource types not listed in the configuration for this provider",
    )
    resources: Optional[List[ConfigurationProviderResourceModel]] = Field(
        None, description="List of supported resource types for this provider"
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "ConfigurationProviderModel":
        """Validate provider configuration requirements and uniqueness."""
        # Validate that lists are provided when additional_* is False
        if not self.additional_regions and (
            self.regions is None or len(self.regions) == 0
        ):
            raise ValueError(
                "If additional_regions is False, regions must be provided and non-empty"
            )
        if not self.additional_resources and (
            self.resources is None or len(self.resources) == 0
        ):
            raise ValueError(
                "If additional_resources is False, resources must be provided and non-empty"
            )

        # Validate unique region names
        if self.regions:
            region_names = []
            for region in self.regions:
                if isinstance(region, dict) and "name" in region:
                    region_names.append(region["name"])
                elif isinstance(region, str):
                    region_names.append(region)
            duplicates = [name for name in region_names if region_names.count(name) > 1]
            if duplicates:
                raise ValueError(
                    f"Duplicate regions in provider '{self.name}': {', '.join(set(duplicates))}"
                )

        # Validate unique resource names
        if self.resources:
            resource_names = [res.name for res in self.resources]
            duplicates = [
                name for name in resource_names if resource_names.count(name) > 1
            ]
            if duplicates:
                raise ValueError(
                    f"Duplicate resources in provider '{self.name}': {', '.join(set(duplicates))}"
                )

        return self


class ConfigurationLayerModel(BaseModel):
    """Definition of a single layer in the deployment hierarchy."""

    name: PlatformName = Field(
        description="Layer name (must be valid identifier: lowercase, alphanumeric, hyphens)"
    )
    description: Optional[str] = Field(
        None, description="Human-readable description of this layer's purpose"
    )
    pattern: Optional[str] = Field(
        None,
        description="Regex pattern for validating layer values (e.g., '^[a-z][a-z0-9\\-]*$')",
    )
    required: bool = Field(
        default=False,
        description="Whether this layer must be provided in deployment files",
    )
    default: Optional[str] = Field(
        None,
        description="Default value if not provided in deployment (only used if required=False)",
    )

    @model_validator(mode="after")
    def validate_default_when_not_required(self) -> "ConfigurationLayerModel":
        """Validate that default is only set when required=False."""
        if self.required and self.default:
            raise ValueError(
                f"Layer '{self.name}': Cannot set default value when required=True"
            )
        return self


class ConfigurationLoggingModel(BaseModel):
    """Model for logging configuration."""

    file: Optional[str] = Field(
        None,
        description="Path to logging configuration YAML file (relative to workspace root or absolute)",
    )


class ConfigurationDeploymentModel(BaseModel):
    """Model for deployment configuration and schema definition.

    Defines required and optional properties that deployments must provide,
    similar to how ConfigurationProviderResourceModel defines resource configuration schemas.

    Example:
        deployment:
          properties:
            additional_properties: false
            properties:
              environment:
                pattern: "^(dev|test|staging|prod)$"
                required: true
                description: "Deployment environment"
              customer:
                pattern: "^[a-z][a-z0-9-]*$"
                required: true
                description: "Customer identifier"
              region:
                pattern: "^[a-z]{2}-[a-z]+(-[0-9]+)?$"
                required: false
                description: "Deployment region"
    """

    additional_properties: bool = Field(
        False,
        description="Allow properties not listed in the schema for deployments",
    )
    properties: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Deployment properties schema (pattern string or structured field with validation)",
    )


class ConfigurationSpecModel(BaseModel):
    """Specification for the configuration model."""

    logging: Optional[ConfigurationLoggingModel] = Field(
        None, description="Logging configuration for the platform"
    )
    layering: Optional[List[ConfigurationLayerModel]] = Field(
        None,
        description="Deployment hierarchy layers (defines artifact path structure and ordering)",
    )
    integrations: List[IntegrationModel] = Field(
        default_factory=list,
        description="External integrations that extend platform capabilities",
    )
    repositories: Optional[List[RepositoryModel]] = Field(
        None, description="Deployment manifest repositories (artifact storage backends)"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(
        None, description="Configuration lifecycle phases"
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None, description="List of configuration defaults"
    )
    properties: Optional[Dict[str, Any]] = Field(
        None, description="List of configuration properties"
    )
    deployment: Optional[ConfigurationDeploymentModel] = Field(
        None,
        description="Deployment configuration and schema for deployment.spec.properties validation",
    )
    providers: Optional[List[ConfigurationProviderModel]] = Field(
        None, description="List of supported providers"
    )
    additional_topologies: bool = Field(
        False,
        description="Allow topology types not listed in the configuration",
    )
    topologies: Optional[List[ConfigurationTopologyModel]] = Field(
        None, description="List of supported topologies"
    )
    security: Optional[ConfigurationSecurityModel] = Field(
        None,
        description="Security policies for store types and other security constraints",
    )

    @model_validator(mode="after")
    def validate_unique_providers(self) -> "ConfigurationSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            provider_names = [provider.name for provider in self.providers]
            duplicates = [
                name for name in provider_names if provider_names.count(name) > 1
            ]
            if duplicates:
                raise ValueError(
                    f"Duplicate provider names in configuration: {', '.join(set(duplicates))}"
                )
        return self

    @model_validator(mode="after")
    def validate_unique_topologies(self) -> "ConfigurationSpecModel":
        """Validate that all topology types are unique."""
        if self.topologies:
            topology_types = [topo.type for topo in self.topologies]
            duplicates = [
                ttype for ttype in topology_types if topology_types.count(ttype) > 1
            ]
            if duplicates:
                raise ValueError(
                    f"Duplicate topology types in configuration: {', '.join(set(duplicates))}"
                )
        return self

    @model_validator(mode="after")
    def validate_unique_layer_names(self) -> "ConfigurationSpecModel":
        """Validate that layer names are unique and last layer is 'environment'."""
        if self.layering:
            layer_names = [layer.name for layer in self.layering]
            if len(layer_names) != len(set(layer_names)):
                duplicates = [
                    name for name in layer_names if layer_names.count(name) > 1
                ]
                raise ValueError(f"Duplicate layer names found: {set(duplicates)}")

            # CRITICAL: Last layer must be named "environment"
            if layer_names[-1] != "environment":
                raise ValueError(
                    f"Last layer must be named 'environment', got '{layer_names[-1]}'. "
                    "This ensures artifact paths always end with environment identifier."
                )
        return self


class ConfigurationMetaModel(BaseModel):
    """Metadata for the configuration model."""

    name: PlatformName = Field(
        ..., description="Unique name for the configuration resource."
    )
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Labels for categorization and filtering."
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags.")


class ConfigurationModel(BaseModel):
    """
    Top-level model for a configuration file.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version of the configuration model.",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.CONFIGURATION,
        frozen=True,
        description="Platform kind: always 'configuration'.",
    )
    meta: ConfigurationMetaModel = Field(
        ..., description="Metadata for the configuration model."
    )
    spec: ConfigurationSpecModel = Field(
        ..., description="Specification for the configuration."
    )

    def get_repo_map(self) -> Dict[str, str]:
        """Return a ``{repo_name: deploy_path}`` mapping for resolving ``@repo_name/...`` references."""
        repos = self.spec.repositories if self.spec and self.spec.repositories else {}
        if not repos or len(repos) == 0:
            return {}
        return {
            repo.name: repo.deploy_path
            for repo in repos
            if repo.name and repo.deploy_path
        }
