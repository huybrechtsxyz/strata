#!/usr/bin/env python3
"""The dotted walk `${value:}` and `${param:}` share: one segment per step through dumped YAML/JSON data.

Moved out of `controllers/value_references.py` when `${param:}` (`base-parameters-v2.md` (work doc)) needed
the same walk: pure data logic, so it lives in `strata.utils` (ADR-0003 layering) where both can reach it.
"""

from typing import Any

#: Sentinel distinguishing "path resolved to a real `None`/falsy leaf" from "path could not be walked at
#: all": `walk_value_path()`'s return value is checked with `is VALUE_PATH_MISSING`, never truthiness.
VALUE_PATH_MISSING = object()


def walk_value_path(node: Any, segments: list[str]) -> Any:
    """Walk `segments` through `node`, one segment per step.

    A list segment is resolved by integer index (`networks.0.cidr`) or, if it isn't a digit, by matching a
    `"name"` key on one of the list's own dict items (`networks.main-network.cidr`): the same `(kind, name)`
    identity convention ADR-0015 established for document addressing, applied one level deeper to named items
    *within* a document (docs/design/firewall-network-cidr-references.md). The two can never be ambiguous:
    `PlatformName`'s own pattern requires a leading lowercase letter, so a real name is never also
    `.isdigit()`. A list of plain scalars never matches a non-digit segment. Duplicate names within one list
    are not re-validated here (the first match wins); uniqueness is each model's own `check_unique_names()`
    validator's job.

    Returns `VALUE_PATH_MISSING` the instant a segment cannot be resolved: a missing dict key, a list
    index/name that doesn't exist, or an attempt to descend into an already-scalar value. An empty `segments`
    list returns `node` itself unchanged; each caller decides whether a whole sub-object is acceptable
    (`${value:}` rejects it as `value_reference_not_scalar`, `${param:}` inserts it as a whole value).
    """
    for segment in segments:
        if isinstance(node, dict):
            if segment not in node:
                return VALUE_PATH_MISSING
            node = node[segment]
        elif isinstance(node, list):
            if segment.isdigit():
                if int(segment) >= len(node):
                    return VALUE_PATH_MISSING
                node = node[int(segment)]
            else:
                match = next((item for item in node if isinstance(item, dict) and item.get("name") == segment), None)
                if match is None:
                    return VALUE_PATH_MISSING
                node = match
        else:
            return VALUE_PATH_MISSING
    return node
