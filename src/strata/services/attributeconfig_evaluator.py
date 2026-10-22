#!/usr/bin/env python3
"""Shared JSON Schema (2020-12) evaluator for `attributeconfig` documents.

Three narrow, stateless building blocks, deliberately independent of
`Diagnostics` — the caller (Phase 3's `semantic_checks.py` pass, or a test)
decides severity/wording and assembles the final diagnostic:

- `build_validator()`: compile a schema fragment, hard-failing closed against
  server-side request forgery (SSRF).
- `translate_validation_error()`: map one `jsonschema.ValidationError` to a
  stable `(code, message, location)`.
- `evaluate_selector()`: does a document satisfy a `target.selector`?
- `selectors_compatible()`: can two `target.selector`s ever match the same
  document (Q16) — the predicate Phase 4's duplicate-attribute check needs.

**Why a non-retrieving registry (the hard SSRF requirement).** `spec.schema`
is untrusted, author-supplied content — any solution contributor can write
one. JSON Schema's `$ref` can point at an arbitrary URL; a validator that
resolves refs by fetching them would let that content make the validating
process issue network requests to attacker-chosen destinations the moment
someone else's document is validated against it. `referencing.Registry()`
constructed with no retriever and no pre-registered resources cannot resolve
anything — confirmed directly (2026-10-15): a schema with an external
`$ref` passes `check_schema()` uninspected (ref resolution is lazy, not
performed at compile time) but raises `referencing.exceptions.Unresolvable`
the moment a document is actually evaluated against it
(`Draft202012Validator.iter_errors()`). Both failure points are handled by
this module's callers, never silently swallowed.
"""

from collections.abc import Iterator
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry

from strata.models.common_models import PlatformBaseModel

#: Diagnostic code for a schema that fails to compile, or that (at
#: evaluation time) turns out to reference an external resource. Exported so
#: callers translating `SchemaError`/`Unresolvable` use the same string
#: `translate_validation_error()` uses for its own fallback-free codes.
ATTRIBUTECONFIG_INVALID_CODE = "attributeconfig_invalid"

#: Validator keyword -> stable diagnostic code, verified directly against
#: real jsonschema 4.26.0 output (2026-10-15): `required` and
#: `additionalProperties` failures report an empty `absolute_path` (the
#: object itself, not one field, violated them), `pattern`/`type` report the
#: offending field's path. Anything else falls back to
#: `attributeconfig_validation_error` (e.g. `enum`, `format`, `minLength`,
#: `if`/`then`).
_CODE_BY_VALIDATOR: dict[str, str] = {
    "pattern": "attributeconfig_pattern_mismatch",
    "required": "attributeconfig_field_missing",
    "additionalProperties": "attributeconfig_field_not_allowed",
    "type": "attributeconfig_type_mismatch",
}
_FALLBACK_CODE = "attributeconfig_validation_error"


def build_validator(schema: dict[str, Any]) -> Draft202012Validator:
    """Compile `schema` into a validator that cannot fetch remote `$ref`s.

    Raises:
        jsonschema.exceptions.SchemaError: `schema` is not a valid JSON
            Schema document. Callers report this as `attributeconfig_invalid`.
    """
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=Registry())


def translate_validation_error(error: ValidationError) -> tuple[str, str, str]:
    """Map one validation failure to `(code, message, location)`.

    `location` is dot-joined from `error.absolute_path` (empty for an
    object-level failure like `required`/`additionalProperties` — the
    caller is expected to fall back to the schema's own base location).
    """
    code = _CODE_BY_VALIDATOR.get(str(error.validator), _FALLBACK_CODE)
    location = ".".join(str(segment) for segment in error.absolute_path)
    return code, error.message, location


def evaluate_selector(document: PlatformBaseModel, selector: dict[str, str]) -> bool:
    """Return whether `document` satisfies every `path: value` equality
    constraint in `selector` (AND across constraints; equality only, no
    wildcards or regex — see D3/Q11/Q16). A path through a list (`foo[].bar`)
    matches if *any* element satisfies it. A path that doesn't resolve on
    this document (missing/`None` attribute) never satisfies its constraint.
    """
    for path, expected in selector.items():
        if not any(leaf == expected for leaf in _leaves(document, path.split("."))):
            return False
    return True


def _leaves(value: Any, segments: list[str]) -> Iterator[Any]:
    """Yield every leaf reachable by walking `segments`, mirroring
    `controllers.references._walk()`'s traversal convention: segment-split +
    `getattr`, with a `[]` suffix iterating a list instead of indexing it.
    """
    if not segments:
        yield value
        return

    head, *rest = segments
    iterate = head.endswith("[]")
    attribute = head[:-2] if iterate else head
    child = getattr(value, attribute, None)
    if child is None:
        return

    if iterate:
        for item in child:
            yield from _leaves(item, rest)
    else:
        yield from _leaves(child, rest)


def selectors_compatible(first: dict[str, str], second: dict[str, str]) -> bool:
    """Q16: can two selectors ever match the same document?

    Compatible (match-sets can overlap) iff every field path present in
    *both* selectors requires the same value — a path present in only one
    is unconstrained in the other and never rules out overlap, and a
    selector-less dict (`{}`) is vacantly compatible with anything (no
    shared keys to disagree on). A shared path with differing required
    values makes the two mutually exclusive: no document could ever satisfy
    both, so declaring the same attribute under both is safe, not a conflict.
    """
    return all(first[key] == second[key] for key in first.keys() & second.keys())


def reshape_block_value(raw: Any, block: str) -> dict[str, Any]:
    """The JSON-shaped object a schema actually evaluates against for `target.block`.

    `properties`/`custom`/`configuration` are already `dict[str, Any] | None` — pass through unchanged
    (`{}` for unset, matching D7's "an unset block is checked as `{}`, not skipped"). `variables`/
    `features` are `list[VariableStoreModel | FeatureStoreModel] | None` instead — not JSON-shaped at all,
    so `jsonschema` would reject the list itself (`type: object` expected) rather than checking per-key
    rules. Reshaped into the same `{key: value}` shape every other block already has, keyed by each
    entry's own `key` (uniqueness within one list is enforced by `EnvironmentSpecModel.
    validate_unique_keys()`), with the *whole* store entry dumped — `value`/`type` are both typed `Any`
    on the real models, so a rule may need `store`/`type` alongside `value` (docs/work/
    environment-value-metadata.md's `variables`/`features` design).
    """
    if block in ("variables", "features"):
        return {item.key: item.model_dump(mode="json", by_alias=True) for item in (raw or [])}
    return raw or {}
