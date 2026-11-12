#!/usr/bin/env python3
"""`strata deploy steps` - a deployment's execution steps as `deploy run` selects and orders them
(docs/design/deploy-steps-command.md).

Reads the solution only. `check_tools` is the one exception: it asks each listed step's tool, on this machine,
whether it is ready (PATH, version), as `deploy run`'s preflight does. Nothing here contacts a remote service.

Order and level are computed over every step of the workspace, before `--stage`/`--scope`, so the numbers do not
change with a filter. Only what `deploy run` would apply is shown: stage parameters strata accepts but ignores are
left out.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from strata.controllers.build_controller import ordered_by_depends_on
from strata.controllers.integration_readiness import IntegrationReadiness, assess_integration
from strata.controllers.integration_resolution import find_provisioner, resolve_integration
from strata.controllers.solution_context import SolutionContext
from strata.controllers.step_selection import select_steps
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.models.deployment_model import DeploymentStageModel
from strata.models.integration_model import Capability
from strata.models.provisioning_model import ProvisioningStepModel
from strata.models.workspace_model import effective_resources_and_namespaces
from strata.utils.diagnostics import Diagnostics

Direction = Literal["apply", "destroy"]

_TARGET_FIELDS = ("resources", "namespaces", "topologies", "dns_zones", "firewalls", "networks", "assets")


@dataclass(frozen=True)
class StepTool:
    """The tool a step needs, read from the solution."""

    name: str
    type: str
    transport: str | None
    command: str | None
    required_version: str | None


@dataclass(frozen=True)
class StepProblem:
    code: str
    message: str


@dataclass(frozen=True)
class StepReadiness:
    """`--check-tools`: what this machine has for the step's tool."""

    available: bool | None
    installed_version: str | None
    tool_path: str | None
    problems: list[StepProblem]
    warnings: list[StepProblem]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class StepCapabilities:
    """What the deploy commands can do with the step: `deploy apply` (`saved_plan`), `deploy destroy` (`destroy`),
    and whether it takes the container path (Helm, Compose)."""

    saved_plan: bool
    destroy: bool
    container: bool


@dataclass(frozen=True)
class StepRow:
    name: str
    scope: str | None
    order: int
    level: int
    depends_on: list[str]
    provisioner: str
    integration: StepTool
    readiness: StepReadiness | None
    capabilities: StepCapabilities
    targets: dict[str, list[str]]
    effective: dict[str, list[str]]
    description: str | None
    timeouts: dict[str, int | None] | None


@dataclass(frozen=True)
class ScopeRow:
    scope: str | None
    steps: list[str]
    depends_on: list[str | None]
    orderable: bool


@dataclass
class DeploymentSteps:
    deployment: str
    workspace: str
    partial: bool
    direction: Direction
    stages: list[str]
    scopes_filter: list[str]
    unmatched_stages: list[str]
    unmatched_scopes: list[str]
    tools_checked: bool
    steps: list[StepRow]
    scopes: list[ScopeRow]
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    def to_data(self) -> dict[str, Any]:
        """The `-o json` `data` (docs/design/deploy-steps-command.md, JSON)."""
        return {
            "deployment": self.deployment,
            "workspace": self.workspace,
            "partial": self.partial,
            "direction": self.direction,
            "filters": {"stages": list(self.stages), "scopes": list(self.scopes_filter)},
            "unmatched": {"stages": list(self.unmatched_stages), "scopes": list(self.unmatched_scopes)},
            "tools_checked": self.tools_checked,
            "steps": [asdict(row) for row in self.steps],
            "scopes": [asdict(row) for row in self.scopes],
        }


def deployment_steps(
    context: SolutionContext,
    deployment_name: str,
    *,
    stages: Sequence[str] = (),
    scopes: Sequence[str] = (),
    destroy: bool = False,
    check_tools: bool = False,
) -> DeploymentSteps:
    """List `deployment_name`'s execution steps as `deploy run` (or, with `destroy`, `deploy destroy`) would run them.

    Raises:
        UsageError: An unknown deployment, or one that names no workspace (from `resolve_deployment_workspace()`).
    """
    deployment, workspace = resolve_deployment_workspace(context, deployment_name)
    index = context.controller.index
    diagnostics = Diagnostics()
    direction: Direction = "destroy" if destroy else "apply"

    all_steps = workspace.spec.execution or []
    ordered = ordered_by_depends_on(all_steps)
    if destroy:
        ordered = list(reversed(ordered))
    order = {step.name: position for position, step in enumerate(ordered)}
    level = _levels(ordered, destroy=destroy)
    selection = select_steps(ordered, stages=stages, scopes=scopes)

    resources_by_name = {r.name: r for r in (workspace.spec.resources or []) if r.enabled}
    topologies_by_name = {t.name: t for t in (workspace.spec.topology or [])}
    stage_by_step = {stage.step: stage for stage in (deployment.spec.stages or [])}
    readiness_by_tool: dict[tuple[str, str | None], IntegrationReadiness] = {}

    rows: list[StepRow] = []
    for step in selection.steps:
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(index, provisioner)
        readiness: StepReadiness | None = None
        if check_tools:
            tool_key = (integration.name, integration.command)
            assessed = readiness_by_tool.get(tool_key)
            first = assessed is None
            if assessed is None:
                assessed = assess_integration(integration, read_version=True)
                readiness_by_tool[tool_key] = assessed
            # Findings once per tool, at the first step that uses it, as `deploy run`'s preflight reports them.
            readiness = _readiness(
                assessed, step, provisioner.name, destroy=destroy, diagnostics=diagnostics if first else None
            )
        resources_eff, namespaces_eff = effective_resources_and_namespaces(step, resources_by_name, topologies_by_name)
        stage = stage_by_step.get(step.name)
        rows.append(
            StepRow(
                name=step.name,
                scope=step.scope,
                order=order[step.name],
                level=level[step.name],
                depends_on=list(step.depends_on or []),
                provisioner=provisioner.name,
                integration=_tool(integration),
                readiness=readiness,
                capabilities=_capabilities(integration),
                targets={name: list(getattr(step, name) or []) for name in _TARGET_FIELDS},
                effective={"resources": sorted(resources_eff), "namespaces": sorted(namespaces_eff)},
                description=stage.description if stage is not None else None,
                timeouts=_timeouts(stage),
            )
        )

    return DeploymentSteps(
        deployment=deployment_name,
        workspace=workspace.meta.name,
        partial=bool(deployment.spec.partial),
        direction=direction,
        stages=list(stages),
        scopes_filter=list(scopes),
        unmatched_stages=selection.unmatched_stages,
        unmatched_scopes=selection.unmatched_scopes,
        tools_checked=check_tools,
        steps=rows,
        scopes=_scopes(ordered, rows, destroy=destroy, diagnostics=diagnostics),
        diagnostics=diagnostics,
    )


def _levels(ordered: list[ProvisioningStepModel], *, destroy: bool) -> dict[str, int]:
    """0 for a step with nothing before it in `ordered`'s direction, else one more than the highest level before it.

    `ordered` runs every predecessor first: dependencies when applying, dependents when destroying.
    """
    before: dict[str, list[str]] = {step.name: [] for step in ordered}
    for step in ordered:
        for dependency in step.depends_on or []:
            if dependency not in before:
                continue
            if destroy:
                before[dependency].append(step.name)
            else:
                before[step.name].append(dependency)
    level: dict[str, int] = {}
    for step in ordered:
        level[step.name] = max((level[name] + 1 for name in before[step.name]), default=0)
    return level


def _scopes(
    ordered: list[ProvisioningStepModel], rows: list[StepRow], *, destroy: bool, diagnostics: Diagnostics
) -> list[ScopeRow]:
    """One row per scope among `rows`; edges and order come from every step (`ordered`), not just the selected."""
    scope_of = {step.name: step.scope for step in ordered}
    first_order: dict[str | None, int] = {}
    for position, step in enumerate(ordered):
        first_order.setdefault(step.scope, position)

    edges: dict[str | None, set[str | None]] = {scope: set() for scope in first_order}
    for step in ordered:
        for dependency in step.depends_on or []:
            if dependency not in scope_of or scope_of[dependency] == step.scope:
                continue
            waiting, waited_for = (scope_of[dependency], step.scope) if destroy else (step.scope, scope_of[dependency])
            edges[waiting].add(waited_for)

    cyclic = {scope for scope in edges if _reaches(edges, scope, scope)}
    sequence = _scope_sequence(edges, first_order, cyclic)

    selected: dict[str | None, list[str]] = {}
    for row in rows:
        selected.setdefault(row.scope, []).append(row.name)

    result: list[ScopeRow] = []
    for scope in sequence:
        if scope not in selected:
            continue
        if scope in cyclic:
            diagnostics.warning(
                f"Scope '{scope if scope is not None else '(no scope)'}' and another scope depend on each other "
                "through their steps, so one job per scope cannot be ordered; one job per step can.",
                location=scope if scope is not None else "(no scope)",
                code="scope_cycle",
            )
        result.append(
            ScopeRow(
                scope=scope,
                steps=selected[scope],
                depends_on=sorted(edges[scope], key=lambda other: first_order[other]),
                orderable=scope not in cyclic,
            )
        )
    return result


def _reaches(edges: dict[str | None, set[str | None]], start: str | None, target: str | None) -> bool:
    seen: set[str | None] = set()
    pending = list(edges[start])
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(edges[current])
    return False


def _scope_sequence(
    edges: dict[str | None, set[str | None]], first_order: dict[str | None, int], cyclic: set[str | None]
) -> list[str | None]:
    """Topological over the acyclic scopes, ties by their first step; scopes on a cycle by their first step, last."""
    remaining = {scope: {other for other in waited if other not in cyclic} for scope, waited in edges.items()}
    sequence: list[str | None] = []
    done: set[str | None] = set()
    acyclic = [scope for scope in edges if scope not in cyclic]
    while len(done) < len(acyclic):
        ready = [scope for scope in acyclic if scope not in done and remaining[scope] <= done]
        if not ready:  # only reachable through a cyclic scope; order the rest by first step
            ready = [scope for scope in acyclic if scope not in done]
        nxt = min(ready, key=lambda scope: first_order[scope])
        sequence.append(nxt)
        done.add(nxt)
    sequence.extend(sorted(cyclic, key=lambda scope: first_order[scope]))
    return sequence


def _tool(integration: InfraIntegration) -> StepTool:
    try:
        transport: str | None = integration.transport
    except IntegrationError:
        transport = None  # reported by --check-tools, as `deploy run`'s preflight reports it
    config = integration.config
    return StepTool(
        name=integration.name,
        type=integration.TYPE,
        transport=transport,
        command=integration.command if transport == "cli" else None,
        required_version=config.spec.version if config is not None else None,
    )


def _capabilities(integration: InfraIntegration) -> StepCapabilities:
    container = Capability.CONTAINER in integration.CAPABILITIES
    return StepCapabilities(
        saved_plan=container or integration.SUPPORTS_SAVED_PLAN,
        destroy=integration.supports_destroy,
        container=container,
    )


def _timeouts(stage: DeploymentStageModel | None) -> dict[str, int | None] | None:
    if stage is None or stage.timeouts is None:
        return None
    return {phase: getattr(stage.timeouts, phase) for phase in ("setup", "check", "plan", "apply", "destroy")}


def _readiness(
    assessed: IntegrationReadiness,
    step: ProvisioningStepModel,
    provisioner: str,
    *,
    destroy: bool,
    diagnostics: Diagnostics | None,
) -> StepReadiness:
    """`assessed` as a row and, with `diagnostics`, its findings at the step, worded as `deploy run`'s preflight."""
    label = f"Provisioner '{provisioner}' (step '{step.name}')"
    if diagnostics is not None:
        for problem in assessed.problems:
            # A teardown passes none of the flags a `flags_only` problem is about (docs/work/helm-4-support.md).
            report = diagnostics.warning if destroy and problem.flags_only else diagnostics.error
            report(f"{label}: {problem.message}", location=step.name, code=problem.code)
        for notice in assessed.warnings:
            diagnostics.warning(f"{label}: {notice.message}", location=step.name, code=notice.code)
    return StepReadiness(
        available=assessed.available,
        installed_version=assessed.installed_version,
        tool_path=assessed.tool_path,
        problems=[StepProblem(problem.code, problem.message) for problem in assessed.problems],
        warnings=[StepProblem(notice.code, notice.message) for notice in assessed.warnings],
    )
