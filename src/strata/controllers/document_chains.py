#!/usr/bin/env python3
"""`extends` chains for deployments, environments and tenants (`base-parameters-v2.md` (work doc), phase 2).

The chain walk (cycles, missing ancestors, poisoning), the parameter sets a chain names, and the `meta.name_rule`
check are the same for the three kinds; only the merge of two specs differs. This module holds the shared part.
`deployment_resolution.py` resolves deployments with it; `resolve_environments_and_tenants()` here resolves the
other two kinds.

**Environments and tenants are resolved when the solution loads, and the result replaces the document in the
index.** They are read in many places (value assembly, token checks, geography checks, `${value:}`,
attributeconfig), so each of those reads the merged document with no change of its own. Deployments keep their
separate resolved map: they are read through `deployment_of()`, and resolve against the resolved tenants.

- A `partial: true` environment or tenant stays in the index as written, so `extends` can find it; the checks that
  list, select or read a document reject it (`semantic_checks._check_base_usage()`, `value_references.py`).
- A document with nothing to resolve (no `extends`, no `meta.params`, no `meta.name_rule`, no `${param:}`) stays
  as it is.
- Every chain is read as written (`IndexEntry.as_written`), never as resolved, so one environment extending
  another non-partial one gets the values of its own chain, not the ones its base was resolved with.
- A resolution problem (a cycle, a value that is missing, a merged spec the model rejects) is reported with the
  load findings, like a document that fails its schema, so later checks do not report what follows from it. A
  missing ancestor is not: the reference walk reports it on `spec.extends`.
- Findings on a merged document name the leaf's file. Origins on findings (the base and field a value came from)
  are deployment-only for now; the full origin map is phase 4.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.environment_model import EnvironmentModel
from strata.models.parameterset_model import ParameterSetModel
from strata.models.template_model import DocumentTemplateModel
from strata.models.tenant_model import TenantModel
from strata.services.base_service import diagnostics_from_validation_error
from strata.services.environment_service import merge_environment_specs
from strata.services.tenant_service import merge_tenant_specs
from strata.utils.diagnostics import Diagnostics
from strata.utils.param_tokens import merge_param_values, substitute_params
from strata.utils.value_paths import VALUE_PATH_MISSING, walk_value_path

#: Role -> the parameter sets a chain names for it, root first: the merge order, and the merged-spec cache key.
ParamSets = dict[str, tuple[str, ...]]

#: The kinds resolved when the solution loads: the model of a resolved document, and the merge of a base into a child.
_LOAD_TIME_KINDS: dict[
    PlatformKind, tuple[type[PlatformBaseModel], Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]
] = {
    PlatformKind.ENVIRONMENT: (EnvironmentModel, merge_environment_specs),
    PlatformKind.TENANT: (TenantModel, merge_tenant_specs),
}


class CircularExtendsError(Exception):
    """Internal signal only — carries the cycle so the top-level caller can
    report it against the document whose `extends` closes the loop.
    """

    def __init__(self, cycle: list[str]) -> None:
        super().__init__(f"circular extends chain: {' -> '.join(cycle)}")
        self.cycle = cycle


def require(index: DocumentIndex, kind: PlatformKind, name: str) -> IndexEntry:
    """The entry for a document already known to exist (a resolved chain link or a checked parameter set)."""
    entry = index.get(kind, name)
    assert entry is not None, f"{kind.value}/{name} vanished from the index"
    return entry


def resolve_chain(
    kind: PlatformKind,
    name: str,
    index: DocumentIndex,
    cache: dict[str, tuple[str, ...] | None],
    poisoned: set[str],
    visiting: list[str],
) -> tuple[str, ...] | None:
    """Return document `name` of `kind` and its bases, leaf first, as written.

    Memoised per name: a base shared by several leaves (a diamond) is walked once, not once per descendant.

    Returns:
        `None` for two distinct reasons, distinguished via `poisoned`: the chain is missing an ancestor entirely
        (not poisoned — reported by the reference walk), or an ancestor is part of a cycle (poisoned — the caller
        reports its own pointer).

    Raises:
        CircularExtendsError: Only from the exact call that closes the loop, so its message names the real cycle.
    """
    if name in cache:
        return cache[name]

    if name in visiting:
        poisoned.add(name)
        raise CircularExtendsError([*visiting, name])

    entry = index.get(kind, name)
    if entry is None:
        return None  # missing ancestor — not a cycle, not cached: nothing to memoise

    extends = getattr(getattr(entry.as_written, "spec", None), "extends", None)
    if not extends:
        cache[name] = (name,)
        return cache[name]

    try:
        base_chain = resolve_chain(kind, extends, index, cache, poisoned, [*visiting, name])
    except CircularExtendsError:
        poisoned.add(name)
        cache[name] = None
        raise

    if base_chain is None:
        if extends in poisoned:
            poisoned.add(name)
        cache[name] = None
        return None

    cache[name] = (name, *base_chain)
    return cache[name]


def chain_of(
    entry: IndexEntry,
    index: DocumentIndex,
    cache: dict[str, tuple[str, ...] | None],
    poisoned: set[str],
    diagnostics: Diagnostics,
) -> tuple[str, ...] | None:
    """`entry`'s chain, leaf first; a cycle through it is reported in `diagnostics` and gives `None`.

    A missing ancestor also gives `None`, unreported: the reference walk reports it against `spec.extends`.
    """
    kind, name = entry.ref.kind, entry.ref.name
    try:
        chain = resolve_chain(kind, name, index, cache, poisoned, visiting=[])
    except CircularExtendsError as exc:
        diagnostics.error(str(exc), source=str(entry.source), location="spec.extends", code="circular_extends")
        return None
    if chain is None and name in poisoned:
        # Part of a cycle first detected while resolving a *different* document's chain; that occurrence already
        # reported the cycle itself, with the full path. This one just also fails.
        diagnostics.error(
            f"{kind.value.capitalize()} '{name}': extends chain could not be resolved — "
            "part of a circular extends chain reported elsewhere",
            source=str(entry.source),
            location="spec.extends",
            code="circular_extends",
        )
    return chain


def chain_param_sets(kind: PlatformKind, chain: tuple[str, ...], index: DocumentIndex) -> ParamSets | None:
    """Role -> the parameter sets `chain` (leaf first) names, root first; `None` if one does not exist."""
    sets: dict[str, list[str]] = {}
    for name in reversed(chain):
        meta = getattr(require(index, kind, name).as_written, "meta", None)
        for role, named in (getattr(meta, "params", None) or {}).items():
            names = [named] if isinstance(named, str) else list(named)
            if any(index.get(PlatformKind.PARAMETERSET, set_name) is None for set_name in names):
                return None
            sets.setdefault(role, []).extend(names)
    return {role: tuple(names) for role, names in sets.items()}


def set_values(param_sets: ParamSets, index: DocumentIndex) -> dict[str, dict[str, Any]]:
    """Parameter set name -> its `spec.values`, for every set `param_sets` names."""
    values: dict[str, dict[str, Any]] = {}
    for names in param_sets.values():
        for name in names:
            model = require(index, PlatformKind.PARAMETERSET, name).model
            assert isinstance(model, ParameterSetModel)
            values[name] = model.spec.values
    return values


def value_supplier(param_sets: ParamSets, values: dict[str, dict[str, Any]], role: str, key: str) -> str:
    """The parameter set the merged value of `role.key` comes from: the last set of the role that holds it.

    For `${param:role}` alone (no key), or a key no set holds, every set of the role, comma-separated.
    """
    names = param_sets.get(role, ())
    if not key:
        return ", ".join(names)
    path = key.split(".")
    found = [name for name in names if walk_value_path(values[name], path) is not VALUE_PATH_MISSING]
    return found[-1] if found else ", ".join(names)


def chain_params(entry: IndexEntry, index: DocumentIndex) -> dict[str, dict[str, Any]] | None:
    """The merged parameter-set values `entry` receives, per role: what its `${param:}` tokens read.

    `{}` for a document whose chain names no set; `None` when its chain or a set it names does not resolve (both
    are reported elsewhere). For attributeconfig's `view: effective, block: params`.
    """
    kind = entry.ref.kind
    try:
        chain = resolve_chain(kind, entry.ref.name, index, {}, set(), visiting=[])
    except CircularExtendsError:
        return None
    if chain is None:
        return None
    param_sets = chain_param_sets(kind, chain, index)
    if param_sets is None:
        return None
    return merge_param_values(param_sets, set_values(param_sets, index))


def check_name_rules(
    index: DocumentIndex,
    entry: IndexEntry,
    chain: tuple[str, ...],
    params: dict[str, dict[str, Any]],
    param_sets: ParamSets,
) -> Diagnostics:
    """Rule 10: `entry` has exactly the name each `meta.name_rule` of its chain gives once filled in.

    The document's own rule counts too. A mismatch is reported on `entry`, which is what gets renamed; a rule whose
    `${param:}` cannot be filled is `param_missing` on the document that holds the rule.
    """
    diagnostics = Diagnostics()
    kind, name = entry.ref.kind, entry.ref.name
    for holder in reversed(chain):
        holder_entry = require(index, kind, holder)
        rule = getattr(getattr(holder_entry.as_written, "meta", None), "name_rule", None)
        if rule is None:
            continue
        expected, problems = substitute_params(rule, params, param_sets, "meta.name_rule")
        for problem in problems:
            diagnostics.error(
                f"{holder} {problem.location}: {problem.message} (for {name})",
                source=str(holder_entry.source),
                location=problem.location,
                code=problem.code,
            )
        if problems or expected == name:
            continue
        rule_of = "its own name_rule" if holder == name else f"the name_rule of '{holder}'"
        diagnostics.error(
            f"{kind.value.capitalize()} '{name}' must be named '{expected}' by {rule_of} ('{rule}'). Rename the "
            f"{kind.value}, or fix the values in its parameter sets.",
            source=str(entry.source),
            location="meta.name",
            code="name_rule_mismatch",
        )
    return diagnostics


def resolve_environments_and_tenants(index: DocumentIndex) -> Diagnostics:
    """Resolve every environment and tenant through `extends` and `${param:}`, and put the result in the index.

    In the order of "Resolution": environments, then tenants. Every chain is resolved against the documents as
    written before any is replaced, so the order of replacement never changes a result.

    Returns:
        The problems found, reported with the load findings.
    """
    diagnostics = Diagnostics()
    replacements: list[IndexEntry] = []
    for kind in _LOAD_TIME_KINDS:
        cache: dict[str, tuple[str, ...] | None] = {}
        poisoned: set[str] = set()
        for entry in index.all_of(kind):
            chain = chain_of(entry, index, cache, poisoned, diagnostics)
            if chain is None or _is_partial(entry.as_written) or not _needs_resolving(entry.as_written, chain):
                continue
            model = _resolve(entry, chain, index, diagnostics)
            if model is not None:
                replacements.append(IndexEntry(ref=entry.ref, model=model, source=entry.source, written=entry.model))
    for replacement in replacements:
        index.replace(replacement)
    return diagnostics


def _is_partial(document: PlatformBaseModel) -> bool:
    return bool(getattr(getattr(document, "spec", None), "partial", False))


def _needs_resolving(document: PlatformBaseModel, chain: tuple[str, ...]) -> bool:
    """Whether `document` differs from what it resolves to, or has a rule or parameter set to check."""
    meta = getattr(document, "meta", None)
    return (
        len(chain) > 1
        or isinstance(document, DocumentTemplateModel)
        or bool(getattr(meta, "params", None))
        or getattr(meta, "name_rule", None) is not None
    )


def _resolve(
    entry: IndexEntry, chain: tuple[str, ...], index: DocumentIndex, diagnostics: Diagnostics
) -> PlatformBaseModel | None:
    """The document `entry` resolves to through `chain` (leaf first), or `None` when it does not (reported)."""
    kind, name = entry.ref.kind, entry.ref.name
    model_class, merge = _LOAD_TIME_KINDS[kind]
    param_sets = chain_param_sets(kind, chain, index)
    if param_sets is None:
        return None  # names a parameter set that does not exist: the reference walk reports it on meta.params
    params = merge_param_values(param_sets, set_values(param_sets, index))

    merged: dict[str, Any] = {}
    failed = False
    for holder in reversed(chain):
        holder_entry = require(index, kind, holder)
        written: Any = holder_entry.as_written
        spec = written.spec
        own, problems = substitute_params(spec.model_dump(exclude_none=True), params, param_sets, "spec")
        for problem in problems:
            diagnostics.error(
                f"{holder} {problem.location}: {problem.message} (for {name})",
                source=str(holder_entry.source),
                location=problem.location,
                code=problem.code,
            )
        failed = failed or bool(problems)
        merged = merge(merged, own)
    if failed:
        return None

    leaf: Any = entry.as_written
    meta = leaf.meta
    try:
        model = model_class.model_validate({"meta": meta.model_dump(exclude_none=True), "spec": merged})
    except ValidationError as exc:
        through = " -> ".join(chain)
        for finding in diagnostics_from_validation_error(exc).errors:
            diagnostics.error(
                f"{kind.value.capitalize()} '{name}' as resolved ({through}): {finding.message}",
                source=str(entry.source),
                location=finding.location,
                code=finding.code,
            )
        return None

    name_rules = check_name_rules(index, entry, chain, params, param_sets)
    diagnostics.extend(name_rules)
    return model
