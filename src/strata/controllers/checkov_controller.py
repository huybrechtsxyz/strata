#!/usr/bin/env python3
"""Checkov policy gate (docs/archive/checkov-integration.md) — gates `build
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

from dataclasses import dataclass
from pathlib import Path

from strata.controllers.integration_resolution import bind_integration_config
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.workload_controller import resolve_module
from strata.integrations.checkov import CheckovIntegration
from strata.integrations.errors import IntegrationError
from strata.models.checkov_model import (
    CHECKOV_SEVERITY_ORDER,
    CheckovFindingModel,
    CheckovPolicyModel,
    CheckovScanResultModel,
)
from strata.models.namespace_model import NamespaceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.builtin_types import TERRAFORM_COMPATIBLE_TYPES, ProvisionerType
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import SystemError, UsageError

#: `checkov_policy.framework` -> the real `ProvisionerModel.tool` values it
#: scans. `helm` is deliberately absent — resolved via namespace modules
#: (`_evaluate_helm()` below), not a provisioner-tool lookup at all.
_FRAMEWORK_TOOLS: dict[str, frozenset[str]] = {
    "terraform": frozenset(t.value for t in TERRAFORM_COMPATIBLE_TYPES),
    "bicep": frozenset({ProvisionerType.BICEP.value}),
    "ansible": frozenset({ProvisionerType.ANSIBLE.value}),
}


def declared_checkov(index: DocumentIndex | None) -> CheckovIntegration | None:
    """The enabled `checkov` document's scanner, else `None` (docs/work/integration-readiness.md D26)."""
    config = bind_integration_config(index, "checkov", requester="checkov_policy") if index is not None else None
    return CheckovIntegration(config) if config is not None else None


def _checkov(index: DocumentIndex | None) -> CheckovIntegration:
    return declared_checkov(index) or CheckovIntegration()


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
    targets, nothing = _targets(policy, workspace, provisioner_source_paths, build_path, namespaces, index)
    if nothing is not None:
        _report_missing_data(diagnostics, policy.on_missing_data, nothing)
        return diagnostics

    scanner = _checkov(index)
    for target in targets:
        if target.path is None:
            _report_missing_data(diagnostics, policy.on_missing_data, target.missing or "", scope=target.label)
            continue
        try:
            result = _scan(scanner, policy, target.path)
        except IntegrationError as exc:
            _report_missing_data(diagnostics, policy.on_missing_data, str(exc), scope=target.label)
            continue

        rated = result.findings_at_or_above(policy.severity_gate.upper())
        unrated = [finding for finding in result.findings if _is_unrated(finding)]
        if rated or unrated:
            note = f", {len(unrated)} of them unrated" if unrated else ""
            severity(
                f"checkov_policy: {target.label} has {len(rated) + len(unrated)} finding(s) at or above "
                f"severity '{policy.severity_gate}'{note} \u2014 scanned with checkov {result.scanner_version}.",
                code="checkov_policy_violation",
            )
    return diagnostics


def _is_unrated(finding: CheckovFindingModel) -> bool:
    """No usable severity (open-source Checkov reports `null`): counted at any `severity_gate` (plan P9b)."""
    return finding.severity not in _RATED


_RATED = CHECKOV_SEVERITY_ORDER[:-1]


@dataclass(frozen=True)
class CheckovFinding:
    finding: CheckovFindingModel
    #: No severity (open-source Checkov without an API key); always listed and counted (plan P9b).
    unrated: bool


@dataclass(frozen=True)
class CheckovTargetFindings:
    """One provisioner or Helm module; `missing` says why it was not scanned."""

    label: str
    path: Path | None
    missing: str | None
    findings: tuple[CheckovFinding, ...]


@dataclass(frozen=True)
class CheckovFindingsReport:
    """`strata policy findings --policy checkov_policy` (security-gates plan Phase 9, P9-5)."""

    scanner_version: str | None
    framework: str
    severities: tuple[str, ...]
    targets: tuple[CheckovTargetFindings, ...]
    #: Why nothing is in scope at all, else `None`.
    nothing: str | None


def checkov_findings(
    build_path: Path,
    workspace: WorkspaceModel,
    provisioner_source_paths: dict[str, Path | None],
    *,
    namespaces: dict[str, NamespaceModel] | None,
    index: DocumentIndex,
    threshold: str,
) -> CheckovFindingsReport:
    """Failed checks per target at or above `threshold`, read-only; unrated ones always listed and marked.

    Targets are the ones `evaluate_checkov_policy()` scans; without a policy, the policy defaults apply.

    Raises:
        UsageError: `build_path` does not exist.
        SystemError: Checkov is not available, or a scan failed.
    """
    if not build_path.is_dir():
        raise UsageError(f"No build output at '{build_path}'; run 'strata build run' first.")
    policy = workspace.spec.checkov_policy or CheckovPolicyModel.model_validate({})
    targets, nothing = _targets(policy, workspace, provisioner_source_paths, build_path, namespaces, index)
    rated = CHECKOV_SEVERITY_ORDER[:-1]
    severities = tuple(CHECKOV_SEVERITY_ORDER[: CHECKOV_SEVERITY_ORDER.index(threshold) + 1])
    scanner = _checkov(index)
    version: str | None = None
    rows: list[CheckovTargetFindings] = []
    for target in targets:
        if target.path is None:
            rows.append(CheckovTargetFindings(target.label, None, target.missing, ()))
            continue
        try:
            result = _scan(scanner, policy, target.path)
        except IntegrationError as exc:
            raise SystemError(f"checkov_policy: {target.label}: {exc}") from None
        version = result.scanner_version
        findings = tuple(
            CheckovFinding(finding, unrated=_is_unrated(finding))
            for finding in result.findings
            if _is_unrated(finding) or finding.severity in severities
        )
        rows.append(CheckovTargetFindings(target.label, target.path, None, findings))
    return CheckovFindingsReport(version, policy.framework, severities, tuple(rows), nothing)


_NOT_MATERIALISED = "not materialised during this build"


@dataclass(frozen=True)
class _Target:
    label: str
    path: Path | None
    missing: str | None = None


def _scan(scanner: CheckovIntegration, policy: CheckovPolicyModel, path: Path) -> CheckovScanResultModel:
    return scanner.scan(
        path,
        framework=policy.framework,
        skip_checks=policy.skip_checks,
        include_checks=policy.include_checks,
        custom_checks_dir=policy.custom_checks_dir,
        timeout=policy.timeout,
    )


def _targets(
    policy: CheckovPolicyModel,
    workspace: WorkspaceModel,
    provisioner_source_paths: dict[str, Path | None],
    build_path: Path,
    namespaces: dict[str, NamespaceModel] | None,
    index: DocumentIndex | None,
) -> tuple[list[_Target], str | None]:
    """What `policy` scans, in order, or why nothing is in scope; shared by the gate and the listing."""
    if policy.framework == "helm":
        return _helm_targets(policy, build_path, namespaces, index)
    provisioners = _provisioners_for_scope(workspace, _FRAMEWORK_TOOLS[policy.framework], policy.scope)
    if not provisioners:
        return [], f"no '{policy.framework}' provisioners found for scope '{policy.scope}'"
    targets = []
    for provisioner in provisioners:
        # `None` covers both "never reached" and a source-less dependency.
        source_path = provisioner_source_paths.get(provisioner.name)
        missing = None if source_path is not None else _NOT_MATERIALISED
        targets.append(_Target(f"provisioner '{provisioner.name}'", source_path, missing))
    return targets, None


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


def _helm_targets(
    policy: CheckovPolicyModel,
    build_path: Path,
    namespaces: dict[str, NamespaceModel] | None,
    index: DocumentIndex | None,
) -> tuple[list[_Target], str | None]:
    """Helm's own resolution path (Phase 3): namespace modules, never a provisioner (module docstring)."""
    if namespaces is None or index is None:
        return [], "no namespace data available to resolve Helm modules"
    selected = _namespaces_for_scope(namespaces, policy.scope)
    if not selected:
        return [], f"no namespaces found for scope '{policy.scope}'"

    targets: list[_Target] = []
    for namespace in selected:
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            module = resolve_module(index, reference)
            if module.spec.type != "helm":
                continue
            label = f"namespace '{namespace.meta.name}' module '{reference.name}'"
            if module.spec.source.chart_name is not None:
                # Only values.yaml/meta.yaml are written for a registry chart, never its templates (as v1).
                targets.append(_Target(label, None, "registry-pulled chart has no local source to scan"))
                continue
            module_dir = build_path / namespace.meta.name / reference.name
            targets.append(
                _Target(label, module_dir) if module_dir.is_dir() else _Target(label, None, _NOT_MATERIALISED)
            )
    if not targets:
        # Every module disabled or not helm-typed: never let "never scanned" look like "scanned clean".
        return [], f"no helm modules found across {len(selected)} namespace(s) for scope '{policy.scope}'"
    return targets, None


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
