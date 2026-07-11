#!/usr/bin/env python3
"""Pydantic model for environment configuration validation."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from strata.models.audit_config_model import AuditConfigModel
from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    check_unique_names,
    validate_slot_type,
)
from strata.models.promotion_model import EnvironmentPromotionModel
from strata.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
    validate_unique_feature_keys,
    validate_unique_secret_keys,
    validate_unique_variable_keys,
)
from strata.models.workspace_model import OutputFileModel


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
        "Examples: '@haven/terraform/waf/listeners/*.tf', 'tenants/acme/overrides.tfvars'",
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


class EnvironmentServiceImageOverrideModel(PlatformBaseModel):
    """Override a single service's container image within a module."""

    name: PlatformName = Field(description="Service name within the module to override")
    image: str = Field(
        description="Container image reference (registry/name:tag)",
        min_length=1,
    )


class EnvironmentModuleOverrideModel(PlatformBaseModel):
    """
    Environment-specific module overrides.

    Targets a module by name. Optionally narrow scope with resource, namespace,
    or slot_type when the same module appears in multiple places.
    """

    module: PlatformName = Field(description="Module meta.name to override")
    resource: Optional[PlatformName] = Field(
        None,
        description="Narrow to module within this resource (optional)",
    )
    namespace: Optional[PlatformName] = Field(
        None,
        description="Narrow to module within this namespace (optional)",
    )
    slot_type: Optional[str] = Field(
        None,
        description="Narrow to specific deployment slot (main, staging, canary, sidecar, init)",
    )
    enabled: Optional[bool] = Field(
        None,
        description="Override whether this module is enabled/deployed in this environment",
    )
    chart_version: Optional[str] = Field(
        None,
        description="Override Helm chart version for this module",
    )
    services: Optional[List[EnvironmentServiceImageOverrideModel]] = Field(
        None,
        description="Override container images for specific services within the module",
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

    @model_validator(mode="after")
    def validate_scope_not_both(self) -> "EnvironmentModuleOverrideModel":
        """Ensure resource and namespace are not both set."""
        if self.resource is not None and self.namespace is not None:
            raise ValueError("Cannot specify both 'resource' and 'namespace' — use one to narrow scope")
        return self

    @model_validator(mode="after")
    def validate_unique_service_names(self) -> "EnvironmentModuleOverrideModel":
        """Ensure service override names are unique within this module override."""
        if self.services:
            check_unique_names([s.name for s in self.services], "service overrides")
        return self


class EnvironmentProviderOverrideModel(PlatformBaseModel):
    """
    Environment-specific provider overrides.

    Mirrors WorkspaceProviderModel fields (all optional except provider identifier).
    Values are merged with workspace provider configuration.
    Note: Provider configuration is in the provider file itself, not in workspace.
    """

    provider: PlatformName = Field(description="Provider name to override (must match a provider in the workspace)")
    file: Optional[str] = Field(
        None,
        description=(
            "Replace the provider file binding for this environment. "
            "Supports the same path formats as workspace provider file references "
            "(@repo/path, relative, absolute). "
            "When set, the workspace's default provider file is ignored and this file "
            "is loaded instead, enabling the same workspace to target different regions "
            "or cloud accounts per deployment."
        ),
    )
    description: Optional[str] = Field(
        None,
        description="Override provider description",
    )
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Override individual properties in the loaded provider file's spec.properties "
            "(e.g. region, engine, version). Applied after file resolution. "
            "Keys that do not correspond to known spec.properties fields are logged and skipped."
        ),
    )


class EnvironmentRemoteOverrideModel(PlatformBaseModel):
    """Override a remote's reference (version/branch/tag) for this environment.

    Only the ``reference`` field is overridable.  Structural fields (repository
    URL, type, source_path, deploy_path) are defined once in the configuration
    file and must not vary per environment.
    """

    remote: PlatformName = Field(
        description="Name of the remote to override (must match a name in configuration spec.remotes)"
    )
    reference: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Version, tag, branch, or commit SHA to use instead of the configuration default"
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
    remotes: Optional[List[EnvironmentRemoteOverrideModel]] = Field(
        None,
        description="Remote reference overrides — pin a remote to a specific version/tag/branch for this environment",
    )
    output_files: Optional[List[OutputFileModel]] = Field(
        None,
        description=(
            "Additional output file definitions appended to the workspace provisioner's files[]. "
            "Additive only — cannot remove or replace workspace-level file definitions."
        ),
    )

    @model_validator(mode="after")
    def validate_unique_overrides(self) -> "EnvironmentOverridesModel":
        """Validate that override references are unique."""
        errors = []

        # Validate unique resource override names
        if self.resources:
            try:
                check_unique_names([res.resource for res in self.resources], "resource overrides")
            except ValueError as e:
                errors.append(str(e))

        # Validate unique module overrides (module+resource+namespace+slot_type combination)
        if self.modules:
            module_keys = [
                f"{mod.module}:{mod.resource or ''}:{mod.namespace or ''}:{mod.slot_type or ''}" for mod in self.modules
            ]
            try:
                check_unique_names(module_keys, "module overrides")
            except ValueError as e:
                errors.append(str(e))

        # Validate unique provider override names
        if self.providers:
            try:
                check_unique_names([prov.provider for prov in self.providers], "provider overrides")
            except ValueError as e:
                errors.append(str(e))

        # Validate unique remote override names
        if self.remotes:
            try:
                check_unique_names([str(rem.remote) for rem in self.remotes], "remote overrides")
            except ValueError as e:
                errors.append(str(e))

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
    audit: Optional[AuditConfigModel] = Field(
        None,
        description="Environment-level audit overrides (structure, sinks, retention)",
    )
    promotion: Optional[EnvironmentPromotionModel] = Field(
        None,
        description="Promotion membership: declares which strategy and ring this environment belongs to",
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
        None,
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
