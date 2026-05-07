#!/usr/bin/env python3
"""Pydantic models for module configuration validation."""

from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    Field,
)

from xyz_platform.models.common_models import (
    CommonLifecycleModel,
    FeatureRefs,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    SourceModel,
    VariableRefs,
)


class ModuleReferenceModel(BaseModel):
    """
    References to variables, secrets, and features required by this module.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.

    Similar to how SourceModel references repositories by name, this model
    references variable/secret/feature keys by name.
    """

    variables: VariableRefs = Field(None, description="List of variable keys this module requires from environment")
    secrets: SecretRefs = Field(None, description="List of secret keys this module requires from environment")
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this module requires from environment",
    )


class ModuleEndpointModel(BaseModel):
    """Model for a module endpoint configuration."""

    name: Optional[PlatformName] = Field(None, description="Name of the endpoint")
    label: Optional[str] = Field(None, description="Label for the endpoint")
    url: Optional[str] = Field(None, description="URL or address of the endpoint")
    type: Optional[str] = Field(None, description="Type of endpoint (e.g., http, tcp)")
    port: Optional[int] = Field(None, description="Port number for the endpoint")
    protocol: Optional[str] = Field(None, description="Protocol for the endpoint (e.g., tcp, udp)")


class ModuleCheckModel(BaseModel):
    """Model for a module health check configuration."""

    name: PlatformName = Field(description="Name of the health check")
    label: Optional[str] = Field(None, description="Label for the health check")
    target: Optional[str] = Field(None, description="Target resource for the health check")
    type: Optional[str] = Field(None, description="Type of health check (e.g., http, tcp, command)")
    interval: Optional[str] = Field(None, description="Interval between health checks (e.g., '30s')")
    timeout: Optional[str] = Field(None, description="Timeout for each health check (e.g., '5s')")
    retries: Optional[int] = Field(None, description="Number of retries before marking as unhealthy")
    command: Optional[List[str]] = Field(None, description="Command to run for 'command' type health checks")


class ModuleMountModel(BaseModel):
    """Model for a module mount configuration."""

    name: Optional[PlatformName] = Field(None, description="Name of the mount")
    type: Optional[str] = Field(None, description="Type of the mount (e.g., volume, bind)")
    change_mod: Optional[str] = Field(None, description="Permissions for the mount (e.g., '755')")
    target_path: Optional[str] = Field(None, description="Path inside the module")
    source_path: Optional[str] = Field(None, description="Source path of the mount")
    description: Optional[str] = Field(None, description="Description of the mount")


class ModulePropertiesModel(BaseModel):
    """Model for module-specific properties and configurations."""

    mounts: Optional[List[ModuleMountModel]] = Field(None, description="List of module mount configurations")
    checks: Optional[List[ModuleCheckModel]] = Field(None, description="List of module health check configurations")
    endpoints: Optional[List[ModuleEndpointModel]] = Field(None, description="List of module endpoint configurations")


class ModuleSpecModel(BaseModel):
    """Model for module spec (lifecycle, modules, validation)."""

    source: SourceModel = Field(description="Module deployment configuration")
    lifecycle: Optional[CommonLifecycleModel] = Field(None, description="Module-specific lifecycle hooks")
    properties: Optional[ModulePropertiesModel] = Field(
        None, description="Module-specific properties and configurations"
    )
    references: Optional[ModuleReferenceModel] = Field(
        None, description="Module references for variable and secret injection"
    )
    configuration: Optional[Dict[str, Any]] = Field(None, description="Module-specific configuration data")


class ModuleMetaModel(BaseModel):
    """Model for module metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique module name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(None, description="Optional list of tags for the module")


class ModuleModel(BaseModel):
    """Top-level model for a module resource."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for module configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.MODULE,
        frozen=True,
        description="Resource kind (always 'Module')",
    )
    meta: ModuleMetaModel = Field(description="Module metadata (name, annotations, labels, tags)")
    spec: ModuleSpecModel = Field(description="Module specification (lifecycle, modules, variables, secrets)")
