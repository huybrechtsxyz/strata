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

from pydantic import Field, field_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)


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
