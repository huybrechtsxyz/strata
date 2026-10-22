#!/usr/bin/env python3
"""Attribute schema validation, as a standalone kind.

Declares a real JSON Schema (2020-12) fragment that must validate against a
targeted `properties`/`custom`/`configuration` block of every document it
matches — see docs/design/dynamic-configuration-validation.md's Current Design
for the full design. Generalizes (and, per that doc's D5, replaces)
`providerconfig`'s own embedded `resource.spec.configuration` schema
sub-feature to every loosely-typed passthrough blob in the schema.

Phase 1 only (structural/Pydantic validation): `spec.schema` is stored as an
untyped passthrough `dict[str, Any]` — it is not compiled or checked against
the real JSON Schema meta-schema here. That happens in Phase 2's shared
evaluator (`strata.services.attributeconfig_evaluator`), which also owns the
hard non-retrieving-registry `$ref` security requirement; doing it here would
mean validating untrusted schema content without that safeguard in place.
"""

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)

#: Which `properties`/`custom`/`configuration` fields each kind's own top-level
#: `*SpecModel` actually declares — verified directly against those classes
#: (2026-10-22, docs/work/environment-value-metadata.md), not merely assumed
#: from `block`'s own `Literal`. A same-named field on a NESTED sub-model
#: (`DnsZoneModel.custom`, `FirewallRuleModel.custom`,
#: `WorkspaceResourceModel.configuration`, ...) does not count — `getattr
#: (candidate.model, "spec", None)` in `_check_attributeconfigs()` only ever
#: reads the top-level spec. Every kind absent here (`asset`, `attributeconfig`,
#: `configuration`, `dns`, `network`, `providerconfig`, `solution`,
#: `topologyconfig`, `version`) has none of the three at that level — `solution`
#: is additionally never indexed at all (semantic_checks.py), so it could never
#: be a reachable target either way.
BLOCKS_BY_KIND: dict[PlatformKind, frozenset[str]] = {
    PlatformKind.ARTIFACT: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.DEPLOYMENT: frozenset({"properties", "custom"}),
    PlatformKind.ENVIRONMENT: frozenset({"properties", "custom"}),
    PlatformKind.TENANT: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.WORKSPACE: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.FIREWALL: frozenset({"custom", "configuration"}),
    PlatformKind.INTEGRATION: frozenset({"custom", "configuration"}),
    PlatformKind.MODULE: frozenset({"custom", "configuration"}),
    PlatformKind.NAMESPACE: frozenset({"custom", "configuration"}),
    PlatformKind.PROVIDER: frozenset({"custom", "configuration"}),
    PlatformKind.RESOURCE: frozenset({"custom", "configuration"}),
}


class AttributeConfigTargetModel(PlatformBaseModel):
    """What this schema validates: which kind, which block, and (optionally)
    which documents of that kind, via a selector."""

    kind: PlatformKind = Field(description="The kind of document this schema targets (e.g. 'environment').")
    block: Literal["properties", "custom", "configuration"] = Field(
        description="Which passthrough blob field on the targeted kind this schema validates."
    )
    selector: dict[str, str] | None = Field(
        None,
        description="Optional field-path -> required-value equality constraints. Omitted means every "
        "document of `kind` matches. Equality only — no wildcards or regex.",
    )
    view: Literal["document", "effective"] = Field(
        "document",
        description="'document' (default): evaluate each matching document's own block in isolation, "
        "unchanged behaviour. 'effective': evaluate the merged `properties`/`custom` of each deployable "
        "deployment (workspace -> environments -> deployment) instead — only valid for "
        "`kind: deployment` and `block: properties`/`custom`.",
    )

    @model_validator(mode="after")
    def validate_block_exists_on_kind(self) -> "AttributeConfigTargetModel":
        """Reject a `(kind, block)` pair naming a field `kind`'s own spec model
        doesn't have — such a pair used to pass load-time validation and then
        silently evaluate every document of `kind` against `{}` forever
        (docs/work/environment-value-metadata.md's "Two real gaps" #2)."""
        allowed = BLOCKS_BY_KIND.get(self.kind, frozenset())
        if self.block not in allowed:
            has = ", ".join(sorted(allowed)) if allowed else "none"
            raise ValueError(f"block '{self.block}' does not exist on kind '{self.kind.value}' (has: {has})")
        return self

    @model_validator(mode="after")
    def validate_effective_view_is_deployment_properties_or_custom(self) -> "AttributeConfigTargetModel":
        """`view: effective` only has a merge to evaluate against for `deployment.properties`/`.custom`
        (`merge_workspace_environment_deployment_properties()`) — no such merge exists for `configuration`,
        or for any other kind's `properties`/`custom` (docs/work/environment-value-metadata.md Phase 2)."""
        if self.view == "effective" and (self.kind != PlatformKind.DEPLOYMENT or self.block == "configuration"):
            raise ValueError(
                "view: effective is only supported for target.kind: deployment and "
                f"target.block: properties/custom (got kind: '{self.kind.value}', block: '{self.block}')"
            )
        return self


class AttributeConfigSpecModel(PlatformBaseModel):
    """An attribute schema's target binding plus the real JSON Schema fragment."""

    target: AttributeConfigTargetModel = Field(description="What this schema validates.")
    # `schema` collides with `BaseModel`'s own deprecated v1-compat `.schema()`
    # classmethod (still present/typed in Pydantic v2) — aliased so the field
    # still loads from (and dumps to) a real `schema:` key in YAML.
    schema_: dict[str, Any] = Field(
        alias="schema",
        description="Verbatim JSON Schema (2020-12 assumed) fragment, authored directly — no "
        "strata-invented vocabulary. Evaluated by the shared evaluator, not here.",
    )


class AttributeConfigMetaModel(PlatformBaseModel):
    """Attribute config metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique name for this attribute schema document.")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class AttributeConfigModel(PlatformBaseModel):
    """Root model for an attribute schema document."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for attribute config",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.ATTRIBUTECONFIG,
        frozen=True,
        description="Platform kind (always 'attributeconfig')",
    )
    meta: AttributeConfigMetaModel = Field(description="Attribute config metadata (name, annotations, labels, tags)")
    spec: AttributeConfigSpecModel = Field(description="Attribute config specification (target, schema)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ATTRIBUTECONFIG)
