#!/usr/bin/env python3
"""Pydantic model for parameter set validation (`base-parameters-v2.md` (work doc), plan step 1a).

A parameter set is one named set of plain values: the instance data of a spoke, a hub or a customer.
A deployment, environment or tenant names it under a role in `meta.params`, and a base reads it with
`${param:role.key}`. Those two parts come in later steps; this model is only the document itself.

Values are plain: no `${...}` token anywhere in `spec.values`, in a key or in a value. A parameter set
then depends on nothing and can be loaded first, and a value can never smuggle a `${secret:}` into a
document. The usual escape still works: `$${var:x}` is the literal text `${var:x}`.
"""

from typing import Any, ClassVar

from pydantic import Field, field_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.value_tokens import has_value_tokens


def _token_locations(node: Any, path: str) -> list[str]:
    """The path of every key or string under `node` that holds a `${...}` token (escapes ignored)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(key, str) and has_value_tokens(key):
                found.append(child)
            found.extend(_token_locations(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_token_locations(item, f"{path}[{index}]"))
    elif isinstance(node, str) and has_value_tokens(node):
        found.append(path)
    return found


class ParameterSetSpecModel(PlatformBaseModel):
    """Parameter set specification: the values themselves.

    Design notes:
    `values` is free-form on purpose, like a Helm values file: the shape is the solution's own, and an
    attributeconfig (`kind: parameterset, block: values`, a later phase) is where a solution constrains it.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "values holds plain YAML only: no ${...} token in any key or value. Write $${...} for the literal text.",
        "A parameter set is plain text in git: never put a secret in it. Read secrets with ${secret:} in the "
        "document's spec instead.",
    )

    values: dict[str, Any] = Field(
        description="The values: a map of any YAML (scalars, lists and nested maps). A document that names "
        "this set under a role in meta.params reads them with ${param:<role>.<key>}; nested maps are walked "
        "with dots (${param:spoke.network.cidr}). No ${...} tokens: values are plain."
    )

    @field_validator("values")
    @classmethod
    def validate_plain_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject any `${...}` token in a key or value of `values`; an escaped `$${...}` is literal text."""
        found = _token_locations(value, "values")
        if found:
            raise ValueError(
                f"a parameter set holds plain values, but {', '.join(found)} holds a ${{...}} token. "
                "Move the token into the document that reads the value, or write $${...} for the literal text."
            )
        return value


class ParameterSetMetaModel(PlatformBaseModel):
    """Parameter set metadata: the name documents use in `meta.params`."""

    name: PlatformName = Field(description="Unique parameter set name, named in a document's meta.params")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None,
        description="Optional labels (key-value pairs for classification/filtering). A label such as "
        "role: spoke is only for attributeconfig selectors: the role a set's values are read under comes "
        "from the meta.params of the document that names it.",
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class ParameterSetModel(PlatformBaseModel):
    """One named set of plain values: the instance data of a spoke, a hub, a customer or a tenant. A
    deployment, environment or tenant names it under a role in `meta.params`, and its base reads the values
    with `${param:<role>.<key>}`, so one base serves many instances.

    Design notes:
    Root model for a parameter set document (`base-parameters-v2.md` (work doc)). Step 1a: the kind only;
    nothing reads it yet.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for parameter set configuration"
    )
    kind: PlatformKind = Field(
        default=PlatformKind.PARAMETERSET, frozen=True, description="Platform kind (always 'parameterset')"
    )
    meta: ParameterSetMetaModel = Field(description="Parameter set metadata (name, annotations, labels, tags)")
    spec: ParameterSetSpecModel = Field(description="Parameter set specification (the values)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.PARAMETERSET)
