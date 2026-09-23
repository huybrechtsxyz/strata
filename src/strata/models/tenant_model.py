#!/usr/bin/env python3
"""Pydantic model for tenant configuration validation.

A `Tenant` is a customer/organisation identity: who they are, which data
residency geographies they may deploy into, and the defaults every deployment
on their behalf inherits. In real use there is one per customer directory
(`customers/c0062/tenant.yaml`), and v1's `PathConventionModel` has a
dedicated `resolves: tenant` mechanism just to locate them.

Three deliberate differences from v1's `tenant_model.py`:

1. **`spec.code` is not ported.** v1 required it to equal `meta.name` — the
   same fact in two places. v1's own `validate_code_matches_name()` is a
   no-op whose comment concedes the model "does not have access to meta", so
   the real check had to live in the service layer. `meta.name` is the
   tenant code; there is nothing to keep in sync.
2. **`spec.name` renamed `display_name`.** It holds the human label ("GSK")
   while `meta.name` holds the identifier (`c0062`) — two different things
   called the same word. Matches `ProviderPropertiesModel.display_name`.
3. **`spec.environments` names Environment documents, not file paths.**
   v1 listed workspace-relative paths (`customers/c0062/tenant.env.yaml`);
   documents are now resolved by identity (ADR-0015). The real tenant env
   file already carries `meta.name: c0062-env`, so the name exists to
   reference.
4. **`spec.zones` renamed `spec.geographies`.** v1's "zones" and v2's
   `ProviderConfigRegionModel.geography` are the same concept — a
   compliance/deployment boundary grouping several regions, both using
   'europe' as the example value. v1 validated zones against an unported
   `configuration.spec.zones` section; v2 derives them from the region
   registry that already exists, so the check is no longer blocked.

No `spec.references` (ADR-0002): Value bindings are checked against a real
Environment in Phase 2, not an internal declared-keys list.

No cloud tags (ADR-0017): a tenant is an identity/grouping concept, not a
provisioned resource.
"""

from datetime import date
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.utils.names import check_unique_names


class TenantSpecModel(PlatformBaseModel):
    """Tenant specification: identity, data residency, and deployment defaults."""

    display_name: str = Field(
        min_length=1,
        description="Human-readable tenant name (e.g. 'Acme Corporation'). The tenant's identifier/code is "
        "meta.name (e.g. 'c0062').",
    )
    geographies: list[str] = Field(
        min_length=1,
        description="Data residency boundaries this tenant may deploy into (e.g. 'europe'). Each must appear "
        "as a `geography` on some region in a ProviderConfig's spec.regions — checked by "
        "`TenantService.validate_geographies_against_provider_configs()`. A tenant's allowed set is what a "
        "zone-isolation policy tests a planned resource's region against.",
    )
    onboarded: date | None = Field(
        None, description="ISO date the tenant was onboarded (e.g. 2026-03-15). Informational only."
    )
    environments: list[Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]] | None = Field(
        None,
        description="Names of Environment documents merged in BEFORE a deployment's own environments, so "
        "deployment values win. Use for tenant-wide defaults (shared variables, feature flags) without "
        "repeating them per deployment.",
    )
    properties: dict[str, Any] | None = Field(
        None,
        description="Tenant-wide deployment properties, merged as a base layer into spec.properties of every "
        "deployment referencing this tenant. Deployment values take precedence.",
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Tenant-specific settings emitted verbatim to the provisioner (e.g. CRM ID, billing "
        "code) — NOT merged into deployment properties, unlike `properties` above.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Tenant-wide custom data merged as a base layer into spec.custom of every deployment "
        "referencing this tenant. Deployment values take precedence.",
    )

    @model_validator(mode="after")
    def validate_unique_geographies(self) -> "TenantSpecModel":
        """Geography entries must be unique."""
        check_unique_names(self.geographies, "geography entries in tenant spec")
        return self

    @model_validator(mode="after")
    def validate_unique_environments(self) -> "TenantSpecModel":
        """Environment references must be unique — a repeat would merge twice."""
        if self.environments:
            check_unique_names(self.environments, "environment references in tenant spec")
        return self


class TenantMetaModel(PlatformBaseModel):
    """Tenant metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Tenant code — the unique identifier (e.g. 'c0062')")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class TenantModel(PlatformBaseModel):
    """Root model for a tenant configuration file."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for tenant configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.TENANT,
        frozen=True,
        description="Platform kind (always 'tenant')",
    )
    meta: TenantMetaModel = Field(description="Tenant metadata (name, annotations, labels, tags)")
    spec: TenantSpecModel = Field(
        description="Tenant specification (identity, geographies, environments, defaults)"
    )

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.TENANT)
