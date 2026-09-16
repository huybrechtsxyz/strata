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

Supported frameworks (ADR-0051, 2026-09-16 multi-provisioner follow-up)
------------------------------------------------------------------------
``configuration.framework`` selects both the Checkov framework AND, via
``_FRAMEWORK_PROVISIONER_MAP``, which strata provisioner type is scanned:

- ``terraform`` (default) — ``provisioner: terraform`` entries, ``*.tf`` files.
- ``bicep`` — ``provisioner: bicep`` entries, ``*.bicep`` files. Fits the same
  flat ``get_provisioner_path()`` shape as terraform (confirmed: ``bicep_builder.py``
  copies source there too).
- ``ansible`` — ``provisioner: ansible`` entries, ``*.yml``/``*.yaml`` files. Also
  fits the same flat shape (confirmed: ``ansible_builder.py`` copies source there too).

An unrecognized ``framework`` (including ``helm``, ``compose``, ``argocd``, ``flux``,
``script`` — all real strata provisioner types, none of them supported by this
policy) skips gracefully with a message listing the supported values. ``helm``
is deliberately excluded even though Checkov supports it: Helm's build output is
organized per namespace+module (``get_module_build_path()``), not one directory
per provisioner, so it needs its own resolution design rather than fitting this
generalization — see ADR-0051's "Proposed design" section. ``compose`` has no
Checkov framework at all; ``argocd``/``flux`` render from the platform artifact
with no stable build-time source directory; ``script`` is not IaC.

One policy instance scans one framework — ``skip_checks``/finding IDs live in
unrelated namespaces per framework (``CKV_AWS_*`` vs ``CKV_ANSIBLE_*`` vs Bicep/ARM
checks), so a workspace wanting coverage across multiple frameworks declares one
``checkov`` policy per framework.

``configuration.scope`` controls which provisioner(s) of the selected framework
are scanned when a workspace declares more than one:

- ``staged`` (default) — provisioners reachable from at least one deployment
  stage (``stage.provisioner`` or ``stage.topology`` → ``topology.provisioner``).
  A shared module-library provisioner that no stage targets directly is
  excluded by construction — no extra metadata needed.
- ``all`` — every provisioner of the selected framework in the workspace,
  staged or not.
- ``<stage-name>`` — provisioner(s) reachable from that one named stage only.
  This is a static filter over the same stage list ``staged`` uses, not a
  runtime binding to "when that stage deploys" — ``build`` evaluates once,
  regardless of deploy stages.

Note: ``configuration.scope`` is unrelated to the existing, different,
free-form ``stages[].scope`` CLI-filter label — same field name, different
namespace.

When more than one provisioner is scanned, a breach in any one of them denies
the whole policy result (AND semantics) — findings are still reported per
provisioner in ``PolicyResult.details["provisioners"]`` and violation strings
are prefixed with the provisioner name (e.g. ``[control_infra] CKV_AWS_1: ...``).

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
# are actually present on disk before scanning). Helm/Compose/ArgoCD/Flux/Script
# are deliberately absent — see the module docstring's "Supported frameworks" note.
_FRAMEWORK_PROVISIONER_MAP: Dict[str, Tuple[ProvisionerType, Tuple[str, ...]]] = {
    "terraform": (ProvisionerType.TERRAFORM, ("*.tf",)),
    "bicep": (ProvisionerType.BICEP, ("*.bicep",)),
    "ansible": (ProvisionerType.ANSIBLE, ("*.yml", "*.yaml")),
}


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

        if framework not in _FRAMEWORK_PROVISIONER_MAP:
            supported = ", ".join(sorted(_FRAMEWORK_PROVISIONER_MAP))
            return self._skip(f"framework '{framework}' has no supported provisioner mapping — use one of: {supported}")

        provisioner_dirs, skip_reason = self._resolve_provisioner_dirs(context, scope, framework)
        if skip_reason is not None:
            return self._skip(skip_reason)

        per_provisioner: List[Dict[str, Any]] = []
        violations: List[str] = []
        warnings: List[str] = []
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

    def _skip(self, reason: str) -> PolicyResult:
        """Build a graceful-degradation PolicyResult: pass=True, but never silent."""
        return PolicyResult(
            passed=True,
            policy_name=self.name,
            enforcement=self.enforcement,
            details={"skipped": reason},
            warnings=[f"checkov: {reason} — scan skipped, nothing was enforced"],
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
