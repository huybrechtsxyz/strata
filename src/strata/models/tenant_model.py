#!/usr/bin/env python3
"""Pydantic model for tenant configuration validation.

A `Tenant` is a customer/organisation identity: who they are, which data
residency geographies they may deploy into, and the defaults every deployment
on their behalf inherits. In real use there is one per customer directory
(`customers/contoso/tenant.yaml`), and v1's `PathConventionModel` has a
dedicated `resolves: tenant` mechanism just to locate them.

Three deliberate differences from v1's `tenant_model.py`:

1. **`spec.code` is not ported.** v1 required it to equal `meta.name` — the
   same fact in two places. v1's own `validate_code_matches_name()` is a
   no-op whose comment concedes the model "does not have access to meta", so
   the real check had to live in the service layer. `meta.name` is the
   tenant code; there is nothing to keep in sync.
2. **`spec.name` renamed `display_name`.** It holds the human label ("GSK")
   while `meta.name` holds the identifier (`contoso`) — two different things
   called the same word. Matches `ProviderSpecModel.display_name`.
3. **`spec.environments` names Environment documents, not file paths.**
   v1 listed workspace-relative paths (`customers/contoso/tenant.env.yaml`);
   documents are now resolved by identity (ADR-0015). The real tenant env
   file already carries `meta.name: contoso-env`, so the name exists to
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
from typing import Annotated, Any, ClassVar

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.models.template_model import DocumentTemplateModel, ParamSetNames, check_name_rule
from strata.utils.names import check_unique_names

_PARTIAL_DESCRIPTION = (
    "When true this is a base for other tenants (`extends`), not a tenant of its own: `display_name` may be absent, "
    "no deployment may name it in `tenant`, and no `${value:}` token may read it."
)
_EXTENDS_DESCRIPTION = (
    "Name of a base Tenant whose spec is merged into this one when the solution loads. Child always wins: "
    "`display_name` and `onboarded` are replaced, `geographies` is replaced by the child's list when it sets one, "
    "`environments` is the base's list then the child's, and `properties`, `custom` and `configuration` deep-merge "
    "per leaf key. Circular chains are rejected."
)
_PARAMS_DESCRIPTION = (
    "Parameter sets this tenant reads, per role: `{tenant: tenant-initech}`, or a list per role merged in order. "
    "Any document of the extends chain may name sets: per role they merge root first, leaf last, later winning per "
    "leaf key. `${param:<role>.<key>}` in the spec of any document of the chain reads the merged values."
)
_NAME_RULE_DESCRIPTION = (
    "The name every tenant built on this one must have, with `${param:<role>.<key>}` tokens filled from that "
    "tenant's parameter sets (`${param:tenant.code}`). Only `${param:}` tokens are allowed."
)


class TenantSpecModel(PlatformBaseModel):
    """A customer or organisation: its display name, the data-residency geographies it may deploy into, and
    defaults (`environments`, `properties`, `custom`) that every deployment for it inherits.

    Design notes:
    Tenant specification: identity, data residency, and deployment defaults.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "Geographies must be unique.",
        "Environment references must be unique, because a repeat would merge twice.",
        "A tenant needs `display_name`. A `partial: true` tenant, or one with `extends`, is exempt because its "
        "missing fields come from the base.",
    )

    partial: bool = Field(default=False, description=_PARTIAL_DESCRIPTION)
    extends: Annotated[PlatformName, References(PlatformKind.TENANT)] | None = Field(
        None, description=_EXTENDS_DESCRIPTION
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        description="Human-readable tenant name (e.g. 'Acme Corporation'). The tenant's identifier/code is "
        "meta.name (e.g. 'contoso'). Required, unless the tenant is `partial` or gets it through `extends`.",
    )
    geographies: list[str] | None = Field(
        default=None,
        description="Data-residency boundaries this tenant may deploy into, for example `europe`. "
        "Each must be the `geography` of some region in a ProviderConfig. A deployment "
        "for this tenant is flagged when its provider's region lies in a geography "
        "that is not listed here. Omit for no restriction.\n\n"
        "Design notes: Data residency boundaries this tenant may deploy into (e.g. "
        "'europe'). Each must appear as a `geography` on some region in a "
        "ProviderConfig's spec.regions — checked by "
        "`TenantService.validate_geographies_against_provider_configs()`. A tenant's "
        "allowed set is what a zone-isolation policy tests a planned resource's region "
        "against. None/omitted means this tenant has no data-residency constraint at "
        "all — the real, common case (docs/work/tenant-zone-policy.md: no real "
        "tenant in the one production consumer checked has this populated yet).",
    )
    onboarded: date | None = Field(
        None, description="ISO date the tenant was onboarded (e.g. 2026-03-15). Informational only."
    )
    environments: list[Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]] | None = Field(
        None,
        description="Names of Environment documents that merge before the deployment's own "
        "environments, so the deployment's values win. Use for tenant-wide defaults "
        "such as shared variables or feature flags.\n\n"
        "Design notes: Names of Environment documents merged in BEFORE a deployment's "
        "own environments, so deployment values win. Use for tenant-wide defaults "
        "(shared variables, feature flags) without repeating them per deployment.",
    )
    properties: dict[str, Any] | None = Field(
        None,
        description="Tenant-wide properties, merged as the base layer into the `properties` of "
        "every deployment for this tenant. Deployment values win.\n\n"
        "Design notes: Tenant-wide deployment properties, merged as a base layer into "
        "spec.properties of every deployment referencing this tenant. Deployment "
        "values take precedence.",
    )
    configuration: dict[str, Any] | None = Field(
        None,
        description="Tenant-specific settings, for example a CRM id or billing code, passed to "
        "Terraform as `strata.tenant.configuration`. Not merged into the deployment's "
        "properties. Add an `attributeconfig` with `target.kind: tenant` and "
        "`target.block: configuration` to validate them.\n\n"
        "Design notes: Tenant-specific settings emitted verbatim to the provisioner "
        "(e.g. CRM ID, billing code) — NOT merged into deployment properties, unlike "
        "`properties` above.",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Tenant-wide custom values, merged as the base layer into the `custom` values "
        "of every deployment for this tenant. Deployment values win.\n\n"
        "Design notes: Tenant-wide custom data merged as a base layer into spec.custom "
        "of every deployment referencing this tenant. Deployment values take "
        "precedence.",
    )

    @model_validator(mode="after")
    def validate_unique_geographies(self) -> "TenantSpecModel":
        """Geography entries must be unique, when declared at all."""
        if self.geographies:
            check_unique_names(self.geographies, "geography entries in tenant spec")
        return self

    @model_validator(mode="after")
    def validate_unique_environments(self) -> "TenantSpecModel":
        """Environment references must be unique — a repeat would merge twice."""
        if self.environments:
            check_unique_names(self.environments, "environment references in tenant spec")
        return self

    @model_validator(mode="after")
    def validate_complete_unless_partial(self) -> "TenantSpecModel":
        """A tenant needs `display_name`, unless it is a `partial: true` base or gets it through `extends`.

        The merged result is validated without `extends`, so the check still applies to every tenant in use.
        """
        if not (self.partial or self.extends or self.display_name):
            raise ValueError(
                "Tenant is missing required field 'display_name'. Set 'partial: true' if this is a reusable "
                "base, or 'extends' if another tenant supplies it."
            )
        return self


class TenantMetaModel(PlatformBaseModel):
    """Tenant metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Tenant code — the unique identifier (e.g. 'contoso')")
    params: ParamSetNames | None = Field(None, description=_PARAMS_DESCRIPTION)
    name_rule: str | None = Field(None, min_length=1, description=_NAME_RULE_DESCRIPTION)
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")

    @field_validator("name_rule")
    @classmethod
    def validate_name_rule_tokens(cls, value: str | None) -> str | None:
        """Only well-formed `${param:}` tokens: a run-time token has no value when names are checked."""
        return check_name_rule(value)


class TenantModel(PlatformBaseModel):
    """A customer or organisation identity. `meta.name` is the tenant code, for example `contoso`. Create one
    per customer and reference it from a deployment's `tenant`. Typically one per customer directory.

    Design notes:
    Root model for a tenant configuration file.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A tenant cannot extend itself; longer extends cycles are rejected when the solution loads.",
    )

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
    spec: TenantSpecModel = Field(description="Tenant specification (identity, geographies, environments, defaults)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.TENANT)

    @model_validator(mode="after")
    def validate_not_self_extending(self) -> "TenantModel":
        """A tenant cannot extend itself; longer cycles are rejected when the chain is resolved."""
        if self.spec.extends and self.spec.extends == self.meta.name:
            raise ValueError(f"Tenant '{self.meta.name}' cannot extend itself.")
        return self


class TenantTemplateSpecModel(PlatformBaseModel):
    """The spec of a tenant template, as written: only `partial` and `extends` are read before substitution.

    Design notes:
    Every other field is kept as written and validated per tenant that resolves through it, once `${param:}` is
    substituted (`base-parameters-v2.md` (work doc), "Templates are validated per instance").
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True, str_strip_whitespace=True)

    partial: bool = Field(default=False, description=_PARTIAL_DESCRIPTION)
    extends: Annotated[PlatformName, References(PlatformKind.TENANT)] | None = Field(
        None, description=_EXTENDS_DESCRIPTION
    )


class TenantTemplateModel(DocumentTemplateModel):
    """A tenant whose spec reads parameter sets (`${param:<role>.<key>}`): meta validated, spec as written.

    Design notes:
    Not a kind of its own and never exported as a schema: the loader picks it over `TenantModel` when the spec holds
    a `${param:}` token. Resolution (`controllers/document_chains.py`) replaces a non-partial one in the index with
    the `TenantModel` it resolves to.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for tenant configuration"
    )
    kind: PlatformKind = Field(default=PlatformKind.TENANT, frozen=True, description="Platform kind (always 'tenant')")
    meta: TenantMetaModel = Field(description="Tenant metadata (name, params, annotations, labels, tags)")
    spec: TenantTemplateSpecModel = Field(description="Tenant specification, as written")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.TENANT)
