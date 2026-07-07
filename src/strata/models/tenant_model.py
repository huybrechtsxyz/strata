#!/usr/bin/env python3
"""Pydantic model for tenant configuration validation."""

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, model_validator

from strata.models.common_models import (
    FeatureRefs,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    SecretRefs,
    VariableRefs,
    check_unique_names,
)


class TenantReferencesModel(PlatformBaseModel):
    """References to variables, secrets, and features required by this tenant's deployments.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """

    variables: VariableRefs = Field(
        None,
        description="List of variable keys this tenant's deployments require from environment",
    )
    secrets: SecretRefs = Field(
        None,
        description="List of secret keys this tenant's deployments require from environment",
    )
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this tenant's deployments require from environment",
    )


class TenantSpecModel(PlatformBaseModel):
    """tenant specification: identity, data residency, environment composition, and runtime references."""

    # --- Identity ---
    code: PlatformName = Field(
        description="Short unique tenant identifier (e.g., 'acme'). Must match meta.name and the filename."
    )
    name: str = Field(description="Human-readable tenant display name (e.g., 'Acme Corporation')")

    # --- Data residency ---
    zones: List[str] = Field(
        min_length=1,
        description=(
            "Zone names this tenant is allowed to deploy into. Validated against configuration.spec.zones at load time."
        ),
    )

    # --- Onboarding ---
    onboarded: Optional[date] = Field(
        None,
        description="ISO date the tenant was onboarded (e.g., 2025-03-15). Informational only.",
    )

    # --- Environment composition ---
    environments: Optional[List[str]] = Field(
        None,
        description=(
            "Ordered list of environment file paths applied before deployment environments. "
            "Typically points to a tier file, e.g. ['environments/tiers/enterprise.yaml']. "
            "Validated against the filesystem during Phase 2."
        ),
    )

    # --- Custom key/value configuration ---
    configuration: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Arbitrary key/value pairs for tenant-specific settings. "
            "Injected as deployment properties at slot generation time. "
            "Example: {'crm_id': '42', 'invoice_prefix': 'ACM'}"
        ),
    )

    # --- Runtime references ---
    references: Optional[TenantReferencesModel] = Field(
        None,
        description="Variables, secrets, and feature flags this tenant's deployments require.",
    )

    @model_validator(mode="after")
    def validate_code_matches_name(self) -> "TenantSpecModel":
        """Validate that spec.code matches meta.name (enforced by the service layer, not here)."""
        # The actual cross-field check (spec.code == meta.name) lives in the service
        # because this model does not have access to meta. Structural-only here.
        return self

    @model_validator(mode="after")
    def validate_unique_zones(self) -> "TenantSpecModel":
        """Validate that the zones list contains no duplicates."""
        check_unique_names(self.zones, "zone entries in tenant spec")
        return self


class TenantMetaModel(PlatformBaseModel):
    """Tenant metadata: name, annotations, labels, tags."""

    name: PlatformName = Field(description="Unique tenant name (short code, e.g. 'acme')")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class TenantModel(PlatformBaseModel):
    """Root model for a tenant configuration file (kind: tenant)."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for tenant configuration",
    )
    kind: Literal[PlatformKind.TENANT] = Field(
        default=PlatformKind.TENANT,
        frozen=True,
        description="Resource kind (always 'Tenant')",
    )
    meta: TenantMetaModel = Field(description="Tenant metadata (name, annotations, labels, tags)")
    spec: TenantSpecModel = Field(description="tenant specification (identity, zones, environments, references)")
