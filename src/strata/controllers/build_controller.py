#!/usr/bin/env python3
"""`strata build run`'s orchestrator (ADR-0022) — renders a workspace's
provisioners into on-disk artifacts, never executes anything.

Three small, independent helpers plus the loop itself:

- `build_resolved_workspace_graph()` (ADR-0022 D1a's assembly step) — the
  only place `ResolvedWorkspaceGraph` gets built from a real
  `DocumentIndex`; every reference it walks is already existence-checked by
  Phase 1's `validate_references` (each field is `References(...)`-annotated),
  so lookups here are defensive, not primary validation.
- `ordered_by_depends_on()` — a Kahn's-algorithm topological sort, the same
  shape `provisioning_model.validate_provisioning_steps()` already uses to
  *detect* a cycle, but returning the order instead of discarding it.
  Assumes its input already passed that validator (acyclic, unique names,
  `depends_on` referencing real steps) — `WorkspaceSpecModel.validate_execution()`
  already guarantees this for `spec.execution`, so this function does not
  re-validate.
- `find_provisioner()` — `WorkspaceSpecModel.validate_execution()` already
  guarantees `step.provisioner` names a real entry in `spec.provisioners`;
  this is a lookup, not a check.

`build_run()` only calls `prepare()` — never `plan`/`deploy`/`destroy`
(ADR-0022 D4: build renders, deploy executes).
"""

from pathlib import Path
from typing import Any, cast

from strata.controllers.integration_resolution import resolve_integration
from strata.controllers.remote_resolution import resolve_remote  # noqa: F401  (re-exported for callers)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.source_sync import sync_source
from strata.controllers.value_controller import build_time_keys, resolve_deployment, resolve_values
from strata.controllers.workload_controller import build_workload_modules
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.common_models import PlatformKind
from strata.models.dns_model import DnsModel
from strata.models.firewall_model import FirewallModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.provider_model import ProviderModel
from strata.models.provisioning_model import ProvisionerModel, ProvisioningStepModel
from strata.models.resource_model import ResourceModel
from strata.models.solution_model import SolutionRemoteModel
from strata.models.topology_model import TopologyModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


def _lookup_all(index: DocumentIndex, kind: PlatformKind, names: list[str] | None) -> dict[str, Any]:
    """`{name: model}` for every name in `names` that resolves in `index`.

    Silently skips a name that does not resolve rather than raising — every
    caller here passes a `References(...)`-annotated field, already
    existence-checked by Phase 1's `validate_references`; this is a
    defensive fallback for an unvalidated context, not primary validation.
    """
    result: dict[str, Any] = {}
    for name in names or []:
        entry = index.get(kind, name)
        if entry is not None:
            result[name] = entry.model
    return result


def build_resolved_workspace_graph(index: DocumentIndex, workspace: WorkspaceModel) -> ResolvedWorkspaceGraph:
    """Assemble a `ResolvedWorkspaceGraph` by walking every name `workspace`
    references (ADR-0022 D1a)."""
    resource_names = [wr.resource for wr in (workspace.spec.resources or []) if wr.resource is not None]
    return ResolvedWorkspaceGraph(
        workspace=workspace,
        providers=cast(dict[str, ProviderModel], _lookup_all(index, PlatformKind.PROVIDER, workspace.spec.providers)),
        topologies=cast(dict[str, TopologyModel], _lookup_all(index, PlatformKind.TOPOLOGY, workspace.spec.topology)),
        resources=cast(dict[str, ResourceModel], _lookup_all(index, PlatformKind.RESOURCE, resource_names)),
        namespaces=cast(
            dict[str, NamespaceModel], _lookup_all(index, PlatformKind.NAMESPACE, workspace.spec.namespaces)
        ),
        firewalls=cast(
            dict[str, FirewallModel], _lookup_all(index, PlatformKind.FIREWALL, workspace.spec.firewalls)
        ),
        dns=cast(dict[str, DnsModel], _lookup_all(index, PlatformKind.DNS, workspace.spec.dns_zones)),
        networks=cast(dict[str, NetworkModel], _lookup_all(index, PlatformKind.NETWORK, workspace.spec.networks)),
    )


def ordered_by_depends_on(steps: list[ProvisioningStepModel]) -> list[ProvisioningStepModel]:
    """Return `steps` in an order where every `depends_on` runs first.

    Assumes `steps` already passed `provisioning_model.validate_provisioning_steps()`
    (acyclic, unique names, `depends_on` referencing real steps) — does not
    re-validate; a cycle here would hang rather than raise, which is why
    this is never called on unvalidated input (`WorkspaceSpecModel`'s own
    `validate_execution()` already guarantees it for `spec.execution`).
    """
    by_name = {step.name: step for step in steps}
    in_degree = {step.name: len(step.depends_on or []) for step in steps}
    queue = [step.name for step in steps if in_degree[step.name] == 0]
    ordered: list[ProvisioningStepModel] = []
    while queue:
        current = queue.pop(0)
        ordered.append(by_name[current])
        for step in steps:
            if current in (step.depends_on or []):
                in_degree[step.name] -= 1
                if in_degree[step.name] == 0:
                    queue.append(step.name)
    return ordered


def find_provisioner(workspace: WorkspaceModel, name: str) -> ProvisionerModel:
    """Return the `ProvisionerModel` named `name` in `workspace.spec.provisioners`.

    Raises:
        UsageError: `name` is not declared — should not happen,
            `WorkspaceSpecModel.validate_execution()` already guarantees
            every `ProvisioningStepModel.provisioner` names a real entry;
            this is a defensive backstop, not primary validation.
    """
    for provisioner in workspace.spec.provisioners:
        if provisioner.name == name:
            return provisioner
    raise UsageError(f"Provisioner '{name}' is not declared in workspace '{workspace.meta.name}'.")


def build_run(context: SolutionContext, deployment_name: str, build_path: Path) -> Diagnostics:
    """Render `deployment_name`'s workspace provisioners into `build_path`.

    Renders only — never calls `plan`/`deploy`/`destroy` (ADR-0022 D4).

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to build.
        build_path: Where rendered artifacts land.

    Returns:
        Diagnostics accumulated while resolving build-time values (a
        variable/feature not declared anywhere reachable, etc.) — not
        raised, since a partial build may still be useful to inspect.

    Raises:
        UsageError: `deployment_name` does not exist, its `workspace` is
            unset or does not resolve, or a provisioning step names an
            integration/provisioner that cannot be resolved.
    """
    index = context.controller.index

    keys = build_time_keys(context, deployment_name)
    resolved = resolve_values(context, deployment_name, keys)

    deployment = resolve_deployment(context, deployment_name)
    if deployment.spec.workspace is None:
        raise UsageError(f"Deployment '{deployment_name}' has no workspace to build.")
    workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
    if workspace_entry is None:
        raise UsageError(
            f"Deployment '{deployment_name}' names workspace '{deployment.spec.workspace}', "
            "which is not in the index."
        )
    workspace = cast(WorkspaceModel, workspace_entry.model)

    graph = build_resolved_workspace_graph(index, workspace)
    remotes: dict[str, SolutionRemoteModel] = {
        remote.name: remote for remote in (context.controller.solution.spec.remotes or [])
    } if context.controller.solution is not None else {}

    for step in ordered_by_depends_on(workspace.spec.execution or []):
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(index, provisioner)
        if provisioner.source is None:
            # Sync/GitOps provisioner (argocd/flux) — renders from the
            # resolved graph directly, nothing to materialise first.
            source_path = build_path / step.name
        else:
            source_path = sync_source(context.root, build_path, provisioner.source, remotes)
        try:
            integration.prepare(source_path, resolved=resolved, provisioner=provisioner, graph=graph)
        except IntegrationError as exc:
            # See workload_controller.build_workload_modules()'s identical
            # guard: IntegrationError is a plain Exception, not a
            # StrataError, and would otherwise escape command_run()'s
            # `except StrataError` as a raw traceback.
            raise UsageError(f"Provisioner '{provisioner.name}': {exc}") from exc

    # Workload pipeline (ADR-0022 D5-D7) — a second, disconnected input shape
    # (Namespace.spec.modules, never ProvisionerModel/ProvisioningStepModel),
    # so it is not part of the ordered provisioner loop above; every
    # namespace the workspace references is already resolved onto `graph`
    # (build_resolved_workspace_graph()), so no extra index walk is needed.
    for namespace in graph.namespaces.values():
        build_workload_modules(index, context.root, remotes, namespace, resolved, build_path)

    return resolved.diagnostics
