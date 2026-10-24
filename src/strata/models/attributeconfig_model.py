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

#: Which `properties`/`custom`/`configuration`/`variables`/`features` fields each kind's own top-level
#: `*SpecModel` actually declares — verified directly against those classes
#: (2026-10-22, docs/archive/environment-value-metadata.md), not merely assumed
#: from `block`'s own `Literal`. A same-named field on a NESTED sub-model
#: (`DnsZoneModel.custom`, `FirewallRuleModel.custom`,
#: `WorkspaceResourceModel.configuration`, ...) does not count — `getattr
#: (candidate.model, "spec", None)` in `_check_attributeconfigs()` only ever
#: reads the top-level spec. Every kind absent here (`asset`, `attributeconfig`,
#: `configuration`, `dns`, `network`, `providerconfig`, `solution`,
#: `topologyconfig`, `version`) has none of the five at that level — `solution`
#: is additionally never indexed at all (semantic_checks.py), so it could never
#: be a reachable target either way.
#:
#: Only consulted for `view: document` (see `validate_block_exists_on_kind()`)
#: — `view: effective` always merges from a different source than `target.
#: kind`'s own spec for `variables`/`features` (only `EnvironmentSpecModel`
#: declares those, never `DeploymentSpecModel`), so this table does not, and
#: cannot, describe effective-view validity; `secrets` is deliberately never a
#: `block` value at all (see `AttributeConfigTargetModel.block`'s own docstring).
BLOCKS_BY_KIND: dict[PlatformKind, frozenset[str]] = {
    PlatformKind.ARTIFACT: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.DEPLOYMENT: frozenset({"properties", "custom"}),
    PlatformKind.ENVIRONMENT: frozenset({"properties", "custom", "variables", "features"}),
    PlatformKind.TENANT: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.WORKSPACE: frozenset({"properties", "custom", "configuration"}),
    PlatformKind.FIREWALL: frozenset({"custom", "configuration"}),
    PlatformKind.INTEGRATION: frozenset({"custom", "configuration"}),
    PlatformKind.MODULE: frozenset({"custom", "configuration"}),
    PlatformKind.NAMESPACE: frozenset({"custom", "configuration"}),
    PlatformKind.PROVIDER: frozenset({"custom", "configuration"}),
    PlatformKind.RESOURCE: frozenset({"custom", "configuration"}),
}

#: `view: effective` block/kind validity (docs/archive/environment-value-metadata.md) — independent of
#: `BLOCKS_BY_KIND`: `target.kind` is always `deployment` regardless of which of these blocks is checked, since
#: `deployment` is the one thing every merge (`properties`/`custom`'s workspace+environments+deployment,
#: `variables`/`features`'s environments-only, `composite`'s all four) is scoped against, not necessarily the kind
#: that owns the field.
_EFFECTIVE_VIEW_BLOCKS: frozenset[str] = frozenset({"properties", "custom", "variables", "features", "composite"})


class AttributeConfigTargetModel(PlatformBaseModel):
    """What this schema validates: which kind, which block, and (optionally)
    which documents of that kind, via a selector."""

    kind: PlatformKind = Field(description="The kind of document this schema targets (e.g. 'environment').")
    block: Literal["properties", "custom", "configuration", "variables", "features", "composite"] = Field(
        description="Which passthrough blob field on the targeted kind this schema validates. 'composite' (view: "
        "effective only) evaluates one object holding the merged properties/custom/variables/features together, "
        "for rules that span blocks. 'secrets' is "
        "deliberately never a valid value — a failing schema's message can echo the offending value verbatim, "
        "and a `store: constant` secret's value IS the literal secret (docs/archive/environment-value-metadata.md)."
    )
    selector: dict[str, str] | None = Field(
        None,
        description="Optional field-path -> required-value equality constraints. Omitted means every "
        "document of `kind` matches. Equality only — no wildcards or regex.",
    )
    view: Literal["document", "effective"] = Field(
        description="Required, no default: the two views check different data. 'document': evaluate each "
        "matching document's own block in isolation. 'effective': evaluate the merged "
        "`properties`/`custom`/`variables`/`features` of each deployable deployment instead — only valid for "
        "`kind: deployment`.",
    )

    # Must stay defined before validate_block_exists_on_kind(): Pydantic stops at the first failing after-validator.
    @model_validator(mode="after")
    def validate_composite_requires_effective_view(self) -> "AttributeConfigTargetModel":
        """`composite` is a merged-only view of four blocks; no single document has it as a field."""
        if self.block == "composite" and self.view != "effective":
            raise ValueError("block 'composite' requires view: effective")
        return self

    @model_validator(mode="after")
    def validate_block_exists_on_kind(self) -> "AttributeConfigTargetModel":
        """`view: document` only: reject a `(kind, block)` pair naming a field `kind`'s own spec model
        doesn't have — such a pair used to pass load-time validation and then
        silently evaluate every document of `kind` against `{}` forever
        (docs/archive/environment-value-metadata.md's "Two real gaps" #2).

        Does not apply to `view: effective`: its merge source is never
        literally `target.kind`'s own spec for `variables`/`features` (only
        `EnvironmentSpecModel` declares those, never `DeploymentSpecModel`) —
        `validate_effective_view_supported()` is the sole validity rule there.
        """
        if self.view != "document":
            return self
        allowed = BLOCKS_BY_KIND.get(self.kind, frozenset())
        if self.block not in allowed:
            has = ", ".join(sorted(allowed)) if allowed else "none"
            raise ValueError(f"block '{self.block}' does not exist on kind '{self.kind.value}' (has: {has})")
        return self

    @model_validator(mode="after")
    def validate_effective_view_supported(self) -> "AttributeConfigTargetModel":
        """`view: effective` only has a merge to evaluate against for `kind: deployment` + a block in
        `_EFFECTIVE_VIEW_BLOCKS` — `properties`/`custom` via `merge_workspace_environment_deployment_
        properties()`, `variables`/`features` via `merge_environment_models()` (docs/archive/
        environment-value-metadata.md's `variables`/`features` design). No merge exists for `configuration`,
        or for any block on any other kind."""
        if self.view == "effective" and (
            self.kind != PlatformKind.DEPLOYMENT or self.block not in _EFFECTIVE_VIEW_BLOCKS
        ):
            raise ValueError(
                "view: effective is only supported for target.kind: deployment and "
                f"target.block: {', '.join(sorted(_EFFECTIVE_VIEW_BLOCKS))} "
                f"(got kind: '{self.kind.value}', block: '{self.block}')"
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
