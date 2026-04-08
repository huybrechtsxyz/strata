#!/usr/bin/env python3
"""Pydantic model for provider configuration validation."""

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
)
from typing import List, Dict, Optional, Annotated, Any

from xyz_platform.models.common_models import (
    CommonLifecycleModel,
    FeatureRefs,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
)
from xyz_platform.models.auth_models import AuthenticationModel


class ProviderReferencesModel(BaseModel):
    """
    References to variables, secrets, and features required by this provider.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """

    variables: VariableRefs = Field(
        None,
        description="List of variable keys this provider requires from environment",
    )
    secrets: SecretRefs = Field(
        None, description="List of secret keys this provider requires from environment"
    )
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this provider requires from environment",
    )


class ProviderPropertiesModel(BaseModel):
    """
    Provider configuration: cloud provider and datacenter location.
    """

    type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = (
        Field(
            description="Cloud or infrastructure provider (e.g., kamatera, local). Must match a provider type in configuration.yaml."
        )
    )
    region: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = (
        Field(
            description="Region of the datacenter used by the provider API to select the datacenter"
        )
    )
    engine: Optional[
        Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    ] = Field(
        None,
        description="Infrastructure as Code (IaC) engine to use for this provider (e.g., azurerm, azureapi)",
    )
    version: Optional[
        Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    ] = Field(
        None,
        description="Version constraint for the IaC engine (e.g., '~>3.0')",
    )

    @field_validator("type")
    @classmethod
    def validate_type_format(cls, v: str) -> str:
        """
        Validate provider type format (Phase 1: static validation only).
        Note: StringConstraints already ensures non-empty after stripping.
        Dynamic validation against configuration happens in service layer (Phase 2).
        """
        return v

    @field_validator("region")
    @classmethod
    def validate_region_format(cls, v: str) -> str:
        """
        Validate region format (Phase 1: static validation only).
        Note: StringConstraints already ensures non-empty after stripping.
        Dynamic validation against configuration happens in service layer (Phase 2).
        """
        return v


class ProviderSpecModel(BaseModel):
    """
    Provider specification containing properties, references, and lifecycle configuration.
    """

    lifecycle: Optional[CommonLifecycleModel] = Field(
        None,
        description="IaC workflow lifecycle phases (setup, validate, plan, apply, output, destroy)",
    )
    properties: ProviderPropertiesModel = Field(
        description="Provider configuration (cloud provider, IaC tool, datacenter location)"
    )
    authentication: Optional[AuthenticationModel] = Field(
        None, description="Authentication configuration for cloud provider access"
    )
    references: Optional[ProviderReferencesModel] = Field(
        None,
        description="Variable and secret mappings for runtime configuration injection",
    )
    custom: Optional[Dict[str, Any]] = Field(
        None, description="Optional custom key-value pairs for automation"
    )
    default_tags: Optional[Dict[str, str]] = Field(
        None,
        description="Default tags to apply to all resources created by this provider (ignored if provider doesn't support tagging)",
    )


class ProviderMetaModel(BaseModel):
    """Provider metadata including name, annotations, labels, and tags."""

    name: PlatformName = Field(description="Unique provider name")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: Optional[List[Any]] = Field(
        None, description="Optional tags (list of values for categorization)"
    )


class ProviderModel(BaseModel):
    """Root model for a provider configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for provider configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PROVIDER,
        frozen=True,
        description="Resource kind (always 'Provider')",
    )
    meta: ProviderMetaModel = Field(
        description="Provider metadata (name, annotations, labels, tags)"
    )
    spec: ProviderSpecModel = Field(
        description="Provider specification (properties, references, lifecycle)"
    )
