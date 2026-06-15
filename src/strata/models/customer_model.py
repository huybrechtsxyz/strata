#!/usr/bin/env python3
"""Pydantic model for customer configuration validation."""

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
)


class CustomerReferencesModel(PlatformBaseModel):
    """References to variables, secrets, and features required by this customer's deployments.

    Lists the keys that must be defined in the environment configuration.
    Actual values and store backends are defined at environment/workspace level.
    """

    variables: VariableRefs = Field(
        None,
        description="List of variable keys this customer's deployments require from environment",
    )
    secrets: SecretRefs = Field(
        None,
        description="List of secret keys this customer's deployments require from environment",
    )
    features: FeatureRefs = Field(
        None,
        description="List of feature flag keys this customer's deployments require from environment",
    )


class CustomerSpecModel(PlatformBaseModel):
    """Customer specification: identity, data residency, environment composition, and runtime references."""

    # --- Identity ---
    code: PlatformName = Field(
        description="Short unique customer identifier (e.g., 'acme'). Must match meta.name and the filename."
    )
    name: str = Field(description="Human-readable customer display name (e.g., 'Acme Corporation')")

    # --- Data residency ---
    zones: List[str] = Field(
        min_length=1,
        description=(
            "Zone names this customer is allowed to deploy into. "
            "Validated against configuration.spec.zones at load time."
        ),
    )

    # --- Onboarding ---
    onboarded: Optional[date] = Field(
        None,
        description="ISO date the customer was onboarded (e.g., 2025-03-15). Informational only.",
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
            "Arbitrary key/value pairs for customer-specific settings. "
            "Injected as deployment properties at slot generation time. "
            "Example: {'crm_id': '42', 'invoice_prefix': 'ACM'}"
        ),
    )

    # --- Runtime references ---
    references: Optional[CustomerReferencesModel] = Field(
        None,
        description="Variables, secrets, and feature flags this customer's deployments require.",
    )

    @model_validator(mode="after")
    def validate_code_matches_name(self) -> "CustomerSpecModel":
        """Validate that spec.code matches meta.name (enforced by the service layer, not here)."""
        # The actual cross-field check (spec.code == meta.name) lives in the service
        # because this model does not have access to meta. Structural-only here.
        return self

    @model_validator(mode="after")
    def validate_unique_zones(self) -> "CustomerSpecModel":
        """Validate that the zones list contains no duplicates."""
        duplicates = [z for z in self.zones if self.zones.count(z) > 1]
        if duplicates:
            raise ValueError(f"Duplicate zone entries in customer spec: {', '.join(set(duplicates))}")
        return self


class CustomerMetaModel(PlatformBaseModel):
    """Customer metadata: name, annotations, labels, tags."""

    name: PlatformName = Field(description="Unique customer name (short code, e.g. 'acme')")
    annotations: Optional[Dict[str, Any]] = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: Optional[Dict[str, Any]] = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: Optional[List[Any]] = Field(None, description="Optional tags (list of values for categorization)")


class CustomerModel(PlatformBaseModel):
    """Root model for a customer configuration file (kind: customer)."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for customer configuration",
    )
    kind: Literal[PlatformKind.CUSTOMER] = Field(
        default=PlatformKind.CUSTOMER,
        frozen=True,
        description="Resource kind (always 'customer')",
    )
    meta: CustomerMetaModel = Field(description="Customer metadata (name, annotations, labels, tags)")
    spec: CustomerSpecModel = Field(description="Customer specification (identity, zones, environments, references)")
