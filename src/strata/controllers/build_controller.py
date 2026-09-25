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

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from strata.controllers.integration_resolution import resolve_integration
from strata.controllers.remote_resolution import resolve_remote  # noqa: F401  (re-exported for callers)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.source_sync import describe_source, sync_source
from strata.controllers.value_controller import (
    build_value_references,
    merge_workspace_environment_deployment_properties,
    reachable_environments,
    resolve_deployment,
    resolve_values,
)
from strata.controllers.workload_controller import build_workload_modules
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueReference, ValueResolution
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
from strata.services.environment_service import merge_environment_models
from strata.utils.diagnostics import Diagnostics
from strata.utils.env_file import load_env_file
from strata.utils.errors import SystemError, UsageError


class BuildCleanError(SystemError):
    """`build_path` could not be wiped before rendering (`clean=True`)."""


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


def build_resolved_workspace_graph(
    index: DocumentIndex,
    workspace: WorkspaceModel,
    *,
    variable_refs: list[ValueReference] | None = None,
    feature_refs: list[ValueReference] | None = None,
    secret_refs: list[ValueReference] | None = None,
    properties: dict[str, Any] | None = None,
    custom: dict[str, Any] | None = None,
) -> ResolvedWorkspaceGraph:
    """Assemble a `ResolvedWorkspaceGraph` by walking every name `workspace`
    references (ADR-0022 D1a).

    `variable_refs`/`feature_refs`/`secret_refs`/`properties`/`custom`
    (docs/design/build-time-value-categories.md, Q1/Q3/Q4) are optional —
    callers with no deployment in scope yet (none exist today; kept optional
    for exactly that reason) get the empty defaults `ResolvedWorkspaceGraph`
    itself already provides.
    """
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
        variable_refs=variable_refs or [],
        feature_refs=feature_refs or [],
        secret_refs=secret_refs or [],
        properties=properties or {},
        custom=custom or {},
    )


def _dump_refs(refs: list[ValueReference]) -> dict[str, dict[str, Any]]:
    """One `resolved.yaml` section (docs/design/build-time-value-categories.md,
    Q8) — `key -> {store, description?, value?}`. `value` is omitted
    entirely (not even `null`) unless it was actually populated by
    `build_value_references()` (`constant`/`environment` stores only) —
    keeps a secret's entry visibly value-less rather than `value: null`,
    which could misread as "resolved to nothing".
    """
    dumped: dict[str, dict[str, Any]] = {}
    for ref in refs:
        entry: dict[str, Any] = {"store": ref.store}
        if ref.description is not None:
            entry["description"] = ref.description
        if ref.value is not None:
            entry["value"] = ref.value
        dumped[ref.key] = entry
    return dumped


def write_resolved_manifest(build_path: Path, graph: ResolvedWorkspaceGraph) -> Path:
    """Write `build_path/resolved.yaml` (docs/design/build-time-value-categories.md,
    Q8) — plain YAML, deliberately not `*.auto.tfvars.json`: Terraform never
    auto-loads it, so no `.tf` module needs a matching `variable {}` block
    just to accommodate strata's own bookkeeping output.

    Safe to write in full: every value in `graph.variable_refs`/`.feature_refs`
    is either a declaration (no `value` key at all) or a `constant`/
    `environment`-store value already written to a real `.auto.tfvars.json`
    file elsewhere in this same build. `graph.secret_refs` never carries a
    `value` at all — enforced by `ValueReference`'s own construction in
    `build_value_references()`, not filtered out here.
    """
    manifest = {
        "variables": _dump_refs(graph.variable_refs),
        "features": _dump_refs(graph.feature_refs),
        "secrets": _dump_refs(graph.secret_refs),
        "properties": graph.properties,
        "custom": graph.custom,
    }
    build_path.mkdir(parents=True, exist_ok=True)
    path = build_path / "resolved.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    return path


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


def build_run(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    clean: bool = True,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
    resolve: bool = False,
    env_files: list[Path] | None = None,
) -> Diagnostics:
    """Render `deployment_name`'s workspace provisioners into `build_path`.

    Renders only — never calls `plan`/`deploy`/`destroy` (ADR-0022 D4).

    Args:
        context: An already-`require_valid()`-ed solution.
        deployment_name: `meta.name` of the deployment to build.
        build_path: Where rendered artifacts land.
        clean: Wipe `build_path` before rendering (default `True`, matching
            v1's `PlatformBuilder`). A build only ever adds/overwrites
            files otherwise (`sync_source()`'s `copytree(dirs_exist_ok=True)`,
            each `.auto.tfvars.json` written per-category) — a document
            removed from the solution (the last `dns_zones` entry, say)
            would leave its old, still-auto-loaded output behind with no
            error. Callers pointing `build_path` somewhere they don't fully
            own (e.g. a user-supplied `--build-path`) should pass `False`
            explicitly — the safe default assumes `build_path` is
            exclusively this build's own directory
            (`layout.build_dir()`'s own convention).
        dry_run: Report what would happen instead of doing it — skips the
            `clean` wipe, materialising any source, writing `resolved.yaml`,
            and every render. Deliberately not a second, parallel code path:
            every step below still runs (deployment/workspace resolution,
            value-reference derivation, integration resolution, `--resolve`'s
            validation), so a dry run still catches a bad deployment name,
            an unresolvable integration, or (with `resolve=True`) a value
            that fails to resolve — only filesystem mutation is skipped.
        on_step: Called with a one-line progress message at each meaningful
            point (clean, per-provisioner materialise/render, per-namespace
            workload materialise/render) — the same call sites `dry_run`
            reports through, just describing real work instead of planned
            work. Optional: a caller that doesn't want streaming progress
            (e.g. a test) can omit it.
        resolve: Opt-in full value validation (docs/design/
            build-time-value-categories.md, Q7) — when `True`, additionally
            resolves **every** declared variable/feature/secret (not just
            the `constant`/`environment`-backed ones `build run` writes to
            disk), including real network calls to integration-backed
            stores. Findings are merged into the returned `Diagnostics`;
            the resolved values themselves are never written anywhere,
            with or without this flag. Default `False`: no network call
            happens beyond `sync_source()`'s own.
        env_files: `.env`-style files (docs/design/
            build-time-value-categories.md, Q9) merged into the real
            process `os.environ` via `setdefault` before anything else runs
            — a real, already-exported env var always wins over a file's
            value. Covers `environment`-store variables/features a local
            dev machine's shell doesn't already have, the way a CI
            pipeline's own exported env vars would.

    Returns:
        Diagnostics accumulated while deriving build-time values and,
        if `resolve=True`, validating every declared value — not raised,
        since a partial build may still be useful to inspect.

    Raises:
        UsageError: `deployment_name` does not exist, its `workspace` is
            unset or does not resolve, or a provisioning step names an
            integration/provisioner that cannot be resolved.
        BuildCleanError: `clean` is `True`, `dry_run` is `False`, and
            `build_path` could not be removed (permissions, a file in use).
    """

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    for env_file in env_files or []:
        for key, value in load_env_file(env_file).items():
            os.environ.setdefault(key, value)

    index = context.controller.index

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

    environments = reachable_environments(context, deployment)
    variable_refs, feature_refs, secret_refs = build_value_references(environments)
    properties = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "properties")
    custom = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "custom")

    diagnostics = Diagnostics()
    resolved = ValueResolution(deployment=deployment_name)
    if resolve:
        variables, secrets, features = merge_environment_models(environments)
        all_keys = sorted({**variables, **secrets, **features})
        validation = resolve_values(context, deployment_name, all_keys)
        diagnostics.extend(validation.diagnostics)

    if clean and build_path.exists():
        if dry_run:
            _step(f"would clean {build_path}")
        else:
            try:
                shutil.rmtree(build_path)
            except OSError as exc:
                raise BuildCleanError(f"Could not clean build_path '{build_path}': {exc}") from exc
            _step(f"cleaned {build_path}")

    graph = build_resolved_workspace_graph(
        index,
        workspace,
        variable_refs=variable_refs,
        feature_refs=feature_refs,
        secret_refs=secret_refs,
        properties=properties,
        custom=custom,
    )
    if dry_run:
        _step(f"would write {build_path / 'resolved.yaml'}")
    else:
        manifest_path = write_resolved_manifest(build_path, graph)
        _step(f"wrote {manifest_path}")
    remotes: dict[str, SolutionRemoteModel] = {
        remote.name: remote for remote in (context.controller.solution.spec.remotes or [])
    } if context.controller.solution is not None else {}

    for step in ordered_by_depends_on(workspace.spec.execution or []):
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(index, provisioner)
        integration_type = type(integration).__name__

        if dry_run:
            if provisioner.source is None:
                _step(f"provisioner '{step.name}': no source to materialise (sync/GitOps)")
            else:
                _step(f"would materialise provisioner '{step.name}' source ({describe_source(provisioner.source)})")
            _step(f"would render provisioner '{step.name}' via {integration_type}")
            continue

        if provisioner.source is None:
            # Sync/GitOps provisioner (argocd/flux) — renders from the
            # resolved graph directly, nothing to materialise first.
            source_path = build_path / step.name
        else:
            source_path = sync_source(context.root, build_path, provisioner.source, remotes)
            _step(f"materialised provisioner '{step.name}' source at {source_path}")
        try:
            integration.prepare(source_path, resolved=resolved, provisioner=provisioner, graph=graph)
        except IntegrationError as exc:
            # See workload_controller.build_workload_modules()'s identical
            # guard: IntegrationError is a plain Exception, not a
            # StrataError, and would otherwise escape command_run()'s
            # `except StrataError` as a raw traceback.
            raise UsageError(f"Provisioner '{provisioner.name}': {exc}") from exc
        _step(f"rendered provisioner '{step.name}' via {integration_type}")

    # Workload pipeline (ADR-0022 D5-D7) — a second, disconnected input shape
    # (Namespace.spec.modules, never ProvisionerModel/ProvisioningStepModel),
    # so it is not part of the ordered provisioner loop above; every
    # namespace the workspace references is already resolved onto `graph`
    # (build_resolved_workspace_graph()), so no extra index walk is needed.
    for namespace in graph.namespaces.values():
        build_workload_modules(
            index, context.root, remotes, namespace, resolved, build_path, dry_run=dry_run, on_step=on_step
        )

    return diagnostics
