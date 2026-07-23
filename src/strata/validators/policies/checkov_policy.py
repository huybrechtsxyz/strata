#!/usr/bin/env python3
"""Built-in policy: Checkov IaC security scanning.

Evaluates at the ``build`` phase.  Runs Checkov against the Terraform
artifacts produced by ``strata build run`` and fails when findings at or
above ``severity_gate`` are detected.

Context resolution
------------------
The policy resolves the Terraform artifact directory from ``context.build_path``.
It looks for the directory in order:

1. ``{build_path}/{deployment_name}/terraform/`` (deployment-scoped build)
2. ``{build_path}/terraform/``
3. ``{build_path}/`` itself (flat layout)

If none of these directories contain ``.tf`` files the policy skips gracefully.

Graceful degradation
--------------------
- Checkov not installed → pass (skip, warning logged)
- No Terraform artifacts in build path → pass (skip)
- Scan subprocess fails → pass (skip, warning logged)
- ``severity_gate`` not configured → defaults to ``high``

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
          skip_checks:                  # CKV IDs to suppress
            - CKV_AWS_1
            - CKV_AWS_20
          include_checks: []            # if set, run ONLY these checks
          custom_checks_dir: ".strata/checkov/custom/"  # optional
          timeout: 120                  # seconds, default 120
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
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
        skip_checks: List[str] = configuration.get("skip_checks") or []
        include_checks: Optional[List[str]] = configuration.get("include_checks") or None
        custom_checks_dir: Optional[str] = configuration.get("custom_checks_dir")
        timeout: int = int(configuration.get("timeout") or 120)

        if severity_gate not in _SEVERITY_ORDER:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": f"invalid severity_gate '{severity_gate}' — use CRITICAL|HIGH|MEDIUM|LOW"},
            )

        # Locate the Terraform artifact directory
        terraform_dir = self._resolve_terraform_dir(context)
        if terraform_dir is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no Terraform artifacts found in build path"},
            )

        # Run Checkov
        scan_result = self._run_scan(
            terraform_dir=terraform_dir,
            framework=framework,
            skip_checks=skip_checks,
            include_checks=include_checks,
            custom_checks_dir=custom_checks_dir,
            timeout=timeout,
        )
        if scan_result is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "Checkov not available or scan failed"},
            )

        # Apply severity gate
        breaching = scan_result.findings_at_or_above(severity_gate)

        violations: List[str] = [
            f"[{f.severity}] {f.check_id}: {f.check_name} — {f.resource} ({f.file_path})" for f in breaching
        ]

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "scanner": "checkov",
                "scanner_version": scan_result.scanner_version,
                "framework": scan_result.framework,
                "scanned_path": scan_result.scanned_path,
                "passed": scan_result.passed,
                "failed": scan_result.failed,
                "skipped": scan_result.skipped,
                "severity_gate": severity_gate,
                "breaching_count": len(breaching),
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_terraform_dir(self, context: PolicyContext) -> Optional[Path]:
        """Find the Terraform artifact directory under build_path."""
        if not context.build_path:
            return None

        build_path = Path(context.build_path)

        # Try deployment-scoped subdirectory first
        if context.deployment_service is not None:
            try:
                dep_build = context.deployment_service.get_build_path(build_path)
                candidate = dep_build / "terraform"
                if candidate.is_dir() and list(candidate.glob("*.tf")):
                    return candidate
            except Exception:
                pass

        # Fall back to build_path/terraform/ or build_path/ directly
        for candidate in [build_path / "terraform", build_path]:
            if candidate.is_dir() and list(candidate.glob("*.tf")):
                return candidate

        return None

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
