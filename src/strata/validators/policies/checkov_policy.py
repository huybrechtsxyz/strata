#!/usr/bin/env python3
"""Built-in policy: Checkov IaC security scanning.

Evaluates at the ``build`` phase.  Runs Checkov against the Terraform
artifacts produced by ``strata build run`` and fails when findings at or
above ``severity_gate`` are detected, for any scanned provisioner.

Context resolution (ADR-0051 revision, 2026-09-16)
---------------------------------------------------
The artifact directory for each scanned terraform provisioner is resolved via
``SolutionController.get_provisioner_path()`` — the same single source of
truth ``TerraformBuilder`` (copy destination) and ``TerraformDeployer``
(working directory) already use, honouring each provisioner's
``source.target_path`` / ``source.source_path``. Previously this policy
guessed at flat candidate directories under ``context.build_path`` that never
consulted a provisioner's ``source_path`` — silently skipping (and reporting
``passed=True``) for any workspace whose terraform provisioner used a nested
``source_path``. See ADR-0051's 2026-09-16 revision for the full writeup.

``configuration.scope`` controls which terraform provisioner(s) are scanned
when a workspace declares more than one:

- ``staged`` (default) — provisioners reachable from at least one deployment
  stage (``stage.provisioner`` or ``stage.topology`` → ``topology.provisioner``).
  A shared module-library provisioner that no stage targets directly is
  excluded by construction — no extra metadata needed.
- ``all`` — every ``provisioner: terraform`` entry in the workspace, staged or
  not.
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

Not in scope: multi-framework support. Checkov itself scans CloudFormation,
Kubernetes, Helm, Dockerfile, etc., but this policy's path resolution is
Terraform-only end-to-end (``provisioner: terraform`` filter, ``.tf`` glob
check) — see ADR-0051's Future Considerations.

Graceful degradation
--------------------
Every skip condition below also appends an explicit, actionable message to
``PolicyResult.warnings`` (surfaced by every ``PolicyContext`` call site as a
``⚠`` line, even when ``passed=True``) — a silent pass is never silent again:

- ``severity_gate``/``scope`` not valid → pass (skip)
- No Terraform artifacts found for the selected scope → pass (skip)
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
          framework: terraform          # default: terraform
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

        provisioner_dirs, skip_reason = self._resolve_terraform_dirs(context, scope)
        if skip_reason is not None:
            return self._skip(skip_reason)

        per_provisioner: List[Dict[str, Any]] = []
        violations: List[str] = []
        warnings: List[str] = []
        passed = True

        for prov_name, terraform_dir in provisioner_dirs:
            scan_result = self._run_scan(
                terraform_dir=terraform_dir,
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
                per_provisioner.append({"provisioner": prov_name, "scanned_path": str(terraform_dir), "skipped": True})
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

    def _resolve_terraform_dirs(
        self, context: PolicyContext, scope: str
    ) -> Tuple[List[Tuple[str, Path]], Optional[str]]:
        """Resolve ``(provisioner_name, path)`` pairs for terraform provisioners matching *scope*.

        Returns ``(dirs, skip_reason)``. ``skip_reason`` is ``None`` on success;
        when set, the caller must skip and surface it (``dirs`` is empty in that case).
        """
        from strata.utils.provisioner_resolution import (
            resolve_stage_provisioner_name,
            stage_reachable_provisioner_names,
        )

        if not context.build_path or context.deployment_service is None:
            return [], "no Terraform artifacts found in build path"

        deployment_service = context.deployment_service
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return [], "no Terraform artifacts found in build path"

        all_provisioners = workspace_service.model.spec.provisioners or []
        terraform_provisioners = [p for p in all_provisioners if p.provisioner == ProvisionerType.TERRAFORM]
        if not terraform_provisioners:
            return [], "no terraform provisioners declared in this workspace"

        deployment_model = deployment_service.model
        stages = list(deployment_model.spec.stages or []) if deployment_model and deployment_model.spec else []

        if scope == "all":
            selected = terraform_provisioners
        elif scope == "staged":
            reachable = stage_reachable_provisioner_names(workspace_service.model, stages)
            selected = [p for p in terraform_provisioners if p.name in reachable]
        else:
            stage = next((s for s in stages if s.name == scope), None)
            if stage is None:
                valid_names = ", ".join(sorted(s.name for s in stages)) or "none declared"
                return [], (
                    f"invalid scope '{scope}' — use 'staged', 'all', or a declared stage name "
                    f"(available: {valid_names})"
                )
            resolved_name = resolve_stage_provisioner_name(stage, workspace_service.model)
            selected = [p for p in terraform_provisioners if p.name == resolved_name]

        if not selected:
            return [], "no Terraform artifacts found for the selected scope"

        build_path = Path(context.build_path)
        dirs: List[Tuple[str, Path]] = []
        for prov in selected:
            if context.solution_controller is not None:
                candidate = context.solution_controller.get_provisioner_path(deployment_service, build_path, prov)
            else:
                target = (prov.source.target_path or prov.source.source_path) if prov.source else "terraform"
                candidate = deployment_service.get_build_path(build_path) / target
            if candidate.is_dir() and list(candidate.glob("*.tf")):
                dirs.append((prov.name, candidate))

        if not dirs:
            return [], "no Terraform artifacts found for the selected scope"

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
