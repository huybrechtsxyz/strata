#!/usr/bin/env python3
"""Pydantic model for provider and resource configuration validation."""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import Field, field_validator, model_validator

from strata.models.audit_config_model import AuditConfigModel
from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)
from strata.models.integration_model import IntegrationModel
from strata.models.policy_model import PolicyModel
from strata.models.repository_model import RemoteModel
from strata.utils.config import SOLUTION_DEPLOYMENTS_DIR, SOLUTION_DIR, SOLUTION_OUTPUTS_DIR


class ConfigurationSecurityModel(PlatformBaseModel):
    """Model for security policies and constraints."""

    allowed_secret_stores: Optional[List[str]] = Field(
        None,
        description="Allowed secret store types. If None, all stores are allowed (dev mode). Valid values: constant, environment, github, azure-keyvault, bitwarden, vault, infisical. For production, restrict to secure stores only. 'github' and 'environment' are pipeline-trust stores (resolved from environment variables) — add them explicitly to permit use.",
    )
    allowed_variable_stores: Optional[List[str]] = Field(
        None,
        description="Allowed variable store types. If None, all stores are allowed.",
    )
    allowed_feature_stores: Optional[List[str]] = Field(
        None,
        description="Allowed feature store types. If None, all stores are allowed.",
    )


class ConfigurationComponentModel(PlatformBaseModel):
    """Model for a topology component configuration."""

    role: PlatformName = Field(..., description="Unique role for the topology component.")
    description: Optional[str] = Field(None, description="Description of the topology component.")
    uses_module: Optional[bool] = Field(False, description="Indicates if the component requires a module.")
    is_control: Optional[bool] = Field(False, description="Indicates if the component is a control or manager element.")
    required: Optional[bool] = Field(True, description="Indicates if the component is required in the topology.")
    min_count: Optional[int] = Field(0, description="Minimum number of instances for the component.")
    max_count: Optional[int] = Field(0, description="Maximum number of instances for the component (0 = unlimited).")

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
                raise ValueError(f"max_count ({self.max_count}) must be >= min_count ({self.min_count})")
        return self


class ConfigurationTopologyModel(PlatformBaseModel):
    """Model for a provider topology configuration."""

    type: PlatformName = Field(..., description="Unique type definition for the topology configuration.")
    description: Optional[str] = Field(None, description="Description of the topology configuration.")
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
                raise ValueError(f"Duplicate component roles in topology '{self.type}': {', '.join(set(duplicates))}")
        return self


class ConfigurationSchemaField(PlatformBaseModel):
    """Model for a configuration schema field with pattern and required flag.

    All field values are validated as strings against the ``pattern`` regex.
    There is no native boolean type — use ``pattern: "^(true|false)$"`` and
    pass ``"true"`` or ``"false"`` as the value in resource configuration.
    """

    pattern: str = Field(..., description="Regex pattern that field values must match")
    required: bool = Field(True, description="Whether this field is required in resource configuration")
    description: Optional[str] = Field(None, description="Description of what this field represents")


class ConfigurationProviderResourceModel(PlatformBaseModel):
    """Model for a provider resource configuration."""

    name: PlatformName = Field(..., description="Unique name for the configuration resource.")
    category: Optional[str] = Field(None, description="Resource category (e.g., compute, storage, networking)")
    subcategory: Optional[str] = Field(
        None,
        description="Resource subcategory (e.g., virtualmachine, blob, api_gateway)",
    )
    description: Optional[str] = Field(None, description="Description of the resource type")
    additional_configurations: bool = Field(
        False,
        description="Allow configuration fields not listed in the schema for this resource",
    )
    configuration: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Resource-specific configuration schema (pattern string or structured field)",
    )


class ConfigurationProviderModel(PlatformBaseModel):
    """Model for a provider configuration."""

    name: PlatformName = Field(..., description="Provider name (e.g., kamatera, azure)")
    description: str = Field(..., description="Description of the provider")
    engine: Optional[str] = Field(None, description="Provider engine/driver (e.g., azurerm, aws, gcp)")
    version: Optional[str] = Field(None, description="Provider version constraint (e.g., ~>3.0, >=1.0)")
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
        if not self.additional_regions and (self.regions is None or len(self.regions) == 0):
            raise ValueError("If additional_regions is False, regions must be provided and non-empty")
        if not self.additional_resources and (self.resources is None or len(self.resources) == 0):
            raise ValueError("If additional_resources is False, resources must be provided and non-empty")

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
                raise ValueError(f"Duplicate regions in provider '{self.name}': {', '.join(set(duplicates))}")

        # Validate unique resource names
        if self.resources:
            resource_names = [res.name for res in self.resources]
            duplicates = [name for name in resource_names if resource_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate resources in provider '{self.name}': {', '.join(set(duplicates))}")

        return self


class ConfigurationLayerModel(PlatformBaseModel):
    """Definition of a single layer in the deployment hierarchy."""

    name: PlatformName = Field(description="Layer name (must be valid identifier: lowercase, alphanumeric, hyphens)")
    description: Optional[str] = Field(None, description="Human-readable description of this layer's purpose")
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
            raise ValueError(f"Layer '{self.name}': Cannot set default value when required=True")
        return self


class ConfigurationLoggingModel(PlatformBaseModel):
    """Model for logging configuration."""

    file: Optional[str] = Field(
        None,
        description="Path to logging configuration YAML file (relative to workspace root or absolute)",
    )


class ManifestStoreType(str, Enum):
    """Supported manifest storage backends."""

    LOCAL = "local"
    GITOPS = "gitops"


class ConfigurationManifestModel(PlatformBaseModel):
    """Configuration for deployment manifest storage.

    Controls where and how deployment manifests are persisted after each
    deploy run.  When omitted from the configuration, manifests are not
    written and a log message is emitted.

    Path structure (auto-appended by the service)::

        {path}/{deployment_name}/{version}/{timestamp}.json

    Example (local):
        manifest:
          type: local
          path: ".strata/deployments"

    Example (gitops):
        manifest:
          type: gitops
          repository: "state-repo"
          branch: "manifests"
          tag: true
          path: "deployments"
    """

    type: ManifestStoreType = Field(description="Storage backend: 'local' (filesystem) or 'gitops' (git repository)")
    path: str = Field(
        default=f"{SOLUTION_DIR}/{SOLUTION_DEPLOYMENTS_DIR}",
        description="Base path for manifests. Service appends /{deployment_name}/{version}/{timestamp}.json",
    )
    repository: Optional[str] = Field(
        None,
        description="Remote name from spec.remotes (required when type=gitops)",
    )
    branch: Optional[str] = Field(
        None,
        description="Target branch for manifest commits (required when type=gitops)",
    )
    tag: bool = Field(
        default=True,
        description="Create a git tag '{deployment_name}/{version}' after writing (gitops only)",
    )

    @model_validator(mode="after")
    def validate_gitops_fields(self) -> "ConfigurationManifestModel":
        """Validate that gitops type has required repository and branch fields."""
        if self.type == ManifestStoreType.GITOPS:
            if not self.repository:
                raise ValueError("manifest.repository is required when type='gitops'")
            if not self.branch:
                raise ValueError("manifest.branch is required when type='gitops'")
        return self


class SensitiveOutputHandling(str, Enum):
    """How to handle Terraform outputs marked ``sensitive = true``."""

    REDACT = "redact"
    """Replace the value with the string ``(sensitive)`` — key remains visible."""

    OMIT = "omit"
    """Drop the key entirely from the stored artifact."""


class ConfigurationOutputsModel(PlatformBaseModel):
    """Configuration for Terraform output artifact storage.

    Controls whether and how Terraform output values are persisted after a
    successful deploy.  Stored outputs never include sensitive values as
    plain text; the ``sensitive`` field controls whether sensitive keys are
    redacted (value replaced with ``"(sensitive)"``) or omitted entirely.

    Path structure (auto-appended by the deployer)::

        {path}/{deployment_name}/{version}/{stage}.json

    Example:
        outputs:
          enabled: true
          path: ".strata/outputs"
          sensitive: redact
    """

    enabled: bool = Field(
        default=True,
        description="Write output artifacts after a successful deploy. Set to false to disable.",
    )
    path: str = Field(
        default=f"{SOLUTION_DIR}/{SOLUTION_OUTPUTS_DIR}",
        description=("Base path for output artifacts. The deployer appends /{deployment_name}/{version}/{stage}.json"),
    )
    sensitive: SensitiveOutputHandling = Field(
        default=SensitiveOutputHandling.REDACT,
        description=(
            "How to handle outputs marked sensitive=true in Terraform: "
            "'redact' (store key with value '(sensitive)') or 'omit' (drop the key entirely)."
        ),
    )


class ConfigurationDeploymentModel(PlatformBaseModel):
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
              tenant:
                pattern: "^[a-z][a-z0-9-]*$"
                required: true
                description: "tenant identifier"
              region:
                pattern: "^[a-z]{2}-[a-z]+(-[0-9]+)?$"
                required: false
                description: "Deployment region"
          manifest:
            type: local
            path: ".strata/deployments"
          outputs:
            enabled: true
            path: ".strata/outputs"
            sensitive: redact
    """

    additional_properties: bool = Field(
        False,
        description="Allow properties not listed in the schema for deployments",
    )
    properties: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = Field(
        None,
        description="Deployment properties schema (pattern string or structured field with validation)",
    )
    manifest: Optional[ConfigurationManifestModel] = Field(
        None,
        description="Manifest storage configuration. When absent, manifests are not written.",
    )
    outputs: Optional[ConfigurationOutputsModel] = Field(
        None,
        description=(
            "Terraform output artifact storage. "
            "When absent, outputs are not written to a durable store "
            "(they are still available within the current deploy run for downstream stages)."
        ),
    )


class ConfigurationZoneModel(PlatformBaseModel):
    """A logical zone grouping one or more provider regions.

    Zones are used to enforce data residency constraints — a tenant
    restricted to zone 'eu' may only be deployed to providers whose
    region is in that zone's regions list.
    """

    name: PlatformName = Field(..., description="Unique zone name (e.g., 'eu', 'us', 'apac')")
    description: Optional[str] = Field(None, description="Human-readable description of this zone")
    regions: List[str] = Field(
        ...,
        min_length=1,
        description="Provider regions belonging to this zone (e.g., 'westeurope', 'northeurope')",
    )


class ConfigurationSpecModel(PlatformBaseModel):
    """Specification for the configuration model."""

    logging: Optional[ConfigurationLoggingModel] = Field(None, description="Logging configuration for the platform")
    layering: Optional[List[ConfigurationLayerModel]] = Field(
        None,
        description="Deployment hierarchy layers (defines artifact path structure and ordering)",
    )
    integrations: List[IntegrationModel] = Field(
        default_factory=list,
        description="External integrations that extend platform capabilities",
    )
    remotes: Optional[List[RemoteModel]] = Field(
        None, description="Named remote endpoints (artifact sources and deployment targets)"
    )
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Configuration lifecycle phases")
    configuration: Optional[Dict[str, Any]] = Field(None, description="List of configuration defaults")
    properties: Optional[Dict[str, Any]] = Field(None, description="List of configuration properties")
    deployment: Optional[ConfigurationDeploymentModel] = Field(
        None,
        description="Deployment configuration and schema for deployment.spec.properties validation",
    )
    providers: Optional[List[ConfigurationProviderModel]] = Field(None, description="List of supported providers")
    additional_topologies: bool = Field(
        False,
        description="Allow topology types not listed in the configuration",
    )
    topologies: Optional[List[ConfigurationTopologyModel]] = Field(None, description="List of supported topologies")
    security: Optional[ConfigurationSecurityModel] = Field(
        None,
        description="Security policies for store types and other security constraints",
    )
    zones: Optional[List[ConfigurationZoneModel]] = Field(
        None,
        description="Logical zones grouping provider regions for data residency enforcement",
    )
    policies: Optional[List[PolicyModel]] = Field(
        default_factory=list,
        description="Policy rules evaluated at validate, build, plan, and deploy phases",
    )
    audit: Optional[AuditConfigModel] = Field(
        None,
        description="Audit and deploy-log configuration (structure, sinks, retention)",
    )

    @model_validator(mode="after")
    def validate_unique_zones(self) -> "ConfigurationSpecModel":
        """Validate zone names are unique and each region appears in at most one zone."""
        if not self.zones:
            return self

        # Unique zone names
        zone_names = [z.name for z in self.zones]
        duplicates = [n for n in zone_names if zone_names.count(n) > 1]
        if duplicates:
            raise ValueError(f"Duplicate zone names in configuration: {', '.join(set(duplicates))}")

        # Each region must appear in at most one zone
        seen: dict[str, str] = {}
        for zone in self.zones:
            for region in zone.regions:
                if region in seen:
                    raise ValueError(
                        f"Region '{region}' is listed in both zone '{seen[region]}' and zone '{zone.name}'. "
                        "Each region must belong to exactly one zone."
                    )
                seen[region] = zone.name

        return self

    @model_validator(mode="after")
    def validate_unique_providers(self) -> "ConfigurationSpecModel":
        """Validate that all provider names are unique."""
        if self.providers:
            provider_names = [provider.name for provider in self.providers]
            duplicates = [name for name in provider_names if provider_names.count(name) > 1]
            if duplicates:
                raise ValueError(f"Duplicate provider names in configuration: {', '.join(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_unique_topologies(self) -> "ConfigurationSpecModel":
        """Validate that all topology types are unique."""
        if self.topologies:
            topology_types = [topo.type for topo in self.topologies]
            duplicates = [ttype for ttype in topology_types if topology_types.count(ttype) > 1]
            if duplicates:
                raise ValueError(f"Duplicate topology types in configuration: {', '.join(set(duplicates))}")
        return self

    @model_validator(mode="after")
    def validate_unique_layer_names(self) -> "ConfigurationSpecModel":
        """Validate that layer names are unique and last layer is 'environment'."""
        if self.layering:
            layer_names = [layer.name for layer in self.layering]
            if len(layer_names) != len(set(layer_names)):
                duplicates = [name for name in layer_names if layer_names.count(name) > 1]
                raise ValueError(f"Duplicate layer names found: {set(duplicates)}")

            # CRITICAL: Last layer must be named "environment"
            if layer_names[-1] != "environment":
                raise ValueError(
                    f"Last layer must be named 'environment', got '{layer_names[-1]}'. "
                    "This ensures artifact paths always end with environment identifier."
                )
        return self


class ConfigurationMetaModel(PlatformBaseModel):
    """Metadata for the configuration model."""

    name: PlatformName = Field(..., description="Unique name for the configuration resource.")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(None, description="Labels for categorization and filtering.")
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags.")


class ConfigurationModel(PlatformBaseModel):
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
    meta: ConfigurationMetaModel = Field(..., description="Metadata for the configuration model.")
    spec: ConfigurationSpecModel = Field(..., description="Specification for the configuration.")

    def get_remote_map(self) -> Dict[str, str]:
        """Return a ``{remote_name: deploy_path}`` mapping for resolving ``@remote_name/...`` references."""
        remotes = self.spec.remotes if self.spec and self.spec.remotes else {}
        if not remotes or len(remotes) == 0:
            return {}
        return {remote.name: remote.deploy_path for remote in remotes if remote.name and remote.deploy_path}
