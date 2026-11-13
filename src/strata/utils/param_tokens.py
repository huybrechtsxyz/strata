#!/usr/bin/env python3
"""Load-time `${param:ROLE.PATH}` substitution (docs/design/parameter-sets.md, "Rules" 3, 4, 6, 7, 8).

A deployment names parameter sets per role in `meta.params`; every document of its `extends` chain may. The
resolver (`controllers/deployment_resolution.py`) collects them root first, merges each role's sets with
`merge_param_values()`, and substitutes each document's `spec` with `substitute_params()` before it merges the
specs. Pure data logic, no index: it lives in `strata.utils` (ADR-0003 layering).

- **Whole value or embedded (rule 6).** A string that is exactly one token becomes the value with its own
  type, so a list or a map can be inserted. A token inside a longer string needs a scalar.
- **Missing is an error (rule 7)**, with no default syntax: defaults belong in a base's parameter set.
- **One pass (rule 8).** Substituted text is never scanned again, run-time tokens (`${var:x}`) are left alone,
  and an escaped `$${param:x}` stays as written (run time unescapes it to the literal `${param:x}`).
"""

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from strata.utils.dict_merge import deep_merge
from strata.utils.value_paths import VALUE_PATH_MISSING, walk_value_path
from strata.utils.value_tokens import PARAM_TOKEN_PATTERN, strip_escaped_value_tokens

#: An escaped `$${...}` (kept as written) or a `${param:...}` token, in one alternation so a single left-to-right
#: pass never reads a token out of an escape.
_SUBSTITUTE_PATTERN = re.compile(r"\$\$\{[^}]*\}|" + PARAM_TOKEN_PATTERN.pattern)


def holds_param_tokens(node: Any) -> bool:
    """Whether any string value under `node` holds a well-formed `${param:}` token (an escaped one does not count).

    What makes a document a template (docs/design/parameter-sets.md, "Templates are validated per instance").
    Map keys are never substituted, so they do not count either.
    """
    if isinstance(node, dict):
        return any(holds_param_tokens(child) for child in node.values())
    if isinstance(node, list):
        return any(holds_param_tokens(child) for child in node)
    return isinstance(node, str) and PARAM_TOKEN_PATTERN.search(strip_escaped_value_tokens(node)) is not None


@dataclass(frozen=True)
class ParamProblem:
    """A problem with a document's `${param:}` substitution, at `location` (`spec.properties.name`).

    `per_leaf` is False for a finding the values played no part in (a name a template writes literally): it is the
    same for every deployment that uses the template, so it is reported once.
    """

    location: str
    message: str
    code: str
    per_leaf: bool = True


@dataclass(frozen=True)
class ParamRead:
    """One `${param:role.key}` token that was filled in, and the value it got."""

    role: str
    key: str
    value: Any


@dataclass(frozen=True)
class ParamOrigin:
    """Where a substituted string came from: the text as written and the tokens filled into it."""

    text: str
    reads: tuple[ParamRead, ...]


def param_reads_in(node: Any) -> set[tuple[str, tuple[str, ...]]]:
    """`(role, path)` for every well-formed `${param:}` token in the string values under `node` (escapes ignored)."""
    found: set[tuple[str, tuple[str, ...]]] = set()
    if isinstance(node, dict):
        for child in node.values():
            found |= param_reads_in(child)
    elif isinstance(node, list):
        for child in node:
            found |= param_reads_in(child)
    elif isinstance(node, str):
        for match in PARAM_TOKEN_PATTERN.finditer(strip_escaped_value_tokens(node)):
            role, *path = match.group("key").split(".")
            found.add((role, tuple(path)))
    return found


@dataclass(frozen=True)
class _Missing:
    """Why a token's value was not found (a found value may itself be `None`, so it cannot signal this)."""

    message: str


def merge_param_values(
    sets: Mapping[str, Sequence[str]], values_of: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Each role's values: its parameter sets merged in order, later winning per leaf key (rule 3).

    Args:
        sets: Role -> the names of its parameter sets, in merge order (root of the chain first, leaf last;
            within one `params` list, in list order).
        values_of: Parameter set name -> its `spec.values`. Every name in `sets` must be present.
    """
    merged: dict[str, dict[str, Any]] = {}
    for role, names in sets.items():
        values: dict[str, Any] = {}
        for name in names:
            values = deep_merge(values, dict(values_of[name]))
        merged[role] = values
    return merged


def _render_scalar(value: Any) -> str | None:
    """The text an embedded token becomes, or `None` when `value` is not a scalar.

    Booleans render as YAML writes them (`true`), dates as ISO text (`2026-12-31`).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return None


def _lookup(key: str, values: Mapping[str, Mapping[str, Any]], sets: Mapping[str, Sequence[str]]) -> Any:
    """The value `key` (`spoke.network.cidr`) names, or `_Missing` with the reason."""
    role, *path = key.split(".")
    if role not in values:
        return _Missing(
            f"${{param:{key}}}: no document in the chain names a parameter set for role '{role}' in meta.params"
        )
    found = walk_value_path(values[role], path)
    if found is VALUE_PATH_MISSING:
        return _Missing(f"${{param:{key}}} not found in {role}: [{', '.join(sets.get(role, ()))}]")
    return found


def substitute_params(
    node: Any,
    values: Mapping[str, Mapping[str, Any]],
    sets: Mapping[str, Sequence[str]],
    path: str,
    origins: dict[str, ParamOrigin] | None = None,
) -> tuple[Any, list[ParamProblem]]:
    """`node` with every `${param:}` in its strings substituted, and the tokens that could not be.

    Only values are substituted, never map keys. `node` is not mutated; an inserted list or map is a copy, so
    later merging cannot change a parameter set.

    Args:
        node: Dumped document data (a `spec` dict, or any part of one).
        values: Role -> merged values (`merge_param_values()`).
        sets: Role -> the parameter sets consulted, named in a missing-key message.
        path: Where `node` sits (`spec`), the prefix of each problem's location.
        origins: When given, filled with the origin of every string a token was filled into, by its location
            (`spec.environments[1]`), so a finding on the result can say where its value came from.
    """
    problems: list[ParamProblem] = []

    def record(text: str, where: str, read: ParamRead) -> None:
        if origins is not None:
            known = origins.get(where)
            origins[where] = ParamOrigin(text, (*known.reads, read) if known else (read,))

    def walk(item: Any, where: str) -> Any:
        if isinstance(item, dict):
            return {key: walk(child, f"{where}.{key}") for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child, f"{where}[{position}]") for position, child in enumerate(item)]
        if isinstance(item, str):
            return substitute_string(item, where)
        return item

    def substitute_string(text: str, where: str) -> Any:
        whole = PARAM_TOKEN_PATTERN.fullmatch(text)
        if whole is not None:
            found = _lookup(whole.group("key"), values, sets)
            if isinstance(found, _Missing):
                problems.append(ParamProblem(where, found.message, "param_missing"))
                return text
            role, _, key = whole.group("key").partition(".")
            record(text, where, ParamRead(role, key, found))
            return copy.deepcopy(found)

        def replace(match: re.Match[str]) -> str:
            key = match.group("key")
            if key is None:
                return match.group(0)  # an escaped `$${...}`: left for run time to unescape
            found = _lookup(key, values, sets)
            if isinstance(found, _Missing):
                problems.append(ParamProblem(where, found.message, "param_missing"))
                return match.group(0)
            rendered = _render_scalar(found)
            if rendered is None:
                problems.append(
                    ParamProblem(
                        where,
                        f"${{param:{key}}} is a {type(found).__name__ if found is not None else 'null'}, and a "
                        "token inside a longer string needs a single value. Make the token the whole value to "
                        "insert a list or map.",
                        "param_not_scalar",
                    )
                )
                return match.group(0)
            role, _, path_key = key.partition(".")
            record(text, where, ParamRead(role, path_key, found))
            return rendered

        return _SUBSTITUTE_PATTERN.sub(replace, text)

    return walk(node, path), problems
