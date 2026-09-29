#!/usr/bin/env python3
"""Resolves `${value:kind.name.path.to.field}` cross-document references
(docs/design/cross-document-value-references.md) — the 5th Value-token
kind, addressed by `(kind, name)` document identity (ADR-0015) plus a
dotted path from that document's own root, rather than an
Environment-declared key the way `var`/`secret`/`feature` are.

A sibling module to `value_controller.py`, not a function inside it —
Open Question 1 in the design doc originally leaned the other way ("small
enough... a natural sibling" inside that file), but implementation
surfaced a real, concrete reason to split it out after all: both
`semantic_checks.py` (via `solution_context.py`) and `value_controller.py`
need this function, and `value_controller.py` itself imports
`solution_context.SolutionContext` for its other functions
(`resolve_values()`, `resolve_deployment()`, etc.) — so
`semantic_checks.py` importing `resolve_document_value_references()`
straight from `value_controller.py` created a real import cycle
(`solution_context` -> `semantic_checks` -> `value_controller` ->
`solution_context`). This module only ever needs a `DocumentIndex`, never
a `SolutionContext`, so it has no such dependency and both callers can
import it directly with no cycle.
"""

from typing import Any

from strata.controllers.solution_controller import DocumentIndex
from strata.models.common_models import PlatformKind
from strata.utils.diagnostics import Diagnostics
from strata.utils.value_tokens import extract_value_tokens, has_value_tokens

#: Sentinel distinguishing "path resolved to a real `None`/falsy leaf" from
#: "path could not be walked at all" — `_walk_value_reference_path()`'s
#: return value is checked with `is _VALUE_PATH_MISSING`, never truthiness.
_VALUE_PATH_MISSING = object()


def _iter_document_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Yield every `(dotted_path, string)` pair inside a document's dumped JSON.

    A small, local duplicate of `environment_service.py`'s own private
    `_iter_strings()` (identical shape — recurse into `dict`/`list`, yield
    every `str` leaf with its dotted path) — deliberately not imported from
    there: that function is a leading-underscore private implementation
    detail of a different module, and this walk is genuinely tiny (7
    lines), not worth coupling two modules over. Paths are built from the
    document's own keys, matching that function's exact convention
    (`spec.zones[0].records[1].value`), so a `${value:...}` diagnostic's
    `location` reads identically to every other Value-token diagnostic.
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path or "<root>", value))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_iter_document_strings(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for position, child in enumerate(value):
            found.extend(_iter_document_strings(child, f"{path}[{position}]"))
    return found


def _walk_value_reference_path(node: Any, segments: list[str]) -> Any:
    """Walk `segments` through `node` (a target document's own
    `model_dump(by_alias=True, mode="json")`), one segment per step.

    Returns `_VALUE_PATH_MISSING` the instant a segment cannot be resolved
    — a missing dict key, a non-integer or out-of-range list index, or an
    attempt to descend into an already-scalar value. An empty `segments`
    list (a bare `${value:kind.name}` with no path at all) returns `node`
    itself unchanged — the whole document's dumped dict — which
    deliberately is not special-cased here: `resolve_document_value_references()`'s
    own scalar check below rejects it as `value_reference_not_scalar`,
    naturally covering "referencing a whole sub-object is out of scope"
    (this design's own "Deliberately out of scope" section) with no extra
    code.
    """
    for segment in segments:
        if isinstance(node, dict):
            if segment not in node:
                return _VALUE_PATH_MISSING
            node = node[segment]
        elif isinstance(node, list):
            if not segment.isdigit() or int(segment) >= len(node):
                return _VALUE_PATH_MISSING
            node = node[int(segment)]
        else:
            return _VALUE_PATH_MISSING
    return node


def resolve_document_value_references(index: DocumentIndex) -> tuple[dict[str, str], Diagnostics]:
    """Resolve every `${value:kind.name.path.to.field}` token anywhere in
    the loaded solution (docs/design/cross-document-value-references.md)
    into one flat `key -> value` map, `key` being the exact
    `"kind.name.path"` string that already appears after `value:` in the
    token — no re-encoding, so it merges directly into `ValueResolution.
    values` alongside every `var`/`secret`/`feature`/`output` entry
    (`resolve_value_tokens()` resolves by flat `key` lookup only, blind to
    which kind produced an entry, confirmed in that function's own
    docstring).

    Solution-wide, not deployment-scoped — a real, deliberate difference
    from `var`/`secret`/`feature` (only ever declared inside a reachable
    Environment): a `${value:...}` reference names its own target
    directly via `(kind, name)` document identity (ADR-0015), so every
    document in `index`, not just ones reachable from one deployment, is
    walked for candidates.

    Checks, in order (cheapest and most-common-failure-first, matching
    this design's own "Resolution model" section):

    1. `kind` names a real `PlatformKind` value
       (`value_reference_unknown_kind`). Checked before segment count, so
       `${value:bogus.x.y}` is reported as an unknown kind even though it
       also happens to have enough segments — the kind itself is always
       the more fundamental problem.
    2. At least a `name` segment is present — `kind.name.path` needs 2+
       dot-segments; `kind` alone (e.g. a bare `${value:tenant}`) has
       nothing to look up (`value_reference_invalid_path`). A bare
       single-segment token whose one segment isn't a real kind (e.g.
       `${value:onlyonesegment}`) is instead reported as
       `value_reference_unknown_kind` at step 1 — the more useful,
       honest answer, since the segment genuinely isn't a valid kind
       either way. (docs/design/cross-document-value-references.md's own
       Phase 2 note framed this case loosely, before this exact algorithm
       was designed — corrected here against the real, implemented
       behaviour rather than left to silently disagree with the doc.)
    3. `(kind, name)` exists in `index` (`value_reference_unknown_document`).
       `kind: solution` always fails here, by construction, never a
       crash — the solution manifest's own kind is deliberately never
       indexed (`SolutionController._load_manifest()`'s own docstring),
       exactly this design's documented edge case.
    4. The remaining segments walk to a real leaf on the target document
       (`value_reference_invalid_path`) — a missing key, a bad list
       index, or indexing into an already-scalar value. An *empty* path
       (`kind.name` with nothing after) is not a path failure — it
       resolves to the whole document dict, which then fails check 5
       instead, exactly matching "referencing a whole sub-object is out
       of scope."
    5. That leaf is a scalar — `str`/`int`/`float`/`bool`, never a
       `dict`/`list`/`None` (`value_reference_not_scalar`).
    6. If the leaf is a string, it contains no `${...}` token of any kind
       — var/secret/feature/output/value, escaped `$${...}` spans don't
       count since they resolve to a fixed literal
       (`value_reference_target_not_literal`). This is also what makes a
       transitive/chained reference (A -> B -> C) and a cycle (A -> B ->
       A) both impossible by construction: B's own targeted field would
       already have to be a pure literal to be a valid target for A, so
       it can never itself contain a `${value:...}` (or any other kind's)
       token pointing further on — no cycle-detection code is needed.

    Args:
        index: The whole loaded `DocumentIndex` (every kind, not scoped to
            one deployment's reachable documents).

    Returns:
        `(values, diagnostics)` — `values` has one entry per successfully
        resolved token (already stringified, matching every other
        resolver's `dict[str, str]` convention); `diagnostics` has one
        error per violation above, `source` the document the token was
        *found in* (not the target), `location` the dotted field path
        within that document.
    """
    values: dict[str, str] = {}
    diagnostics = Diagnostics()

    for entry in index.all():
        dumped = entry.model.model_dump(by_alias=True, mode="json")
        for location, text in _iter_document_strings(dumped):
            for token_kind, full_key in extract_value_tokens(text):
                if token_kind != "value":
                    continue
                segments = full_key.split(".")
                kind_str = segments[0]
                try:
                    target_kind = PlatformKind(kind_str)
                except ValueError:
                    diagnostics.error(
                        f"'${{value:{full_key}}}' uses unknown kind '{kind_str}' — expected one of "
                        f"{sorted(k.value for k in PlatformKind)}.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_unknown_kind",
                    )
                    continue
                if len(segments) < 2:
                    diagnostics.error(
                        f"'${{value:{full_key}}}' is missing a document name and field path — expected "
                        "'${value:kind.name.path.to.field}'.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_invalid_path",
                    )
                    continue
                name = segments[1]
                target = index.get(target_kind, name)
                if target is None:
                    diagnostics.error(
                        f"'${{value:{full_key}}}' references '{target_kind.value}/{name}', which does not exist.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_unknown_document",
                    )
                    continue
                path_segments = segments[2:]
                resolved = _walk_value_reference_path(
                    target.model.model_dump(by_alias=True, mode="json"), path_segments
                )
                if resolved is _VALUE_PATH_MISSING:
                    diagnostics.error(
                        f"'${{value:{full_key}}}' has no field at path '{'.'.join(path_segments)}' on "
                        f"'{target_kind.value}/{name}'.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_invalid_path",
                    )
                    continue
                if not isinstance(resolved, (str, int, float, bool)):
                    diagnostics.error(
                        f"'${{value:{full_key}}}' resolves to a {type(resolved).__name__}, not a single "
                        "value — referencing a whole sub-object is not supported.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_not_scalar",
                    )
                    continue
                if isinstance(resolved, str) and has_value_tokens(resolved):
                    diagnostics.error(
                        f"'${{value:{full_key}}}' targets '{target_kind.value}/{name}'.{'.'.join(path_segments)}', "
                        "which itself contains an unresolved Value token — referencing another token's own "
                        "target (chained/transitive resolution) is not supported.",
                        source=str(entry.source),
                        location=location,
                        code="value_reference_target_not_literal",
                    )
                    continue
                values[full_key] = str(resolved)

    return values, diagnostics
