#!/usr/bin/env python3
"""Checkov policy gate (docs/work/checkov-integration.md) — gates `build
run` on static-IaC-security findings in the provisioners/Helm modules
`build_run()` just materialised, when `workspace.spec.checkov_policy` is
set.

Not folded into `sbom_controller.py` — no SBOM relationship exists here,
same reasoning as `checkov_model.py`'s own placement (docs/work/
checkov-integration.md Phase 1).

**`scope` is build-time-native, not v1's deployment-stage concept** — a
real, evidenced re-interpretation, not a straight port: `build_run()`'s own
step loop (confirmed by direct read) iterates every `workspace.spec.
execution` step unconditionally, regardless of `DeploymentModel.spec.
stages` (a deploy-time runtime-knobs concept — `DeploymentStageModel`
carries timeouts/health_checks/enabled, never consulted by `build run`).
v1's "staged" (reachable from at least one deployment stage) has no clean
equivalent at build time in v2. Re-scoped instead to a distinction that
*is* build-time-native:

- `terraform`/`bicep`/`ansible` (provisioner-based): `"staged"` (default)
  selects provisioners that have their own execution step (what
  `build run` actually renders as a primary step); `"all"` selects every
  declared provisioner of the matching framework, staged or
  dependency-only; a bare step name selects the one provisioner that step
  runs.
- `helm` (namespace-module-based, Phase 3): a workspace's namespaces have
  no equivalent "has its own execution step" distinction at all — a
  namespace is either referenced by the workspace or it isn't, there is
  no narrower "staged" subset the way a provisioner has one (confirmed by
  reading `workload_controller.py`'s own module docstring: the workload
  pipeline is "a second, disconnected input shape", never a
  `ProvisioningStepModel`). So for `helm`, `"staged"` and `"all"` are
  deliberately equivalent — both mean every namespace this workspace
  references — and a bare namespace name narrows to just that one
  namespace's modules.
"""

from collections.abc import Callable
from pathlib import Path

from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.workload_controller import resolve_module
from strata.integrations.checkov import CheckovIntegration
from strata.integrations.errors import IntegrationError
from strata.models.checkov_model import CheckovPolicyModel
from strata.models.namespace_model import NamespaceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.builtin_types import TERRAFORM_COMPATIBLE_TYPES, ProvisionerType
from strata.utils.diagnostics import Diagnostics

#: `checkov_policy.framework` -> the real `ProvisionerModel.tool` values it
#: scans. `helm` is deliberately absent — resolved via namespace modules
#: (`_evaluate_helm()` below), not a provisioner-tool lookup at all.
_FRAMEWORK_TOOLS: dict[str, frozenset[str]] = {
    "terraform": frozenset(t.value for t in TERRAFORM_COMPATIBLE_TYPES),
    "bicep": frozenset({ProvisionerType.BICEP.value}),
    "ansible": frozenset({ProvisionerType.ANSIBLE.value}),
}


def evaluate_checkov_policy(
    build_path: Path,
    workspace: WorkspaceModel,
    provisioner_source_paths: dict[str, Path | None],
    *,
    namespaces: dict[str, NamespaceModel] | None = None,
    index: DocumentIndex | None = None,
) -> Diagnostics:
    """Gate `build run` on Checkov findings for whatever
    `workspace.spec.checkov_policy.framework`/`scope` selects, when that
    policy is set.

    Args:
        build_path: The same build output root `build_run()` is writing
            to — used directly by the `helm` branch to locate each
            namespace/module's materialised directory
            (`build_path/<namespace>/<module reference>`, the same
            convention `workload_controller.build_workload_modules()`
            establishes); the `terraform`/`bicep`/`ansible` branch instead
            reads provisioner paths from `provisioner_source_paths`.
        workspace: The deployment's resolved workspace — the sole owner of
            `spec.checkov_policy`.
        provisioner_source_paths: The real `materialised` dict
            `build_run()`'s own step loop builds (docs/work/
            checkov-integration.md Phase 1) — provisioner name -> the
            directory its source was synced to, for every provisioner
            reached this build, however it was reached. `None` for a
            sync/GitOps dependency provisioner with no `source` of its
            own — treated identically to "not present at all" below,
            `dict.get()` already returns `None` for both. Unused by the
            `helm` branch.
        namespaces: The deployment's resolved namespaces
            (`ResolvedWorkspaceGraph.namespaces`), required only for
            `framework: helm` — `None` (the default) degrades through
            `on_missing_data` for that framework only, same as any other
            "nothing to evaluate" condition.
        index: The loaded `DocumentIndex`, required only for
            `framework: helm` (to resolve each `ModuleReferenceModel` into
            a real `ModuleModel`, matching `workload_controller.
            resolve_module()`'s own established pattern) — `None` degrades
            the same way as `namespaces` above.

    Returns:
        One `Severity.ERROR`/`Severity.WARNING` finding per breaching or
        unscannable provisioner/module — never raises.
    """
    diagnostics = Diagnostics()
    policy = workspace.spec.checkov_policy
    if policy is None:
        return diagnostics

    severity = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning

    if policy.framework == "helm":
        _evaluate_helm(diagnostics, policy, build_path, namespaces, index, severity)
        return diagnostics

    tools = _FRAMEWORK_TOOLS[policy.framework]
    provisioners = _provisioners_for_scope(workspace, tools, policy.scope)
    if not provisioners:
        _report_missing_data(
            diagnostics,
            policy.on_missing_data,
            f"no '{policy.framework}' provisioners found for scope '{policy.scope}'",
        )
        return diagnostics

    scanner = CheckovIntegration()
    for provisioner in provisioners:
        source_path = provisioner_source_paths.get(provisioner.name)
        if source_path is None:
            _report_missing_data(
                diagnostics,
                policy.on_missing_data,
                "not materialised during this build",
                scope=f"provisioner '{provisioner.name}'",
            )
            continue

        try:
            result = scanner.scan(
                source_path,
                framework=policy.framework,
                skip_checks=policy.skip_checks,
                include_checks=policy.include_checks,
                custom_checks_dir=policy.custom_checks_dir,
                timeout=policy.timeout,
            )
        except IntegrationError as exc:
            _report_missing_data(
                diagnostics, policy.on_missing_data, str(exc), scope=f"provisioner '{provisioner.name}'"
            )
            continue

        breaching = result.findings_at_or_above(policy.severity_gate.upper())
        if breaching:
            severity(
                f"checkov_policy: provisioner '{provisioner.name}' has {len(breaching)} finding(s) at or above "
                f"severity '{policy.severity_gate}' — scanned with checkov {result.scanner_version}.",
                code="checkov_policy_violation",
            )
    return diagnostics


def _provisioners_for_scope(workspace: WorkspaceModel, tools: frozenset[str], scope: str) -> list[ProvisionerModel]:
    """Resolve `scope` (`"staged" | "all" | <step-name>`) against
    `workspace`'s own provisioners/execution steps — see this module's own
    docstring for why this is build-time-native rather than a port of v1's
    deployment-stage concept.
    """
    matching = [p for p in workspace.spec.provisioners if p.tool in tools]
    if scope == "all":
        return matching
    staged_names = {step.provisioner for step in (workspace.spec.execution or [])}
    if scope == "staged":
        return [p for p in matching if p.name in staged_names]
    step = next((s for s in (workspace.spec.execution or []) if s.name == scope), None)
    if step is None:
        return []
    return [p for p in matching if p.name == step.provisioner]


def _evaluate_helm(
    diagnostics: Diagnostics,
    policy: CheckovPolicyModel,
    build_path: Path,
    namespaces: dict[str, NamespaceModel] | None,
    index: DocumentIndex | None,
    severity: Callable[..., None],
) -> None:
    """Helm's own resolution path (Phase 3) — namespace/module enumeration
    instead of a provisioner-tool lookup, since a Helm workload is reached
    via `Namespace.spec.modules`, never `ProvisionerModel` (see this
    module's own docstring).
    """
    if namespaces is None or index is None:
        _report_missing_data(diagnostics, policy.on_missing_data, "no namespace data available to resolve Helm modules")
        return

    targets = _namespaces_for_scope(namespaces, policy.scope)
    if not targets:
        _report_missing_data(diagnostics, policy.on_missing_data, f"no namespaces found for scope '{policy.scope}'")
        return

    scanner = CheckovIntegration()
    modules_reached = 0
    for namespace in targets:
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            module = resolve_module(index, reference)
            if module.spec.type != "helm":
                continue
            modules_reached += 1

            label = f"namespace '{namespace.meta.name}' module '{reference.name}'"

            if module.spec.source.chart_name is not None:
                # Registry-pulled chart — only `values.yaml`/`meta.yaml` are
                # ever written for it (source_sync.sync_module_source()'s
                # own documented behavior), never the chart's own templates
                # — matches v1's real "no local source, skip with an
                # explicit warning" behavior.
                _report_missing_data(
                    diagnostics,
                    policy.on_missing_data,
                    "registry-pulled chart has no local source to scan",
                    scope=label,
                )
                continue

            module_dir = build_path / namespace.meta.name / reference.name
            if not module_dir.is_dir():
                _report_missing_data(
                    diagnostics, policy.on_missing_data, "not materialised during this build", scope=label
                )
                continue

            try:
                result = scanner.scan(
                    module_dir,
                    framework="helm",
                    skip_checks=policy.skip_checks,
                    include_checks=policy.include_checks,
                    custom_checks_dir=policy.custom_checks_dir,
                    timeout=policy.timeout,
                )
            except IntegrationError as exc:
                _report_missing_data(diagnostics, policy.on_missing_data, str(exc), scope=label)
                continue

            breaching = result.findings_at_or_above(policy.severity_gate.upper())
            if breaching:
                severity(
                    f"checkov_policy: {label} has {len(breaching)} finding(s) at or above severity "
                    f"'{policy.severity_gate}' — scanned with checkov {result.scanner_version}.",
                    code="checkov_policy_violation",
                )

    if modules_reached == 0:
        # Every reachable module in scope was either disabled or not
        # helm-typed — the "nothing to evaluate" case the provisioner-based
        # path already guards (`if not provisioners:` above) was otherwise
        # silently unreachable here: the only not-empty check before this
        # loop is on *namespaces*, not on whether any of them actually had
        # a helm module to scan. Left unguarded, a typo'd `type:` or an
        # all-disabled namespace would produce zero diagnostics at all —
        # exactly the "never scanned looks the same as scanned clean" risk
        # `on_missing_data` exists to prevent (this doc's own "v2 design
        # decision" section).
        _report_missing_data(
            diagnostics,
            policy.on_missing_data,
            f"no helm modules found across {len(targets)} namespace(s) for scope '{policy.scope}'",
        )


def _namespaces_for_scope(namespaces: dict[str, NamespaceModel], scope: str) -> list[NamespaceModel]:
    """Resolve `scope` for `framework: helm` — `"staged"`/`"all"` are
    deliberately equivalent here (see this module's own docstring for
    why); a bare namespace name narrows to just that one namespace.
    """
    if scope in ("staged", "all"):
        return list(namespaces.values())
    namespace = namespaces.get(scope)
    return [namespace] if namespace is not None else []


def _report_missing_data(
    diagnostics: Diagnostics, on_missing_data: str, reason: str, *, scope: str | None = None
) -> None:
    """Shared by every "nothing to evaluate" branch above — matches
    `sbom_controller._report_missing_data()`'s own established shape and
    `on_missing_data` vocabulary/default rationale (`CheckovPolicyModel`'s
    own docstring). Scoped to one provisioner/namespace-module when given
    (per-unit degradation, the "v2 design decision" Part 1 above), or to
    the whole policy when not (e.g. an unsupported framework, or zero
    provisioners/namespaces matching `scope` at all).
    """
    scope_label = scope if scope is not None else "policy"
    message = f"checkov_policy: cannot evaluate {scope_label} — {reason}."
    if on_missing_data == "block":
        diagnostics.error(message, code="checkov_policy_missing_data")
    elif on_missing_data == "warn":
        diagnostics.warning(message, code="checkov_policy_missing_data")
