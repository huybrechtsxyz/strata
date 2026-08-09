#!/usr/bin/env python3
"""Built-in policy: enforce a maximum CVE severity threshold.

Evaluates at the ``build`` phase.  Fails when the number of vulnerability
findings at or above ``max_severity`` exceeds ``max_count`` (default: 0).

Context resolution
------------------
1. If ``context.cve_audit_result`` is already populated (``--audit`` was
   passed to ``build run``), the policy reuses that result — no second scan.
2. Otherwise the policy runs the CVE scanner itself against the SBOM produced
   during the current build.  If no scanner is available the policy skips
   gracefully.

Graceful degradation
--------------------
- No SBOM in build path and no pre-computed result → pass (skip)
- No CVE scanner available → pass (skip, warning logged)
- ``max_severity`` not configured → pass (skip)

Example configuration YAML::

    policies:
      - name: no_critical_cves
        type: cve_max_severity
        phase: build
        enforcement: deny
        description: "Block builds with CRITICAL vulnerabilities"
        configuration:
          max_severity: CRITICAL     # CRITICAL | HIGH | MEDIUM | LOW
          max_count: 0               # fail when count exceeds this (default 0)
          severity_threshold: MEDIUM # minimum severity sent to scanner (default MEDIUM)
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class CveMaxSeverityPolicy(BasePolicy):
    """Deny (or warn/audit) builds whose CVE findings exceed the configured threshold."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)
        self.logger = get_logger(__name__)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        max_severity: str = (configuration.get("max_severity") or "").upper()
        max_count: int = int(configuration.get("max_count") or 0)
        severity_threshold: str = (configuration.get("severity_threshold") or "MEDIUM").upper()

        if not max_severity or max_severity not in _SEVERITY_ORDER:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "max_severity not configured or invalid"},
            )

        # --- Resolve CVE audit result ----------------------------------------
        audit_result = context.cve_audit_result
        if audit_result is None:
            audit_result = self._run_scan(context, severity_threshold)
            if audit_result is None:
                return PolicyResult(
                    passed=True,
                    policy_name=self.name,
                    enforcement=self.enforcement,
                    details={"skipped": "no SBOM available or scanner not found"},
                )

        # --- Apply allowlist from work_path if context provides it -----------
        audit_result = self._apply_allowlist(audit_result, context.work_path)

        # --- Count findings at or above max_severity -------------------------
        threshold_idx = _SEVERITY_ORDER.index(max_severity)
        counts = {
            "CRITICAL": audit_result.critical,
            "HIGH": audit_result.high,
            "MEDIUM": audit_result.medium,
            "LOW": audit_result.low,
            "UNKNOWN": audit_result.unknown,
        }
        breaching = sum(counts[s] for s in _SEVERITY_ORDER[: threshold_idx + 1] if s in counts)

        violations: List[str] = []
        if breaching > max_count:
            violations.append(
                f"{breaching} finding(s) at or above {max_severity} "
                f"(limit: {max_count}, scanner: {audit_result.scanner} {audit_result.scanner_version})"
            )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "scanner": audit_result.scanner,
                "scanner_version": audit_result.scanner_version,
                "total_findings": audit_result.total_findings,
                "critical": audit_result.critical,
                "high": audit_result.high,
                "medium": audit_result.medium,
                "low": audit_result.low,
                "unknown": audit_result.unknown,
                "max_severity": max_severity,
                "max_count": max_count,
                "breaching_count": breaching,
            },
        )

    def _run_scan(self, context: PolicyContext, severity_threshold: str):
        """Run the CVE scanner against the build SBOM. Returns None if unavailable."""
        from strata.integrations.cve_scanner import CveScannerIntegration
        from strata.models.integration_model import IntegrationModel

        if not context.build_path:
            return None

        # Locate sbom.json — try deployment service build path first, then root build path
        sbom_path: Path | None = None
        if context.deployment_service is not None:
            candidate = context.deployment_service.get_build_path(context.build_path) / "sbom.json"
            if candidate.exists():
                sbom_path = candidate

        if sbom_path is None:
            candidate = context.build_path / "sbom.json"
            if candidate.exists():
                sbom_path = candidate

        if sbom_path is None:
            return None

        config = IntegrationModel(name="cve_scanner", type="cve_scanner")
        scanner = CveScannerIntegration(config)

        available, reason = scanner.ensure_available()
        if not available:
            self.logger.debug("CVE policy: scanner not available, skipping", reason=reason)
            return None

        try:
            return scanner.scan_sbom(sbom_path, severity_threshold=severity_threshold)
        except RuntimeError as exc:
            self.logger.warning("CVE policy: scan failed", error=str(exc))
            return None

    @staticmethod
    def _apply_allowlist(audit_result: Any, work_path: Optional[Path]) -> Any:
        """Filter findings through .strata/cve-allowed.yaml if it exists."""
        from datetime import date

        from strata.models.sbom_model import CveAllowedEntryModel
        from strata.utils.config import get_cve_allowed_path

        if work_path is None:
            return audit_result

        allowed_path = get_cve_allowed_path(work_path)
        if not allowed_path.exists():
            return audit_result

        try:
            import yaml

            with allowed_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, dict):
                return audit_result
            entries = [CveAllowedEntryModel(**e) for e in (data.get("allowed") or []) if isinstance(e, dict)]
        except Exception:
            return audit_result

        today = date.today()
        allowed_ids: dict[str, CveAllowedEntryModel] = {}
        for entry in entries:
            if entry.expires:
                try:
                    if date.fromisoformat(entry.expires) < today:
                        continue
                except ValueError:
                    pass
            allowed_ids[entry.id] = entry

        if not allowed_ids:
            return audit_result

        filtered = [
            f
            for f in audit_result.findings
            if not (
                f.vulnerability_id in allowed_ids
                and (
                    allowed_ids[f.vulnerability_id].package is None
                    or allowed_ids[f.vulnerability_id].package == f.package_name
                )
            )
        ]

        if len(filtered) == len(audit_result.findings):
            return audit_result

        sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        for f in filtered:
            if f.severity in sev_counts:
                sev_counts[f.severity] += 1

        return audit_result.model_copy(
            update={
                "findings": filtered,
                "total_findings": len(filtered),
                **sev_counts,
            }
        )
