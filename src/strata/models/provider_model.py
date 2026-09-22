#!/usr/bin/env python3
"""Pydantic model for provider configuration validation."""

from typing import Annotated, Any

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
)

from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)


class ProviderPropertiesModel(PlatformBaseModel):
    """
    Provider configuration: cloud provider and datacenter location.
    """

    type: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Cloud or infrastructure provider (e.g., kamatera, local). Must match a provider type in configuration.yaml."
    )
    region: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Region of the datacenter used by the provider API to select the datacenter"
    )
    location: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Optional location of the datacenter (e.g., 'West US', 'eu-west-1'). Used for documentation and may be used by some providers for resource naming or tagging, but is not required for provider validation.",
    )
    organization: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Optional organization or subscription name/ID for this provider. Used for documentation and may be used by some providers for resource naming or tagging, but is not required for provider validation.",
    )
    version: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None = Field(
        None,
        description="Version constraint for the provider (e.g., '~>3.0')",
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


class ProviderSpecModel(PlatformBaseModel):
    """
    Provider specification containing properties and lifecycle configuration.
    """
    lifecycle: CommonLifecycleModel | None = Field(
        None,
        description="IaC workflow lifecycle phases, keyed by phase name "
        "(e.g. deploy_check, deploy_plan_before, deploy_provision, deploy_destroy_after)",
    )
    properties: ProviderPropertiesModel = Field(
        description="Provider configuration (cloud provider, IaC tool, datacenter location)"
    )
    authentication: AuthenticationModel | None = Field(
        None, description="Authentication configuration for cloud provider access"
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Raw provisioner-specific passthrough configuration (e.g. extra Terraform "
        "provider-block arguments such as 'skip_provider_registration' or 'partner_id'). "
        "Unlike `properties`, these keys are not validated by strata and are passed through "
        "as-is to the provisioner. Must be consumed by the corresponding builder/service — "
        "a field here is inert until that layer exists.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Optional custom key-value pairs for automation/bookkeeping (e.g. cost center, "
        "billing account). Not consumed by any provisioner — for external tooling/documentation "
        "only. Must be explicitly wired through by the builder/service layer if it needs to reach "
        "build output; it is not automatic.",
    )
    default_tags: dict[str, str] | None = Field(
        None,
        description="Default tags to apply to all resources created by this provider (ignored if provider "
        "doesn't support tagging). Deliberately distinct from meta.tags (a free-form list used for "
        "strata-internal categorization/documentation, not cloud tags). Optional (unlike "
        "ResourceSpecModel.default_tags) — not every provider account needs an organization-wide tagging "
        "policy; no custom_tags sibling either, since this is a broad provider-scope default, not an "
        "individually-tagged resource.",
    )


class ProviderMetaModel(PlatformBaseModel):
    """Provider metadata including name, annotations, labels, and tags."""

    name: PlatformName = Field(description="Unique provider name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering)",
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class ProviderModel(PlatformBaseModel):
    """Root model for a provider configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for provider configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PROVIDER,
        frozen=True,
        description="Resource kind (always 'Provider')",
    )
    meta: ProviderMetaModel = Field(description="Provider metadata (name, annotations, labels, tags)")
    spec: ProviderSpecModel = Field(description="Provider specification (properties, authentication, lifecycle)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.PROVIDER)
