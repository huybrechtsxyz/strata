#!/usr/bin/env python3
"""What every kind that reads parameter sets shares (docs/design/parameter-sets.md).

A deployment, an environment and a tenant may name parameter sets per role in `meta.params`, require a name with
`meta.name_rule`, and read the values with `${param:<role>.<key>}` in their `spec`. A document whose spec holds such
a token is a **template**: the loader validates its `meta`, its `extends` and `partial`, and the syntax of its
tokens, and keeps the rest of its `spec` as written. The resolver validates it once per document that resolves
through it, after the values are filled in ("Templates are validated per instance").
"""

from typing import Annotated, Any

from pydantic import model_validator

from strata.models.common_models import PlatformBaseModel, PlatformKind, PlatformName
from strata.models.reference_fields import References
from strata.utils.value_tokens import find_malformed_value_tokens, find_non_param_tokens

#: `meta.params`: role -> one parameter set, or a list of them merged in order.
ParamSetNames = dict[
    PlatformName,
    Annotated[PlatformName, References(PlatformKind.PARAMETERSET)]
    | list[Annotated[PlatformName, References(PlatformKind.PARAMETERSET)]],
]


def check_name_rule(value: str | None) -> str | None:
    """`meta.name_rule` holds only well-formed `${param:}` tokens: a run-time token has no value when names are checked.

    Raises:
        ValueError: Naming every other `${...}` in `value`.
    """
    if value is None:
        return value
    bad = find_non_param_tokens(value)
    if bad:
        raise ValueError(
            f"name_rule may only hold ${{param:ROLE.KEY}} tokens, but has {', '.join(repr(b) for b in bad)}"
        )
    return value


def spec_strings(node: Any, path: str) -> list[tuple[str, str]]:
    """Every `(path, string)` under `node`, values only, paths as `spec.layers.segments.hub`."""
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        return [found for key, child in node.items() for found in spec_strings(child, f"{path}.{key}")]
    if isinstance(node, list):
        return [found for position, child in enumerate(node) for found in spec_strings(child, f"{path}[{position}]")]
    return []


class DocumentTemplateModel(PlatformBaseModel):
    """The base of a template model (`DeploymentTemplateModel`, `EnvironmentTemplateModel`, `TenantTemplateModel`).

    Design notes:
    A subclass declares `meta` (the kind's own meta model) and `spec` (`extends` and `partial` typed, every other
    field kept as written). `isinstance(model, DocumentTemplateModel)` tells a template apart from a validated
    document, for the checks that skip one.
    """

    @model_validator(mode="after")
    def validate_template(self) -> "DocumentTemplateModel":
        """No self-`extends`, and every `${...}` token in the spec well formed, `${param:}` included."""
        document: Any = self  # a subclass declares `meta` and `spec`
        meta, spec = document.meta, document.spec
        if spec.extends and spec.extends == meta.name:
            raise ValueError(f"{document.kind.value.capitalize()} '{meta.name}' cannot extend itself.")
        malformed = [
            f"{path}: {candidate!r}"
            for path, text in spec_strings(spec.model_dump(exclude_none=True), "spec")
            for candidate in find_malformed_value_tokens(text, param_tokens=True)
        ]
        if malformed:
            raise ValueError(
                f"Malformed token(s) {'; '.join(malformed)}. Expected '${{param:ROLE.KEY}}' or a run-time token "
                "such as '${var:KEY}'; escape a literal as '$${...}'."
            )
        return self
