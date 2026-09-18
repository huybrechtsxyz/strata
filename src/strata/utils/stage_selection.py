"""Deployment stage selection — which stages run, and why the others do not (ADR-0083).

One implementation shared by ``deploy run`` and ``deploy destroy``. Before this
module the ``--stage``/``--scope`` filter was copy-pasted between the two
commands, and had already drifted: the two ``--stage`` not-found messages were
worded differently. Both now produce identical, single-sourced errors.

Distinct from ``provisioner_resolution``, which answers *"which namespaces or
secrets does this stage touch"* — this module answers *"does this stage run at
all"*.

Selection order (ADR-0083 Design §3)::

    1. evaluate `enabled`                  → disabled set
    2. propagate skip through depends_on   → skipped set
    3. apply --stage filter
    4. apply --scope filter
    5. to_run = filtered - skipped, in dependency order

``StageSkip``/``StageSelection`` are declared in their final shape so no phase
changes this module's return type.

Two properties are load-bearing and easy to break:

* **Ordering is stable.** ``order_stages`` dequeues ready nodes in declaration
  order, so a deployment file that is already in a valid order comes back
  unchanged. Without that, giving ``depends_on`` runtime meaning would silently
  reorder every existing deployment.
* **Cascade follows ``enabled`` only, never ``--stage``/``--scope``.** A stage
  gated off is a durable statement that dependents must respect; an operator
  scoping one run is not. Cascading CLI filtering would make ``--stage B``
  impossible whenever ``B`` declares a dependency.
"""

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from strata.utils.resolved_values import collect_expr_refs, parse_bool, resolve_expr_string

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from strata.models.deployment_model import DeploymentStageModel
    from strata.utils.resolved_values import ResolvedValues


class StageSelectionMode(Enum):
    """What a command intends to do with the stages it selects (ADR-0083 D7).

    Replaces an ``apply_gating``/``apply_ordering`` boolean pair, which admitted
    four combinations of which only two were meaningful — and where a wrong
    pairing failed silently. Each member records *why* it behaves as it does, so
    the reasons survive even where two modes currently behave identically.
    """

    DEPLOY = "deploy"
    """Honour ``enabled``; run in dependency order. ``deploy run`` only."""

    DESTROY = "destroy"
    """Never gate — gating a teardown would strand infrastructure created while
    the flag was on (D3). Declaration order, because a correct teardown needs the
    *reverse* order and that is a decision ADR-0083 does not make."""

    INSPECT = "inspect"
    """Read-only surfaces: never gate, never reorder (D11).

    Load-bearing, not merely descriptive: ``build plan``'s ``would_skip`` marker
    is computed per returned stage, so gating here would filter disabled stages
    out first and silently turn that marker into dead code."""

    @property
    def gates(self) -> bool:
        """Whether ``enabled`` is honoured in this mode."""
        return self is StageSelectionMode.DEPLOY

    @property
    def orders(self) -> bool:
        """Whether ``to_run`` is sorted into dependency order."""
        return self is StageSelectionMode.DEPLOY


#: Why a stage was excluded. ``dependency_skipped`` is declared now but cannot
#: occur until ``depends_on`` cascade lands (ADR-0083 Phase 5).
SkipReason = Literal["disabled", "dependency_skipped"]


@dataclass(frozen=True)
class StageSkip:
    """A stage that will not run, and why.

    Persisted to the deployment manifest and deploy-log so the audit trail can
    distinguish "deliberately not deployed" from "absent" (ADR-0083 D6).

    Attributes:
        stage_name: Name of the stage that will not run.
        reason: Machine-readable cause.
        detail: Human-readable explanation, recorded verbatim.
        expression: The raw ``enabled`` source, when ``reason`` is ``disabled``.
        resolved_value: What ``expression`` resolved to, when available.
    """

    stage_name: str
    reason: SkipReason
    detail: str
    expression: Optional[str] = None
    resolved_value: Optional[str] = None


@dataclass(frozen=True)
class StageSelection:
    """Outcome of :func:`select_stages`.

    Attributes:
        to_run: Stages that will execute, in execution order.
        skipped: Stages gated out by ``enabled``, in declaration order, limited to
            stages this invocation would otherwise have run. ``--stage``/``--scope``
            narrowing is deliberately *not* a skip: an operator scoping a single
            run makes no statement about whether the other stages apply, and
            recording them as skipped would pollute the audit trail.
    """

    to_run: List["DeploymentStageModel"] = field(default_factory=list)
    skipped: List[StageSkip] = field(default_factory=list)


def evaluate_enabled(
    stage: "DeploymentStageModel",
    resolved: Optional["ResolvedValues"],
) -> Tuple[bool, Optional[StageSkip], Optional[str]]:
    """Decide whether one stage's ``enabled`` gate lets it run (ADR-0083 D1).

    Args:
        stage: The stage to evaluate.
        resolved: Values to resolve ``${...}`` references against. May be ``None``
            only when no stage uses an expression.

    Returns:
        ``(is_enabled, skip, error)``. Exactly one of *skip* / *error* is set when
        *is_enabled* is False. An unresolvable reference is always an *error*,
        never a silent "disabled" — a typo must not quietly drop a stage from a
        deployment (ADR-0075's fail-loud driver).
    """
    raw: Any = getattr(stage, "enabled", None)

    if raw is None or raw is True:
        return True, None, None

    if raw is False:
        return (
            False,
            StageSkip(
                stage_name=stage.name,
                reason="disabled",
                detail="'enabled' is false",
                expression="false",
                resolved_value="false",
            ),
            None,
        )

    # Any remaining value is a string: the model validator guarantees it.
    if collect_expr_refs(raw):
        if resolved is None:
            return (
                False,
                None,
                f"Stage '{stage.name}': 'enabled' is the expression '{raw}', but no resolved "
                "variables/features are available to evaluate it.",
            )
        value, expr_errors = resolve_expr_string(raw, resolved)
        if expr_errors:
            return (
                False,
                None,
                f"Stage '{stage.name}': 'enabled' expression '{raw}' could not be resolved — {'; '.join(expr_errors)}.",
            )
    else:
        value = raw

    if parse_bool(value):
        return True, None, None

    # Name BOTH the authored expression and what it resolved to: this string is
    # persisted verbatim as the manifest/deploy-log skip reason, where "resolved to
    # false" alone would not say which flag was responsible (ADR-0083 D6).
    detail = f"'enabled' ({raw}) resolved to '{value}'" if value != raw else f"'enabled' is '{raw}'"
    return (
        False,
        StageSkip(
            stage_name=stage.name,
            reason="disabled",
            detail=detail,
            expression=raw,
            resolved_value=value,
        ),
        None,
    )


def _dependency_names(stage: "DeploymentStageModel") -> List[str]:
    """Return a stage's ``depends_on`` entries, tolerating ``None``."""
    return list(getattr(stage, "depends_on", None) or [])


def order_stages(
    stages: List["DeploymentStageModel"],
) -> Tuple[List["DeploymentStageModel"], List[str]]:
    """Return *stages* in dependency order (ADR-0083 D5, Design §4).

    Kahn's algorithm, with ready nodes dequeued in **declaration order**. That
    tie-break is the whole safety story: if a deployment file is already in a
    valid order, the output is identical to the input, so giving ``depends_on``
    runtime meaning cannot silently reorder an existing deployment.

    Dependencies naming a stage outside *stages* are ignored, not an error — the
    caller may legitimately have narrowed the set with ``--stage``/``--scope``.
    Authoring-level checks live in :func:`validate_stage_dependencies`.

    Returns:
        ``(ordered_stages, errors)``. A non-empty *errors* means a cycle was
        found and *ordered_stages* is empty.
    """
    position: Dict[str, int] = {stage.name: i for i, stage in enumerate(stages)}
    dependents: Dict[str, List[str]] = {name: [] for name in position}
    remaining: Dict[str, int] = {name: 0 for name in position}

    for stage in stages:
        for dep in _dependency_names(stage):
            if dep in position and dep != stage.name:
                dependents[dep].append(stage.name)
                remaining[stage.name] += 1

    ready = [position[name] for name, count in remaining.items() if count == 0]
    heapq.heapify(ready)

    ordered: List["DeploymentStageModel"] = []
    while ready:
        stage = stages[heapq.heappop(ready)]
        ordered.append(stage)
        for dependent in dependents[stage.name]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, position[dependent])

    if len(ordered) < len(stages):
        blocked = sorted(name for name, count in remaining.items() if count > 0)
        return [], [
            f"Stage 'depends_on' cycle detected: {blocked} can never run because their "
            "dependencies depend on them, directly or transitively."
        ]

    return ordered, []


def validate_stage_dependencies(all_stages: List["DeploymentStageModel"]) -> List[str]:
    """Authoring-time validation of ``depends_on`` (ADR-0083 D5).

    Checks a deployment's *declared* stages, so it is deliberately separate from
    :func:`order_stages`, which tolerates dependencies filtered out by the CLI.

    Returns:
        A list of error messages; empty when the dependency graph is sound.
    """
    errors: List[str] = []
    known = {stage.name for stage in all_stages}

    for stage in all_stages:
        for dep in _dependency_names(stage):
            if dep == stage.name:
                errors.append(f"Stage '{stage.name}': 'depends_on' lists the stage itself.")
            elif dep not in known:
                errors.append(
                    f"Stage '{stage.name}': 'depends_on' references unknown stage '{dep}'. Available: {sorted(known)}"
                )

    if errors:
        # Cycle detection needs a well-formed graph; reporting both at once would
        # bury the concrete typo under a derived complaint.
        return errors

    _, cycle_errors = order_stages(all_stages)
    return cycle_errors


def _propagate_dependency_skips(
    all_stages: List["DeploymentStageModel"],
    gated_out: List[StageSkip],
) -> List[StageSkip]:
    """Transitively skip every dependent of a skipped stage (ADR-0083 D5).

    Matches GitHub Actions' ``needs:`` default, which is what most operators will
    expect: a job whose dependency was skipped is itself skipped.

    The reason names the *direct* dependency that caused it, so a transitive
    chain (``a`` disabled → ``b`` → ``c``) can be walked back one hop at a time
    rather than all pointing at a distant root.
    """
    skipped_names = {skip.stage_name for skip in gated_out}
    if not skipped_names:
        return gated_out

    cascaded = list(gated_out)
    changed = True
    while changed:
        changed = False
        for stage in all_stages:
            if stage.name in skipped_names:
                continue
            blocking = next((dep for dep in _dependency_names(stage) if dep in skipped_names), None)
            if blocking is not None:
                cascaded.append(
                    StageSkip(
                        stage_name=stage.name,
                        reason="dependency_skipped",
                        detail=f"depends on skipped stage '{blocking}'",
                    )
                )
                skipped_names.add(stage.name)
                changed = True

    position = {stage.name: i for i, stage in enumerate(all_stages)}
    cascaded.sort(key=lambda skip: position.get(skip.stage_name, 0))
    return cascaded


def select_stages(
    all_stages: List["DeploymentStageModel"],
    *,
    stage: Optional[str] = None,
    scope: Optional[str] = None,
    resolved: Optional["ResolvedValues"] = None,
    mode: StageSelectionMode = StageSelectionMode.DEPLOY,
) -> Tuple[StageSelection, List[str]]:
    """Resolve which stages run for this invocation.

    Args:
        all_stages: Every stage declared on the deployment, in declaration order.
        stage: Value of ``--stage`` — restrict to the single stage of that name.
        scope: Value of ``--scope`` — restrict to stages carrying that label.
        resolved: Values used to evaluate ``enabled`` expressions.
        mode: What the caller intends to do with the result — see
            :class:`StageSelectionMode`. Determines whether ``enabled`` is
            honoured and whether ``to_run`` is dependency-ordered.

    Returns:
        ``(selection, errors)``. Errors are returned rather than raised, matching
        the deploy commands' existing ``self._errors.append(...)`` convention; a
        non-empty list means the caller should abort and ``selection`` is not
        meaningful.
    """
    errors: List[str] = []
    gated_out: List[StageSkip] = []

    if mode.gates:
        for candidate in all_stages:
            _enabled, skip, error = evaluate_enabled(candidate, resolved)
            if error:
                errors.append(error)
            elif skip is not None:
                gated_out.append(skip)
        if errors:
            return StageSelection(), errors
        gated_out = _propagate_dependency_skips(all_stages, gated_out)

    disabled_names = {skip.stage_name for skip in gated_out}

    selected = [s for s in all_stages if s.name == stage] if stage else list(all_stages)
    if stage and not selected:
        errors.append(f"Stage '{stage}' not found in deployment definition. Available: {[s.name for s in all_stages]}")
        return StageSelection(), errors

    # ADR-0083 D4: naming a disabled stage explicitly is an error, not a silent
    # skip and not an override. Unlike --namespace vs helm_namespaces, `enabled` is
    # a correctness condition for the environment, so forcing it would create
    # infrastructure the environment declares inapplicable.
    if stage and stage in disabled_names:
        skip = next(sk for sk in gated_out if sk.stage_name == stage)
        if skip.reason == "disabled":
            errors.append(
                f"Stage '{stage}' is disabled in this environment ({skip.detail}) and cannot be "
                "selected with --stage. Enable it for this environment, or drop --stage to run "
                "the stages that do apply."
            )
        else:
            errors.append(
                f"Stage '{stage}' is skipped because it {skip.detail}, and cannot be selected "
                "with --stage. Enable the stage it depends on, or drop --stage to run the "
                "stages that do apply."
            )
        return StageSelection(), errors

    if scope:
        selected = [s for s in selected if s.scope == scope]
        if not selected:
            errors.append(
                f"No stages match scope '{scope}'. Available scopes: {[s.scope for s in all_stages if s.scope]}"
            )
            return StageSelection(), errors

    # A scope whose stages are all disabled is a legitimate no-op, not an error:
    # the stages matched, the environment simply does not want them.
    selected_names = {s.name for s in selected}
    to_run = [s for s in selected if s.name not in disabled_names]

    if mode.orders:
        to_run, order_errors = order_stages(to_run)
        if order_errors:
            return StageSelection(), order_errors

    return (
        StageSelection(
            to_run=to_run,
            skipped=[skip for skip in gated_out if skip.stage_name in selected_names],
        ),
        errors,
    )
