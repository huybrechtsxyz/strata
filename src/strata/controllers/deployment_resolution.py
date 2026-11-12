#!/usr/bin/env python3
"""`extends` chain resolution, plus tenant defaults (ADR-0024) — folding a
deployment's ancestry, and its tenant's base layer, into one document.

`merge_deployment_specs()` (`deployment_service.py`) already merges one base
into one child, on raw dicts, and is fully tested. Nothing called it: this
module is that caller, generalised to chains of any length.

**Why no new raw-dict plumbing is needed.** A `partial: true` base already
validates standalone (that is exactly what the flag is for), so it already
sits in the index as an ordinary, already-validated `DeploymentModel`.
`model.spec.model_dump(exclude_none=True)` on that gives back precisely the
dict shape `merge_deployment_specs` expects — the loader does not need to be
touched.

**Why completeness is checked against the document's *own* declared
`partial`, not the merged result's.** `merge_deployment_specs` strips
`partial`/`extends` out of every merged result. That is correct for a leaf,
but wrong applied to an *intermediate* link — a base that is itself
`partial: true` AND is further extended by something else. Once merged with
its own ancestor, an intermediate link can look complete (it inherits
`workspace` from its parent) even though its author never intended it to be
deployable on its own; re-validating that merge would demand fields
(`environments`) only the eventual leaf is meant to supply. So completeness
is only checked for documents whose *own, unmerged* `spec.partial` is falsy —
an intermediate base is still resolved (its descendants need the dict) but is
never itself required to be complete.

**Cycles** (`A extends B extends C extends A`) are the one thing the model
genuinely cannot catch by itself — `DeploymentModel.validate_not_self_extending`
only rejects the trivial `A extends A`. Detection here is unconditional
(runs before the partial/completeness check), because a cycle is an
authoring error regardless of whether anything currently deployable extends
into it — the same reason a circular import is a bug even in dead code.
Detected via a `visiting` list threaded through the recursive resolve, and
reported against the deployment whose `extends` field closes the loop,
naming the full cycle; every other node on the same cycle gets a shorter
"reported elsewhere" pointer rather than a second full trace.

**Tenant defaults (ADR-0024) are a second, deliberately separate merge
axis, applied last.** `extends` folds deployment-to-deployment ancestry;
`_merge_tenant_defaults()` folds tenant-to-deployment defaults
(`properties`/`custom`/`environments`) — conceptually different relations,
reusing the same tested `merge_deployment_specs()` mechanics rather than
inventing a second one. Kept in this module rather than given its own
call site per consumer: every current and planned reader of this
function's output wants the same fully-resolved deployment, and a second
call site is one more place to forget to call it.

**Parameter sets (`base-parameters-v2.md` (work doc), step 1b) make it two
passes.** The values come from the whole chain, leaf included, and apply
to every base, so a base's merged spec is no longer one per name. Pass one
walks up and collects the chain (cycles and missing ancestors, keyed by
name as before). Pass two, for each deployable leaf, merges the chain's
parameter sets per role, substitutes `${param:}` in each document's spec
and merges the specs root first. The merged-spec cache is keyed by (name,
the sets the chain names), so leaves that name the same sets, or none,
still share their bases' work.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from strata.controllers.references import unresolved_references
from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.logging.config import get_logger
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import (
    AnyDeploymentModel,
    DeploymentModel,
    DeploymentSpecModel,
    DeploymentTemplateModel,
)
from strata.models.parameterset_model import ParameterSetModel
from strata.models.tenant_model import TenantModel
from strata.models.version_model import VersionModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import diagnostics_from_validation_error
from strata.services.deployment_service import merge_deployment_specs
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.param_tokens import (
    ParamOrigin,
    ParamProblem,
    merge_param_values,
    param_reads_in,
    substitute_params,
)
from strata.utils.value_paths import VALUE_PATH_MISSING, walk_value_path
from strata.utils.value_tokens import extract_value_tokens

log = get_logger(__name__)


class _CircularExtendsError(Exception):
    """Internal signal only — carries the cycle so the top-level caller can
    report it against the deployment whose `extends` closes the loop.
    """

    def __init__(self, cycle: list[str]) -> None:
        super().__init__(f"circular extends chain: {' -> '.join(cycle)}")
        self.cycle = cycle


#: Role -> the parameter sets a chain names for it, root first: the merge order, and the merged-spec cache key.
ParamSets = dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ResolvedDeployment:
    """One deployable deployment, resolved.

    `model` is the merged document. `params` is the merged values per role its `${param:}` tokens read, from
    `param_sets` (role -> the sets named, root first). `chain` is the deployment and its bases, leaf first.
    """

    model: DeploymentModel
    params: dict[str, dict[str, Any]]
    param_sets: ParamSets
    chain: tuple[str, ...]


#: `(document, its file, problem)`: a problem found in one document of a chain.
_Located = tuple[str, str, ParamProblem]


@dataclass(frozen=True)
class _MergedSpec:
    """A chain's merged spec under one choice of parameter sets.

    `problems` stop the deployment resolving (a token that could not be filled, a value the model rejects).
    `findings` do not (a reference that names no document), as for a document loaded as written.
    """

    spec: dict[str, Any]
    problems: tuple[_Located, ...]
    findings: tuple[_Located, ...]


@dataclass(frozen=True)
class DeploymentSummary:
    """One `strata deploy list` row.

    `workspace`, `tenant`, `version` and `environments` are as resolved through `extends`; `extends` is the
    declared parent. `ring`, `order` and `wave` are the version document's `spec.promotion`, `None` when the
    deployment has no version or the document is not tagged.
    """

    name: str
    workspace: str | None
    extends: str | None
    tenant: str | None
    version: str | None
    ring: str | None
    order: int | None
    wave: str | None
    environments: tuple[str, ...]
    partial: bool
    source: str


def _version_rings(index: DocumentIndex) -> set[str]:
    """Every ring some version document declares."""
    rings: set[str] = set()
    for entry in index.all_of(PlatformKind.VERSION):
        promotion = cast(VersionModel, entry.model).spec.promotion
        if promotion is not None and promotion.ring is not None:
            rings.add(promotion.ring)
    return rings


def _require_known_filters(
    index: DocumentIndex, *, workspace: str | None, tenant: str | None, version: str | None, ring: str | None
) -> None:
    """A filter naming nothing is an error, not an empty list: a typo would otherwise pass a CI matrix with nothing in it."""
    for option, kind, value in (
        ("workspace", PlatformKind.WORKSPACE, workspace),
        ("tenant", PlatformKind.TENANT, tenant),
        ("version", PlatformKind.VERSION, version),
    ):
        if value is not None and index.get(kind, value) is None:
            raise UsageError(
                f"--{option} names no {kind.value} document: '{value}'. Available: {sorted(index.names_of(kind))}"
            )
    if ring is not None and ring not in (rings := _version_rings(index)):
        raise UsageError(f"--ring names no ring a version document declares: '{ring}'. Available: {sorted(rings)}")


def list_deployments(
    index: DocumentIndex,
    *,
    workspace: str | None = None,
    tenant: str | None = None,
    version: str | None = None,
    ring: str | None = None,
    deployable: bool = False,
) -> list[DeploymentSummary]:
    """Every deployment document, partial bases included and marked (docs/work/vscode-integrations-policies-views.md V1).

    The filters compare against the resolved values and combine with AND; `deployable` drops partial bases.

    Raises:
        UsageError: `workspace`, `tenant`, `version` or `ring` names nothing that exists.
    """
    _require_known_filters(index, workspace=workspace, tenant=tenant, version=version, ring=ring)
    resolved, _ = resolve_deployment_chains(index)
    rows = []
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        raw = cast(AnyDeploymentModel, entry.model)
        deployment = deployment_of(entry, resolved)
        # A template that did not resolve (a template base) is listed as written, `${param:}` tokens and all.
        spec = deployment.spec.model_dump() if deployment is not None else raw.spec.model_dump()
        version_name = spec.get("version")
        version_entry = index.get(PlatformKind.VERSION, version_name) if isinstance(version_name, str) else None
        promotion = cast(VersionModel, version_entry.model).spec.promotion if version_entry is not None else None
        row = DeploymentSummary(
            name=entry.ref.name,
            workspace=_text(spec.get("workspace")),
            extends=raw.spec.extends,
            tenant=_text(spec.get("tenant")),
            version=_text(version_name),
            ring=promotion.ring if promotion is not None else None,
            order=promotion.order if promotion is not None else None,
            wave=promotion.wave if promotion is not None else None,
            environments=tuple(str(item) for item in spec.get("environments") or ()),
            partial=bool(raw.spec.partial),
            source=str(entry.source),
        )
        if deployable and row.partial:
            continue
        wanted = ((workspace, row.workspace), (tenant, row.tenant), (version, row.version), (ring, row.ring))
        if any(want is not None and want != got for want, got in wanted):
            continue
        rows.append(row)
    return rows


def _text(value: Any) -> str | None:
    """A summary field as text: `None` stays `None` (a template may hold anything as written)."""
    return None if value is None else str(value)


def deployment_workspace(index: DocumentIndex, deployment: DeploymentModel) -> WorkspaceModel | None:
    """The workspace `deployment` names, or `None` when it names none or one not in the index.

    Never raises: validation and the audit manifest skip a missing workspace (the reference walk reports
    it); `value_controller.resolve_deployment_workspace()` is the raising form for commands.
    """
    if not deployment.spec.workspace:
        return None
    entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
    return cast(WorkspaceModel, entry.model) if entry is not None else None


def resolve_deployment_chains(index: DocumentIndex) -> tuple[dict[str, DeploymentModel], Diagnostics]:
    """`resolve_deployments()`, the merged models only: what every caller before parameter sets needed.

    Args:
        index: The loaded `DocumentIndex`.

    Returns:
        `(resolved, diagnostics)`, `resolved` mapping each deployable deployment's name to its merged model.
    """
    resolved, diagnostics = resolve_deployments(index)
    return {name: deployment.model for name, deployment in resolved.items()}, diagnostics


def resolve_deployments(index: DocumentIndex) -> tuple[dict[str, ResolvedDeployment], Diagnostics]:
    """Resolve every deployment's `extends` chain into a complete document.

    Args:
        index: The loaded `DocumentIndex`. Assumes Phase 1 already passed —
            same precondition as `run_semantic_checks`.

    Returns:
        `(resolved, diagnostics)`. `resolved` maps a deployment's own name to
        its fully-merged model — present for every deployment whose own
        document is not `partial: true`, whether or not it used `extends` at
        all (a plain deployment resolves to itself). A `partial: true`
        document is deliberately **not** in `resolved`, whether it is a root
        base or an intermediate link: it is not deployable, so the other
        checks have nothing to say about it — logged at debug, not reported
        as a finding. A deployment whose `${param:}` tokens cannot all be
        substituted is not in `resolved` either; each token is reported
        (`param_missing`, `param_not_scalar`) against the document that holds
        it, naming the deployment it was resolved for.
    """
    diagnostics = Diagnostics()
    chains: dict[str, tuple[str, ...] | None] = {}
    poisoned: set[str] = set()
    specs: dict[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], _MergedSpec] = {}
    resolved: dict[str, ResolvedDeployment] = {}
    reported_once: set[tuple[str, str, str]] = set()

    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        name = entry.ref.name
        deployment = cast(AnyDeploymentModel, entry.model)

        try:
            chain = _resolve_chain(name, index, chains, poisoned, visiting=[])
        except _CircularExtendsError as exc:
            diagnostics.error(str(exc), source=str(entry.source), location="spec.extends", code="circular_extends")
            continue

        if chain is None:
            if name in poisoned:
                # Part of a cycle first detected while resolving a *different*
                # deployment's chain; that occurrence already reported the
                # cycle itself, with the full path. This one just also fails.
                diagnostics.error(
                    f"Deployment '{name}': extends chain could not be resolved — "
                    "part of a circular extends chain reported elsewhere",
                    source=str(entry.source),
                    location="spec.extends",
                    code="circular_extends",
                )
            # Otherwise: an ancestor is simply missing from the index —
            # validate_references already reports that against spec.extends.
            continue

        if deployment.spec.partial:
            log.debug("deployment base has no consumers", name=name, source=str(entry.source))
            continue

        param_sets = _param_sets(chain, index)
        if param_sets is None:
            continue  # names a parameter set that does not exist: validate_references reports it on meta.params
        params = merge_param_values(param_sets, _set_values(param_sets, index))
        merged = _merged_spec(chain, param_sets, params, index, specs)
        for holder, source, problem in merged.findings + merged.problems:
            if not problem.per_leaf:
                if (holder, problem.location, problem.message) in reported_once:
                    continue
                reported_once.add((holder, problem.location, problem.message))
            diagnostics.error(
                f"{holder} {problem.location}: {problem.message}" + (f" (for {name})" if problem.per_leaf else ""),
                source=source,
                location=problem.location,
                code=problem.code,
            )
        if merged.problems:
            continue

        merged_dict = _merge_tenant_defaults(merged.spec, index)

        try:
            merged_model = DeploymentModel.model_validate({"meta": deployment.meta.model_dump(), "spec": merged_dict})
        except ValidationError as exc:
            diagnostics.extend(diagnostics_from_validation_error(exc), source=str(entry.source))
            continue

        diagnostics.extend(_check_name_rules(index, entry, chain, params, param_sets))

        resolved[name] = ResolvedDeployment(model=merged_model, params=params, param_sets=param_sets, chain=chain)

    diagnostics.extend(_unused_values(index, chains))
    return resolved, diagnostics


def _check_name_rules(
    index: DocumentIndex,
    entry: IndexEntry,
    chain: tuple[str, ...],
    params: dict[str, dict[str, Any]],
    param_sets: ParamSets,
) -> Diagnostics:
    """Rule 10: the deployable `entry` has exactly the name each `meta.name_rule` of its chain gives once filled in.

    The leaf's own rule counts too. A mismatch is reported on the leaf, which is what gets renamed; a rule whose
    `${param:}` cannot be filled is `param_missing` on the document that holds the rule.
    """
    diagnostics = Diagnostics()
    name = entry.ref.name
    for holder in reversed(chain):
        holder_entry = _require(index, PlatformKind.DEPLOYMENT, holder)
        rule = cast(AnyDeploymentModel, holder_entry.model).meta.name_rule
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
            f"Deployment '{name}' must be named '{expected}' by {rule_of} ('{rule}'). Rename the deployment, or fix "
            "the values in its parameter sets.",
            source=str(entry.source),
            location="meta.name",
            code="name_rule_mismatch",
        )
    return diagnostics


def _param_sets(chain: tuple[str, ...], index: DocumentIndex) -> ParamSets | None:
    """Role -> the parameter sets `chain` (leaf first) names, root first; `None` if one does not exist."""
    sets: dict[str, list[str]] = {}
    for name in reversed(chain):
        model = cast(AnyDeploymentModel, _require(index, PlatformKind.DEPLOYMENT, name).model)
        for role, named in (model.meta.params or {}).items():
            names = [named] if isinstance(named, str) else list(named)
            if any(index.get(PlatformKind.PARAMETERSET, set_name) is None for set_name in names):
                return None
            sets.setdefault(role, []).extend(names)
    return {role: tuple(names) for role, names in sets.items()}


def _set_values(param_sets: ParamSets, index: DocumentIndex) -> dict[str, dict[str, Any]]:
    """Parameter set name -> its `spec.values`, for every set `param_sets` names."""
    return {
        name: cast(ParameterSetModel, _require(index, PlatformKind.PARAMETERSET, name).model).spec.values
        for names in param_sets.values()
        for name in names
    }


def _merged_spec(
    chain: tuple[str, ...],
    param_sets: ParamSets,
    params: dict[str, dict[str, Any]],
    index: DocumentIndex,
    cache: dict[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], _MergedSpec],
) -> _MergedSpec:
    """The merged spec of `chain` (leaf first), each document substituted with `params` before it is merged.

    Memoised per (name, sets named): a base shared by several leaves (a diamond) is folded once per choice of
    parameter sets, not once per descendant. The sets determine the values, so they are the whole key.
    """
    name = chain[0]
    key = (name, tuple(sorted(param_sets.items())))
    if key in cache:
        return cache[key]

    entry = _require(index, PlatformKind.DEPLOYMENT, name)
    document = cast(AnyDeploymentModel, entry.model)
    origins: dict[str, ParamOrigin] = {}
    own, own_problems = substitute_params(
        document.spec.model_dump(exclude_none=True), params, param_sets, "spec", origins
    )
    own_findings: list[ParamProblem] = []
    if isinstance(document, DeploymentTemplateModel) and not own_problems:
        describe = _OriginText(origins, param_sets, _set_values(param_sets, index))
        own, own_problems, own_findings = _validate_substituted(own, index, describe)
    source = str(entry.source)
    problems = tuple((name, source, problem) for problem in own_problems)
    findings = tuple((name, source, finding) for finding in own_findings)

    if len(chain) == 1:
        result = _MergedSpec(spec=own, problems=problems, findings=findings)
    else:
        base = _merged_spec(chain[1:], param_sets, params, index, cache)
        result = _MergedSpec(
            spec=merge_deployment_specs(base.spec, own),
            problems=base.problems + problems,
            findings=base.findings + findings,
        )
    cache[key] = result
    return result


def _bracketed(location: str) -> str:
    """A model's dotted location (`environments.1`) as substitution writes one (`spec.environments[1]`)."""
    path = "spec"
    for part in location.split(".") if location else ():
        path += f"[{part}]" if part.isdigit() else f".{part}"
    return path


class _OriginText:
    """Says where a substituted value of one template document came from, for a finding at that location."""

    def __init__(
        self, origins: dict[str, ParamOrigin], param_sets: ParamSets, set_values: dict[str, dict[str, Any]]
    ) -> None:
        self.origins = origins
        self.param_sets = param_sets
        self.set_values = set_values

    def supplier(self, role: str, key: str) -> str:
        """The parameter set the value of `role.key` comes from: the last one of the role that holds it."""
        names = self.param_sets.get(role, ())
        if not key:
            return ", ".join(names)
        path = key.split(".")
        found = [name for name in names if walk_value_path(self.set_values[name], path) is not VALUE_PATH_MISSING]
        return found[-1] if found else ", ".join(names)

    def of(self, location: str) -> str | None:
        """`from 'hub-${param:spoke.hub}-env': spoke.hub = 'hub9' (spoke-acme-001)`, or `None` if nothing was
        substituted at `location` (`spec.environments[1]`)."""
        origin = self.origins.get(location)
        if origin is None:
            return None
        reads = ", ".join(
            f"{read.role}{'.' + read.key if read.key else ''} = {read.value!r} ({self.supplier(read.role, read.key)})"
            for read in origin.reads
        )
        return f"from {origin.text!r}: {reads}"


def _validate_substituted(
    spec: dict[str, Any], index: DocumentIndex, describe: _OriginText
) -> tuple[dict[str, Any], list[ParamProblem], list[ParamProblem]]:
    """A template's spec, substituted: validated as the spec it now is, dumped as a loaded document's would be, and
    its references checked.

    What the loader and the reference walk do for every other deployment, done per instance
    (`base-parameters-v2.md` (work doc), "Templates are validated per instance"): the dump normalises the spec, so it
    merges like any other. Each finding is located in the template and says which values made the field what it
    is. A reference the template writes without a token is not per leaf: it is the same for every deployment.

    Returns:
        `(spec, problems, findings)`: problems stop the deployment resolving, findings do not.
    """
    try:
        model = DeploymentSpecModel.model_validate(spec)
    except ValidationError as exc:
        problems = []
        for finding in diagnostics_from_validation_error(exc).errors:
            location = _bracketed(finding.location or "")
            origin = describe.of(location)
            problems.append(
                ParamProblem(
                    location=location,
                    message=f"invalid after ${{param:}} substitution: {finding.message}"
                    + (f", {origin}" if origin else ""),
                    code=finding.code or "invalid_value",
                )
            )
        return spec, problems, []

    findings = []
    for location, message in unresolved_references(index, model):
        bracketed = _bracketed(location)
        origin = describe.of(bracketed)
        findings.append(
            ParamProblem(
                location=bracketed,
                message=f"{message}, {origin}" if origin else message,
                code="unknown_reference",
                per_leaf=origin is not None,
            )
        )
    return model.model_dump(exclude_none=True), [], findings


def _unused_values(index: DocumentIndex, chains: Mapping[str, tuple[str, ...] | None]) -> Diagnostics:
    """Rule 12: an `info` finding (`param_unused`) per top-level key of a parameter set that no document reads.

    A key is read when a document of some chain holds `${param:<role>.<key>...}` (in its spec or `meta.name_rule`)
    and that chain names the set under `<role>`; `${param:<role>}` alone reads every key. A
    `${value:parameterset.<name>.spec.values.<key>...}` token in any document reads `<key>` of that set too
    (`_value_token_reads()`). A set nothing names or reads gets one finding of its own. Info, not a warning:
    `validate --strict` fails on warnings, and a values file holding more than one deployment reads is normal (a
    spoke's set serves seven deployments).
    """
    reads_of: dict[str, set[tuple[str, tuple[str, ...]]]] = {}

    def reads(name: str) -> set[tuple[str, tuple[str, ...]]]:
        if name not in reads_of:
            document = cast(AnyDeploymentModel, _require(index, PlatformKind.DEPLOYMENT, name).model)
            reads_of[name] = param_reads_in(document.spec.model_dump(exclude_none=True)) | param_reads_in(
                document.meta.name_rule or ""
            )
        return reads_of[name]

    named: set[str] = set()
    read_whole: set[str] = set()
    read_keys: dict[str, set[str]] = {}
    for chain in chains.values():
        if chain is None:
            continue
        sets = _param_sets(chain, index)
        if sets is None:
            continue
        for names in sets.values():
            named.update(names)
        for role, path in set().union(*(reads(name) for name in chain)):
            for set_name in sets.get(role, ()):
                if path:
                    read_keys.setdefault(set_name, set()).add(path[0])
                else:
                    read_whole.add(set_name)
    value_whole, value_keys = _value_token_reads(index)
    read_whole |= value_whole
    for set_name, keys in value_keys.items():
        read_keys.setdefault(set_name, set()).update(keys)
    named |= value_whole | set(value_keys)

    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.PARAMETERSET):
        name = entry.ref.name
        if name not in named:
            diagnostics.info(
                f"Parameter set '{name}' is named in no deployment's meta.params and read by no "
                "${value:parameterset...} token, so none of its values is read.",
                source=str(entry.source),
                location="meta.name",
                code="param_unused",
            )
            continue
        if name in read_whole:
            continue
        for key in cast(ParameterSetModel, entry.model).spec.values:
            if key not in read_keys.get(name, set()):
                diagnostics.info(
                    f"Parameter set '{name}': value '{key}' is read by no document (no ${{param:<role>.{key}}} in "
                    f"a chain that names this set under that role, and no ${{value:parameterset.{name}.spec.values."
                    f"{key}}}).",
                    source=str(entry.source),
                    location=f"spec.values.{key}",
                    code="param_unused",
                )
    return diagnostics


def _value_token_reads(index: DocumentIndex) -> tuple[set[str], dict[str, set[str]]]:
    """The parameter-set values `${value:parameterset.<name>.spec.values.<key>...}` tokens read, in any document.

    Returns `(sets read whole, set -> top-level keys read)`. A token that stops above a key
    (`${value:parameterset.<name>.spec.values}`) reads the set whole; `${value:}` rejects it as not a single value,
    but the author clearly uses the set, so it is not reported unused on top.
    """
    whole: set[str] = set()
    keys: dict[str, set[str]] = {}

    def strings(node: Any) -> Iterator[str]:
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for child in node.values():
                yield from strings(child)
        elif isinstance(node, list):
            for child in node:
                yield from strings(child)

    for entry in index.all():
        dumped = entry.model.model_dump(by_alias=True, mode="json")
        dumped.pop("meta", None)  # as `${value:}` resolution itself: a token in meta is never resolved
        for text in strings(dumped):
            for kind, key in extract_value_tokens(text):
                segments = key.split(".")
                if kind != "value" or segments[0] != PlatformKind.PARAMETERSET.value or len(segments) < 2:
                    continue
                if len(segments) >= 5 and segments[2:4] == ["spec", "values"]:
                    keys.setdefault(segments[1], set()).add(segments[4])
                else:
                    whole.add(segments[1])
    return whole, keys


def deployment_of(entry: IndexEntry, resolved: Mapping[str, DeploymentModel]) -> DeploymentModel | None:
    """The deployment checks read for `entry`: its resolved model, else its own document, else `None`.

    `None` is a template that did not resolve: a template base no leaf completes on its own, or a leaf whose
    values are missing (already reported). Its spec is not a valid deployment as written, so a check skips it, as
    it skips what a partial base lacks; the leaves that use it are checked resolved.
    """
    found = resolved.get(entry.ref.name)
    if found is not None:
        return found
    return entry.model if isinstance(entry.model, DeploymentModel) else None


def _merge_tenant_defaults(spec_dict: dict[str, Any], index: DocumentIndex) -> dict[str, Any]:
    """Fold a tenant's `properties`/`custom`/`environments` in as a base
    layer under the deployment's own (ADR-0024) — deployment always wins on
    a key conflict, same `merge_deployment_specs()` semantics `extends`
    already uses. Applied *after* `extends` resolution, not before:
    `spec_dict["tenant"]` is already the deployment's own final value by
    this point, however many `extends` links deep it came from — tenant is
    one further outer layer, underneath the already-fully-resolved dict.

    Only `environments`/`properties`/`custom` travel from the tenant, not
    its whole spec — `display_name`/`geographies`/`onboarded` are
    tenant-identity fields with no `DeploymentSpecModel` equivalent to
    merge into.

    A missing/unresolvable `tenant` reference silently no-ops — the same
    treatment `_resolve_chain()` gives a missing `extends` ancestor;
    `validate_references` is the layer that reports a bad `spec.tenant`.
    """
    tenant_name = spec_dict.get("tenant")
    if not tenant_name:
        return spec_dict
    tenant_entry = index.get(PlatformKind.TENANT, tenant_name)
    if tenant_entry is None:
        return spec_dict
    tenant = cast(TenantModel, tenant_entry.model)
    tenant_base = {
        "environments": tenant.spec.environments or [],
        "properties": tenant.spec.properties or {},
        "custom": tenant.spec.custom or {},
    }
    return merge_deployment_specs(tenant_base, spec_dict)


def _require(index: DocumentIndex, kind: PlatformKind, name: str) -> IndexEntry:
    """The entry for a document already known to exist (a resolved chain link or a checked parameter set)."""
    entry = index.get(kind, name)
    assert entry is not None, f"{kind.value}/{name} vanished from the index"
    return entry


def _resolve_chain(
    name: str,
    index: DocumentIndex,
    cache: dict[str, tuple[str, ...] | None],
    poisoned: set[str],
    visiting: list[str],
) -> tuple[str, ...] | None:
    """Return deployment `name` and its bases, leaf first, recursively (pass one).

    Memoised per name: a base shared by several leaves (a diamond) is
    walked once, not once per descendant.

    Returns:
        `None` for two distinct reasons, distinguished via `poisoned`: the
        chain is missing an ancestor entirely (not poisoned — already
        reported elsewhere), or an ancestor is part of a cycle (poisoned —
        the caller reports its own pointer).

    Raises:
        _CircularExtendsError: Only from the exact call that closes the loop, so
            its message names the real cycle.
    """
    if name in cache:
        return cache[name]

    if name in visiting:
        poisoned.add(name)
        raise _CircularExtendsError([*visiting, name])

    entry = index.get(PlatformKind.DEPLOYMENT, name)
    if entry is None:
        return None  # missing ancestor — not a cycle, not cached: nothing to memoise

    deployment = entry.model
    assert isinstance(deployment, (DeploymentModel, DeploymentTemplateModel))

    if not deployment.spec.extends:
        cache[name] = (name,)
        return cache[name]

    try:
        base_chain = _resolve_chain(deployment.spec.extends, index, cache, poisoned, [*visiting, name])
    except _CircularExtendsError:
        poisoned.add(name)
        cache[name] = None
        raise

    if base_chain is None:
        if deployment.spec.extends in poisoned:
            poisoned.add(name)
        cache[name] = None
        return None

    cache[name] = (name, *base_chain)
    return cache[name]
