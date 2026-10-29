#!/usr/bin/env python3
"""`strata policy list | check | findings` (docs/design/security-gates.md D7).

A dispatch table, not a policy engine (policy-engine-architecture.md's "bespoke" decision, ADR-0049):
one row per workspace policy field, naming its phase, the function that evaluates it now and the
one that lists its findings. Every build-phase policy is judged against `--build-path`, recorded as
`build run` records it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from strata.controllers.build_controller import expected_source_paths, workspace_namespaces
from strata.controllers.checkov_controller import (
    CheckovFindingsReport,
    checkov_findings,
    declared_checkov,
    evaluate_checkov_policy,
)
from strata.controllers.policy_results import (
    append_policy_result,
    checkov_policy_result,
    cve_policy_result,
    read_policy_results,
)
from strata.controllers.sbom_controller import (
    CveFindingsReport,
    cve_findings,
    declared_cve_scanner,
    evaluate_cve_policy,
)
from strata.controllers.security_gate_controller import (
    Gate,
    ImageFindingsReport,
    evaluate_security_gates,
    list_image_findings,
)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.checkov_model import CheckovPolicyModel
from strata.models.common_models import PlatformKind
from strata.models.integration_model import IntegrationModel
from strata.models.sbom_model import CveMaxSeverityPolicyModel
from strata.models.security_gate_model import ImageGatePolicyModel, ReleaseGatePolicyModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError

#: Fallback threshold for `policy findings` when no `image_gate_policy` names one.
DEFAULT_FINDINGS_SEVERITY = "HIGH"

_Policy = CveMaxSeverityPolicyModel | CheckovPolicyModel | ReleaseGatePolicyModel | ImageGatePolicyModel


@dataclass(frozen=True)
class _CheckRequest:
    context: SolutionContext
    deployment_name: str
    workspace: WorkspaceModel
    build_path: Path
    version_pin: str | None
    commit_override: str | None
    on_step: Callable[[str], None] | None


_CheckFunction = Callable[[_CheckRequest], tuple[Diagnostics, list[ManifestPolicyResultModel]]]

PolicyFindingsReport = ImageFindingsReport | CveFindingsReport | CheckovFindingsReport


@dataclass(frozen=True)
class _FindingsRequest:
    context: SolutionContext
    deployment_name: str
    workspace: WorkspaceModel
    build_path: Path
    severity: str | None
    integration: str | None
    version_pin: str | None
    on_step: Callable[[str], None] | None


_FindingsFunction = Callable[[_FindingsRequest], PolicyFindingsReport]


def _cve_check(request: _CheckRequest) -> tuple[Diagnostics, list[ManifestPolicyResultModel]]:
    """`build run`'s own CVE gate against `--build-path`, recorded as `build run` records it (P8-1)."""
    diagnostics = evaluate_cve_policy(request.build_path, request.workspace, request.context.controller.index)
    result = cve_policy_result(request.workspace, diagnostics)
    return diagnostics, [result] if result is not None else []


def _cve_findings(request: _FindingsRequest) -> PolicyFindingsReport:
    _no_integration(request, "cve_policy uses the declared or PATH scanner")
    policy = request.workspace.spec.cve_policy
    threshold = request.severity or (
        (policy.severity_threshold or policy.max_severity) if policy is not None else DEFAULT_FINDINGS_SEVERITY
    )
    return cve_findings(request.build_path, request.context.controller.index, threshold)


def _checkov_check(request: _CheckRequest) -> tuple[Diagnostics, list[ManifestPolicyResultModel]]:
    """`build run`'s own Checkov gate over the re-derived source paths (P9-1, P9-3)."""
    index = request.context.controller.index
    diagnostics = evaluate_checkov_policy(
        request.build_path,
        request.workspace,
        expected_source_paths(request.build_path, request.workspace),
        namespaces=workspace_namespaces(index, request.workspace),
        index=index,
    )
    result = checkov_policy_result(request.workspace, diagnostics)
    return diagnostics, [result] if result is not None else []


def _checkov_findings(request: _FindingsRequest) -> PolicyFindingsReport:
    _no_integration(request, "checkov_policy uses the declared or PATH checkov")
    policy = request.workspace.spec.checkov_policy
    index = request.context.controller.index
    return checkov_findings(
        request.build_path,
        request.workspace,
        expected_source_paths(request.build_path, request.workspace),
        namespaces=workspace_namespaces(index, request.workspace),
        index=index,
        threshold=request.severity or (policy.severity_gate.upper() if policy is not None else "HIGH"),
    )


def _no_integration(request: _FindingsRequest, reason: str) -> None:
    if request.integration is not None:
        raise UsageError(f"--integration only applies to image_gate_policy; {reason}.")


def _image_findings(request: _FindingsRequest) -> PolicyFindingsReport:
    policy = request.workspace.spec.image_gate_policy
    integration_name = request.integration or (policy.integration if policy is not None else None)
    if integration_name is None:
        raise UsageError(
            f"Workspace '{request.workspace.meta.name}' configures no image_gate_policy; "
            "pass --integration NAME to name an image_findings integration."
        )
    threshold = request.severity or (policy.max_severity if policy is not None else DEFAULT_FINDINGS_SEVERITY)
    return list_image_findings(
        request.context,
        request.deployment_name,
        request.build_path,
        integration_name,
        threshold,
        version_pin=request.version_pin,
        on_step=request.on_step,
    )


def _gate_check(gate: Gate) -> _CheckFunction:
    def _check(request: _CheckRequest) -> tuple[Diagnostics, list[ManifestPolicyResultModel]]:
        evaluation = evaluate_security_gates(
            request.context,
            request.deployment_name,
            request.workspace,
            request.build_path,
            resolved=None,
            version_pin=request.version_pin,
            commit_override=request.commit_override,
            only=gate,
            on_step=request.on_step,
        )
        return evaluation.diagnostics, evaluation.policy_results(request.workspace)

    return _check


@dataclass(frozen=True)
class _PolicyRow:
    name: str
    phase: Literal["build", "deploy"]
    check: _CheckFunction | None
    findings: _FindingsFunction | None


#: Every workspace policy field. A new policy adds one row (design D7).
_POLICIES: tuple[_PolicyRow, ...] = (
    _PolicyRow("cve_policy", "build", check=_cve_check, findings=_cve_findings),
    _PolicyRow("checkov_policy", "build", check=_checkov_check, findings=_checkov_findings),
    _PolicyRow("release_gate_policy", "deploy", check=_gate_check("release"), findings=None),
    _PolicyRow("image_gate_policy", "deploy", check=_gate_check("images"), findings=_image_findings),
)
_ROWS = {row.name: row for row in _POLICIES}


@dataclass(frozen=True)
class PolicyInfo:
    """One configured policy, as `policy list` shows it."""

    name: str
    phase: str
    enforcement: str
    backing: str
    checkable: bool
    findings: bool
    last_result: ManifestPolicyResultModel | None


def list_policies(
    context: SolutionContext, deployment_name: str, build_path: Path, *, version_pin: str | None = None
) -> list[PolicyInfo]:
    """Every policy the deployment's workspace configures, with its last recorded result. No network."""
    _, workspace = resolve_deployment_workspace(context, deployment_name, version_pin=version_pin)
    recorded = {result.policy_name: result for result in (read_policy_results(build_path) or [])}
    infos = []
    for row, policy in _configured(workspace):
        infos.append(
            PolicyInfo(
                name=row.name,
                phase=row.phase,
                enforcement=policy.enforcement,
                backing=_backing(context, row.name, policy),
                checkable=row.check is not None,
                findings=row.findings is not None,
                last_result=recorded.get(row.name),
            )
        )
    return infos


def check_policies(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    *,
    policies: Sequence[str] = (),
    version_pin: str | None = None,
    commit_override: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> tuple[Diagnostics, list[str]]:
    """Evaluate the selected policies now, as the deploy would, and record the results.

    Default selection: every configured policy with a check function. Results are appended to
    `policy_results.json` when `build_path` exists, replacing the same policy's earlier entry.

    Returns:
        The combined diagnostics and the names of the policies evaluated.

    Raises:
        UsageError: A `--policy` that is unknown, not configured or not checkable yet; nothing to check.
    """
    _, workspace = resolve_deployment_workspace(context, deployment_name, version_pin=version_pin)
    configured = {row.name: row for row, _ in _configured(workspace)}
    if policies:
        selected = [_selectable(name, configured, workspace) for name in dict.fromkeys(policies)]
    else:
        selected = [row for row in configured.values() if row.check is not None]
        if not selected:
            checkable = ", ".join(row.name for row in _POLICIES if row.check is not None)
            raise UsageError(
                f"Nothing to check: workspace '{workspace.meta.name}' configures none of the policies "
                f"'strata policy check' evaluates ({checkable})."
            )

    request = _CheckRequest(context, deployment_name, workspace, build_path, version_pin, commit_override, on_step)
    diagnostics = Diagnostics()
    for row in selected:
        row_diagnostics, results = cast(_CheckFunction, row.check)(request)
        diagnostics.extend(row_diagnostics)
        if build_path.is_dir():
            for result in results:
                append_policy_result(build_path, result)
    return diagnostics, [row.name for row in selected]


def policy_findings(
    context: SolutionContext,
    deployment_name: str,
    build_path: Path,
    policy_name: str,
    *,
    severity: str | None = None,
    integration: str | None = None,
    version_pin: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> PolicyFindingsReport:
    """Itemised findings for `policy_name`, read-only (image gate per SBOM image; CVE per finding).

    `integration` names an `image_findings` document directly, so the image lookup works before the
    policy is configured. `severity` defaults to the policy's own threshold, else HIGH.

    Raises:
        UsageError: An unknown or unsupported policy, no integration to ask, or no `sbom.json`.
    """
    row = _ROWS.get(policy_name)
    if row is None:
        raise UsageError(f"Unknown policy '{policy_name}'. Known: {', '.join(_ROWS)}.")
    if row.findings is None:
        raise UsageError(
            f"'strata policy findings' does not support {policy_name} yet; "
            f"supported: {', '.join(r.name for r in _POLICIES if r.findings is not None)}."
        )
    _, workspace = resolve_deployment_workspace(context, deployment_name, version_pin=version_pin)
    request = _FindingsRequest(
        context, deployment_name, workspace, build_path, severity, integration, version_pin, on_step
    )
    return row.findings(request)


def _configured(workspace: WorkspaceModel) -> list[tuple[_PolicyRow, _Policy]]:
    found: list[tuple[_PolicyRow, _Policy]] = []
    for row in _POLICIES:
        policy = getattr(workspace.spec, row.name)
        if policy is not None:
            found.append((row, policy))
    return found


def _selectable(name: str, configured: dict[str, _PolicyRow], workspace: WorkspaceModel) -> _PolicyRow:
    row = _ROWS.get(name)
    if row is None:
        raise UsageError(f"Unknown policy '{name}'. Known: {', '.join(_ROWS)}.")
    if name not in configured:
        raise UsageError(f"Workspace '{workspace.meta.name}' does not configure {name}.")
    if row.check is None:
        raise UsageError(
            f"'strata policy check' does not evaluate {name} yet; it runs in the {row.phase} phase "
            f"('strata {row.phase} run')."
        )
    return row


def _backing(context: SolutionContext, name: str, policy: _Policy) -> str:
    """What evaluates the policy: a local tool, or `<type> '<integration>'` for the gates."""
    if name == "cve_policy":
        declared = declared_cve_scanner(context.controller.index)
        return f"{declared.TYPE} '{declared.name}'" if declared is not None else "trivy/grype (PATH)"
    if name == "checkov_policy":
        checkov = declared_checkov(context.controller.index)
        return f"checkov '{checkov.name}'" if checkov is not None else "checkov (PATH)"
    integration_name = cast(ReleaseGatePolicyModel | ImageGatePolicyModel, policy).integration
    entry = context.controller.index.get(PlatformKind.INTEGRATION, integration_name)
    integration_type = cast(IntegrationModel, entry.model).spec.type if entry is not None else "unknown"
    return f"{integration_type} '{integration_name}'"
