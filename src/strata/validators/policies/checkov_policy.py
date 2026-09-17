#!/usr/bin/env python3
"""Built-in policy: Checkov IaC security scanning.

Evaluates at the ``build`` phase.  Runs Checkov against the IaC artifacts
produced by ``strata build run`` for one provisioner type (``configuration.framework``)
and fails when findings at or above ``severity_gate`` are detected, for any
scanned provisioner.

Context resolution (ADR-0051 revision, 2026-09-16)
---------------------------------------------------
The artifact directory for each scanned provisioner is resolved via
``SolutionController.get_provisioner_path()`` — the same single source of
truth the matching builder (copy destination) and deployer (working
directory) already use, honouring each provisioner's ``source.target_path`` /
``source.source_path``. Previously this policy guessed at flat candidate
directories under ``context.build_path`` that never consulted a provisioner's
``source_path`` — silently skipping (and reporting ``passed=True``) for any
workspace whose terraform provisioner used a nested ``source_path``. See
ADR-0051's 2026-09-16 revision for the full writeup.

Supported frameworks (ADR-0051, 2026-09-16 multi-provisioner follow-up; helm added 2026-09-17)
------------------------------------------------------------------------------------------------
``configuration.framework`` selects both the Checkov framework AND, via
``_FRAMEWORK_PROVISIONER_MAP``, which strata provisioner type is scanned:

- ``terraform`` (default) — ``provisioner: terraform`` entries, ``*.tf`` files.
- ``bicep`` — ``provisioner: bicep`` entries, ``*.bicep`` files. Fits the same
  flat ``get_provisioner_path()`` shape as terraform (confirmed: ``bicep_builder.py``
  copies source there too).
- ``ansible`` — ``provisioner: ansible`` entries, ``*.yml``/``*.yaml`` files. Also
  fits the same flat shape (confirmed: ``ansible_builder.py`` copies source there too).
- ``helm`` — resolved differently from the three above, since Helm has no single
  directory per provisioner (build output is organized per namespace+module via
  ``get_module_build_path()``). Resolution: ``scope`` selects target namespace(s) via
  ``helm_namespaces_for_stage()`` (a stage's ``helm_namespaces`` allowlist, or every
  namespace when unset/scope is ``all``); each namespace's modules are enumerated via
  the already-loaded ``NamespaceService`` (same source ``HelmBuilder`` uses) and
  filtered to ``spec.type == helm``. Only **local** charts (no ``chart_repository``)
  have chart source on disk to scan — a registry-pulled chart's module is skipped with
  an explicit warning, never silently, since only ``values.yaml``/``meta.yaml`` exist
  for those at build time, never a ``Chart.yaml``. Findings are reported per
  ``namespace/module`` instead of per provisioner name.

``compose``/``argocd``/``flux``/``script`` remain unsupported: ``compose`` has no
Checkov framework at all; ``argocd``/``flux`` render from the platform artifact with
no stable build-time source directory; ``script`` is not IaC. An unrecognized
``framework`` skips gracefully with a message listing the supported values.

One policy instance scans one framework — ``skip_checks``/finding IDs live in
unrelated namespaces per framework (``CKV_AWS_*`` vs ``CKV_ANSIBLE_*`` vs Bicep/ARM
checks), so a workspace wanting coverage across multiple frameworks declares one
``checkov`` policy per framework.

``configuration.scope`` controls which provisioner(s) (or, for ``helm``, namespaces)
of the selected framework are scanned when a workspace declares more than one:

- ``staged`` (default) — provisioners reachable from at least one deployment
  stage (``stage.provisioner`` or ``stage.topology`` → ``topology.provisioner``).
  A shared module-library provisioner that no stage targets directly is
  excluded by construction — no extra metadata needed. For ``helm``: namespaces
  reachable via a staged helm provisioner's ``helm_namespaces`` (or every
  namespace, when that stage doesn't set it).
- ``all`` — every provisioner of the selected framework in the workspace,
  staged or not (for ``helm``: every namespace in the workspace).
- ``<stage-name>`` — provisioner(s)/namespace(s) reachable from that one named
  stage only. This is a static filter over the same stage list ``staged`` uses,
  not a runtime binding to "when that stage deploys" — ``build`` evaluates
  once, regardless of deploy stages.

Note: ``configuration.scope`` is unrelated to the existing, different,
free-form ``stages[].scope`` CLI-filter label — same field name, different
namespace.

When more than one provisioner (or namespace/module, for helm) is scanned, a breach
in any one of them denies the whole policy result (AND semantics) — findings are
still reported individually in ``PolicyResult.details["provisioners"]`` and
violation strings are prefixed with the provisioner name or ``namespace/module``
label (e.g. ``[control_infra] CKV_AWS_1: ...`` or ``[prod/nginx] CKV_K8S_1: ...``).

Graceful degradation
--------------------
Every skip condition below also appends an explicit, actionable message to
``PolicyResult.warnings`` (surfaced by every ``PolicyContext`` call site as a
``⚠`` line, even when ``passed=True``) — a silent pass is never silent again:

- ``severity_gate``/``scope``/``framework`` not valid → pass (skip)
- No IaC artifacts found for the selected scope → pass (skip)
- Checkov not installed, for a given provisioner → pass (skip that provisioner)
- Scan subprocess fails, for a given provisioner → pass (skip that provisioner)

Example configuration YAML::

    policies:
      - name: terraform_security_baseline
        type: checkov
        phase: build
        enforcement: deny
        description: "Block builds with HIGH or CRITICAL Checkov findings"
        configuration:
          framework: terraform          # default: terraform | bicep | ansible
          severity_gate: high           # critical|high|medium|low (default: high)
          scope: staged                 # staged (default) | all | <stage-name>
          skip_checks:                  # CKV IDs to suppress
            - CKV_AWS_1
            - CKV_AWS_20
          include_checks: []            # if set, run ONLY these checks
          custom_checks_dir: ".strata/checkov/custom/"  # optional
          timeout: 120                  # seconds, default 120
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.logger import get_logger
from strata.models.common_models import ProvisionerType
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]

# Framework -> (strata provisioner type, glob patterns used to confirm artifacts
# are actually present on disk before scanning). "helm" is deliberately absent —
# it has no single directory per provisioner, so it's resolved by
# _resolve_helm_module_dirs() instead of _resolve_provisioner_dirs(). Compose/
# ArgoCD/Flux/Script remain unsupported — see the module docstring.
_FRAMEWORK_PROVISIONER_MAP: Dict[str, Tuple[ProvisionerType, Tuple[str, ...]]] = {
    "terraform": (ProvisionerType.TERRAFORM, ("*.tf",)),
    "bicep": (ProvisionerType.BICEP, ("*.bicep",)),
    "ansible": (ProvisionerType.ANSIBLE, ("*.yml", "*.yaml")),
}

# Frameworks this policy knows how to resolve artifact paths for — supersets
# _FRAMEWORK_PROVISIONER_MAP with "helm", which uses its own resolver.
_SUPPORTED_FRAMEWORKS = frozenset(_FRAMEWORK_PROVISIONER_MAP) | {"helm"}


class CheckovPolicy(BasePolicy):
    """Deny (or warn/audit) builds with Checkov findings above the configured severity gate."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)
        self.logger = get_logger(__name__)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        framework: str = configuration.get("framework") or "terraform"
        severity_gate: str = (configuration.get("severity_gate") or "high").upper()
        scope: str = configuration.get("scope") or "staged"
        skip_checks: List[str] = configuration.get("skip_checks") or []
        include_checks: Optional[List[str]] = configuration.get("include_checks") or None
        custom_checks_dir: Optional[str] = configuration.get("custom_checks_dir")
        timeout: int = int(configuration.get("timeout") or 120)

        if severity_gate not in _SEVERITY_ORDER:
            return self._skip(f"invalid severity_gate '{severity_gate}' — use CRITICAL|HIGH|MEDIUM|LOW")

        if framework not in _SUPPORTED_FRAMEWORKS:
            supported = ", ".join(sorted(_SUPPORTED_FRAMEWORKS))
            return self._skip(f"framework '{framework}' has no supported provisioner mapping — use one of: {supported}")

        extra_warnings: List[str] = []
        if framework == "helm":
            provisioner_dirs, skip_reason, extra_warnings = self._resolve_helm_module_dirs(context, scope)
        else:
            provisioner_dirs, skip_reason = self._resolve_provisioner_dirs(context, scope, framework)
        if skip_reason is not None:
            return self._skip(skip_reason, extra_warnings=extra_warnings)

        per_provisioner: List[Dict[str, Any]] = []
        violations: List[str] = []
        warnings: List[str] = list(extra_warnings)
        passed = True

        for prov_name, provisioner_dir in provisioner_dirs:
            scan_result = self._run_scan(
                terraform_dir=provisioner_dir,
                framework=framework,
                skip_checks=skip_checks,
                include_checks=include_checks,
                custom_checks_dir=custom_checks_dir,
                timeout=timeout,
            )
            if scan_result is None:
                warnings.append(
                    f"checkov: scan skipped for provisioner '{prov_name}' "
                    "(Checkov not available or scan failed) — nothing was enforced for it"
                )
                per_provisioner.append(
                    {"provisioner": prov_name, "scanned_path": str(provisioner_dir), "skipped": True}
                )
                continue

            breaching = scan_result.findings_at_or_above(severity_gate)
            prov_violations = [
                f"[{prov_name}] [{f.severity}] {f.check_id}: {f.check_name} — {f.resource} ({f.file_path})"
                for f in breaching
            ]
            violations.extend(prov_violations)
            if prov_violations:
                passed = False

            per_provisioner.append(
                {
                    "provisioner": prov_name,
                    "scanner_version": scan_result.scanner_version,
                    "framework": scan_result.framework,
                    "scanned_path": scan_result.scanned_path,
                    "passed": scan_result.passed,
                    "failed": scan_result.failed,
                    "skipped": scan_result.skipped,
                    "breaching_count": len(breaching),
                }
            )

        return PolicyResult(
            passed=passed,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            warnings=warnings,
            details={
                "scanner": "checkov",
                "scope": scope,
                "severity_gate": severity_gate,
                "provisioners": per_provisioner,
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _skip(self, reason: str, extra_warnings: Optional[List[str]] = None) -> PolicyResult:
        """Build a graceful-degradation PolicyResult: pass=True, but never silent.

        ``extra_warnings`` carries any per-item context accumulated before the
        decision to skip entirely (e.g. Helm registry-chart skips found while
        resolving module directories) — surfaced alongside the generic skip
        message rather than discarded.
        """
        return PolicyResult(
            passed=True,
            policy_name=self.name,
            enforcement=self.enforcement,
            details={"skipped": reason},
            warnings=[f"checkov: {reason} — scan skipped, nothing was enforced", *(extra_warnings or [])],
        )

    def _resolve_provisioner_dirs(
        self, context: PolicyContext, scope: str, framework: str
    ) -> Tuple[List[Tuple[str, Path]], Optional[str]]:
        """Resolve ``(provisioner_name, path)`` pairs for *framework*'s provisioner type matching *scope*.

        Returns ``(dirs, skip_reason)``. ``skip_reason`` is ``None`` on success;
        when set, the caller must skip and surface it (``dirs`` is empty in that case).
        """
        from strata.utils.provisioner_resolution import (
            resolve_stage_provisioner_name,
            stage_reachable_provisioner_names,
        )

        provisioner_type, glob_patterns = _FRAMEWORK_PROVISIONER_MAP[framework]

        if not context.build_path or context.deployment_service is None:
            return [], f"no {framework} artifacts found in build path"

        deployment_service = context.deployment_service
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return [], f"no {framework} artifacts found in build path"

        all_provisioners = workspace_service.model.spec.provisioners or []
        matching_provisioners = [p for p in all_provisioners if p.provisioner == provisioner_type]
        if not matching_provisioners:
            return [], f"no {framework} provisioners declared in this workspace"

        deployment_model = deployment_service.model
        stages = list(deployment_model.spec.stages or []) if deployment_model and deployment_model.spec else []

        if scope == "all":
            selected = matching_provisioners
        elif scope == "staged":
            reachable = stage_reachable_provisioner_names(workspace_service.model, stages)
            selected = [p for p in matching_provisioners if p.name in reachable]
        else:
            stage = next((s for s in stages if s.name == scope), None)
            if stage is None:
                valid_names = ", ".join(sorted(s.name for s in stages)) or "none declared"
                return [], (
                    f"invalid scope '{scope}' — use 'staged', 'all', or a declared stage name "
                    f"(available: {valid_names})"
                )
            resolved_name = resolve_stage_provisioner_name(stage, workspace_service.model)
            selected = [p for p in matching_provisioners if p.name == resolved_name]

        if not selected:
            return [], f"no {framework} artifacts found for the selected scope"

        build_path = Path(context.build_path)
        dirs: List[Tuple[str, Path]] = []
        for prov in selected:
            if context.solution_controller is not None:
                candidate = context.solution_controller.get_provisioner_path(deployment_service, build_path, prov)
            else:
                target = (prov.source.target_path or prov.source.source_path) if prov.source else framework
                candidate = deployment_service.get_build_path(build_path) / target
            if candidate.is_dir() and any(list(candidate.glob(pattern)) for pattern in glob_patterns):
                dirs.append((prov.name, candidate))

        if not dirs:
            return [], f"no {framework} artifacts found for the selected scope"

        return dirs, None

    def _resolve_helm_module_dirs(
        self, context: PolicyContext, scope: str
    ) -> Tuple[List[Tuple[str, Path]], Optional[str], List[str]]:
        """Resolve ``(namespace/module, path)`` pairs for local Helm charts matching *scope*.

        Unlike terraform/bicep/ansible, Helm has no single directory per provisioner —
        each namespace's modules own their own chart source independently (ADR-0051's
        Helm follow-up, 2026-09-17). Resolution:

        1. Determine target namespace names from *scope* via ``helm_namespaces_for_stage()``
           (a direct, single-hop mapping — no topology traversal needed).
        2. For each namespace, enumerate its modules via the already-loaded
           ``NamespaceService`` (the same source ``HelmBuilder`` uses) and load each
           module YAML to check ``spec.type == helm``.
        3. Registry-pulled charts (``source.chart_repository`` set) have no local chart
           source at build time — skipped with an explicit warning, never silently.
        4. Local charts are confirmed scannable by checking for a copied ``Chart.yaml``
           at ``get_module_build_path()``.

        Returns ``(dirs, skip_reason, extra_warnings)``. ``skip_reason`` is ``None`` on
        success; when set, the caller must skip and surface it (``dirs`` is empty).
        ``extra_warnings`` (e.g. registry-chart skips) is merged into the final
        ``PolicyResult.warnings`` even when some other modules scanned successfully.
        """
        from strata.models.common_models import ProvisionerType, ServiceDeployerType
        from strata.services.module_service import ModuleService
        from strata.utils.provisioner_resolution import helm_namespaces_for_stage, resolve_stage_provisioner_name
        from strata.utils.system import resolve_path

        if not context.build_path or context.deployment_service is None:
            return [], "no helm artifacts found in build path", []

        deployment_service = context.deployment_service
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return [], "no helm artifacts found in build path", []

        all_namespaces = {ns.name for ns in (workspace_service.model.spec.namespaces or [])}
        if not all_namespaces:
            return [], "no namespaces declared in this workspace", []

        deployment_model = deployment_service.model
        stages = list(deployment_model.spec.stages or []) if deployment_model and deployment_model.spec else []
        provisioners = workspace_service.model.spec.provisioners or []

        if scope == "all":
            target_namespaces = set(all_namespaces)
        elif scope == "staged":
            target_namespaces = set()
            for stage in stages:
                resolved_name = resolve_stage_provisioner_name(stage, workspace_service.model)
                prov = next((p for p in provisioners if p.name == resolved_name), None)
                if prov is not None and prov.provisioner == ProvisionerType.HELM:
                    target_namespaces |= helm_namespaces_for_stage(stage, workspace_service.model)
        else:
            stage = next((s for s in stages if s.name == scope), None)
            if stage is None:
                valid_names = ", ".join(sorted(s.name for s in stages)) or "none declared"
                return (
                    [],
                    (
                        f"invalid scope '{scope}' — use 'staged', 'all', or a declared stage name "
                        f"(available: {valid_names})"
                    ),
                    [],
                )
            target_namespaces = helm_namespaces_for_stage(stage, workspace_service.model)

        if not target_namespaces:
            return [], "no helm artifacts found for the selected scope", []

        namespace_services = deployment_service.get_namespace_services() or {}
        build_path = Path(context.build_path)
        work_path = context.work_path
        repo_map = context.solution_controller.get_repo_map() if context.solution_controller is not None else {}

        extra_warnings: List[str] = []
        dirs: List[Tuple[str, Path]] = []
        for ns_name in sorted(target_namespaces):
            ns_service = namespace_services.get(ns_name)
            if ns_service is None or not ns_service.is_validated() or not ns_service.model:
                continue

            for module_ref in ns_service.model.spec.modules or []:
                if not work_path:
                    continue
                try:
                    module_path = resolve_path(str(work_path), module_ref.file, repo_map=repo_map)
                except Exception:
                    continue
                if not module_path.exists():
                    continue

                mod_service = ModuleService.load(str(module_path), validate=True)
                if not mod_service.is_validated() or not mod_service.model:
                    continue

                module = mod_service.model
                if module.spec.type != ServiceDeployerType.HELM:
                    continue

                module_name = str(module.meta.name)
                label = f"{ns_name}/{module_name}"

                if module.spec.source.chart_repository:
                    extra_warnings.append(
                        f"checkov: module '{label}' pulls its chart from a registry "
                        "(chart_repository set) — no local chart source to scan, skipped"
                    )
                    continue

                module_dir = (
                    context.solution_controller.get_module_build_path(
                        deployment_service, build_path, ns_name, module_name
                    )
                    if context.solution_controller is not None
                    else deployment_service.get_build_path(build_path) / ns_name / module_name
                )
                if module_dir.is_dir() and (module_dir / "Chart.yaml").exists():
                    dirs.append((label, module_dir))

        if not dirs:
            return [], "no helm artifacts found for the selected scope", extra_warnings

        return dirs, None, extra_warnings

    def _run_scan(
        self,
        terraform_dir: Path,
        framework: str,
        skip_checks: List[str],
        include_checks: Optional[List[str]],
        custom_checks_dir: Optional[str],
        timeout: int,
    ):
        """Invoke CheckovIntegration.scan(). Returns None on any failure."""
        from strata.integrations.checkov import CheckovIntegration
        from strata.models.integration_model import IntegrationModel

        config = IntegrationModel(name="checkov", type="checkov")
        scanner = CheckovIntegration(config)

        available, reason = scanner.ensure_available()
        if not available:
            self.logger.debug("checkov policy: scanner not available, skipping", reason=reason)
            return None

        try:
            return scanner.scan(
                terraform_dir=terraform_dir,
                framework=framework,
                skip_checks=skip_checks or None,
                include_checks=include_checks,
                external_checks_dir=custom_checks_dir,
                timeout=timeout,
            )
        except RuntimeError as exc:
            self.logger.warning("checkov policy: scan failed", error=str(exc))
            return None
