"""CVE scanner integration — auto-detects Trivy or Grype for vulnerability scanning."""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ICveScanner
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel
from strata.models.sbom_model import CveAuditResultModel, CveFindingModel
from strata.utils.system import CommandResult

logger = get_logger(__name__)

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def _detect_backend() -> Optional[str]:
    """Detect which CVE scanner is available. Trivy preferred over Grype."""
    if shutil.which("trivy"):
        return "trivy"
    if shutil.which("grype"):
        return "grype"
    return None


class CveScannerIntegration(BaseIntegration):
    """CVE vulnerability scanner integration.

    Auto-detects Trivy or Grype in PATH.  Scans CycloneDX SBOM files and
    returns structured vulnerability findings.
    """

    COMMAND = "trivy"  # default; overridden at init if grype is detected instead
    CAPABILITIES: list = [ICveScanner]

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        return "default"

    def __init__(self, config: IntegrationModel):
        # Detect backend before super().__init__ resolves command
        backend = _detect_backend()
        super().__init__(config)
        self._backend: Optional[str] = backend
        # Override the command resolved by base class with the detected backend
        if backend:
            self.command = backend
        logger.debug("CVE scanner initialized", backend=self._backend)

    @property
    def backend(self) -> Optional[str]:
        """Return 'trivy', 'grype', or None if unavailable."""
        return self._backend

    def get_version_command(self) -> List[str]:
        if self._backend == "grype":
            return [self.command, "version"]
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """Extract version from trivy/grype output."""
        # trivy: "Version: 0.52.0" or "trivy version 0.52.0"
        # grype: "grype 0.74.0" or "Application: grype\nVersion: 0.74.0"
        m = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return m.group(1) if m else version_output.strip()

    def ensure_available(self) -> Tuple[bool, str]:
        """Check if a CVE scanner backend is available."""
        if not self._backend:
            return False, (
                "No CVE scanner found. Install trivy (https://trivy.dev) or grype (https://github.com/anchore/grype)."
            )
        if not self.is_available():
            return False, f"{self._backend} is not available in PATH."
        self._info = f"{self._backend} {self.get_version()} is available"
        return True, ""

    def get_setup_info(self) -> Dict[str, Any]:
        return {
            "name": "cve_scanner",
            "command": self._backend or "trivy",
            "install_url": "https://trivy.dev/latest/getting-started/installation/",
            "env_vars": [],
            "auth_methods": [],
            "yaml_example": None,
        }

    def scan_sbom(
        self,
        sbom_path: Path,
        severity_threshold: str = "MEDIUM",
        timeout: int = 300,
    ) -> CveAuditResultModel:
        """Run vulnerability scan against a CycloneDX SBOM file.

        Args:
            sbom_path: Path to the sbom.json file.
            severity_threshold: Minimum severity to include (CRITICAL|HIGH|MEDIUM|LOW|UNKNOWN).
            timeout: Command timeout in seconds.

        Returns:
            CveAuditResultModel with findings and severity counts.

        Raises:
            RuntimeError: If scanner is not available or scan fails fatally.
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(error)

        if self._backend == "grype":
            return self._scan_grype(sbom_path, severity_threshold, timeout)
        return self._scan_trivy(sbom_path, severity_threshold, timeout)

    def _scan_trivy(self, sbom_path: Path, severity_threshold: str, timeout: int) -> CveAuditResultModel:
        """Run trivy sbom scan."""
        severities = self._severities_at_or_above(severity_threshold)

        args = [
            "sbom",
            str(sbom_path),
            "--format",
            "json",
            "--severity",
            ",".join(severities),
            "--exit-code",
            "0",  # don't fail on findings — we handle exit codes ourselves
        ]

        result: CommandResult = self._run_integration(args, timeout=timeout)
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(f"Trivy scan failed: {result.stderr}")

        return self._parse_trivy_output(result.stdout, sbom_path)

    def _scan_grype(self, sbom_path: Path, severity_threshold: str, timeout: int) -> CveAuditResultModel:
        """Run grype sbom scan."""
        args = [
            f"sbom:{sbom_path}",
            "--output",
            "json",
        ]

        result: CommandResult = self._run_integration(args, timeout=timeout)
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(f"Grype scan failed: {result.stderr}")

        return self._parse_grype_output(result.stdout, sbom_path, severity_threshold)

    def _parse_trivy_output(self, raw: str, sbom_path: Path) -> CveAuditResultModel:
        """Parse Trivy JSON output into structured model."""
        findings: List[CveFindingModel] = []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse trivy JSON output")
            return self._empty_result(sbom_path)

        for target in data.get("Results") or []:
            for vuln in target.get("Vulnerabilities") or []:
                findings.append(
                    CveFindingModel(
                        vulnerability_id=vuln.get("VulnerabilityID", "UNKNOWN"),
                        severity=(vuln.get("Severity") or "UNKNOWN").upper(),
                        package_name=vuln.get("PkgName", ""),
                        installed_version=vuln.get("InstalledVersion", ""),
                        fixed_version=vuln.get("FixedVersion") or None,
                        title=vuln.get("Title") or vuln.get("Description", "")[:120] or None,
                        purl=vuln.get("PkgIdentifier", {}).get("PURL") or None,
                    )
                )

        return self._build_result(findings, sbom_path)

    def _parse_grype_output(self, raw: str, sbom_path: Path, severity_threshold: str) -> CveAuditResultModel:
        """Parse Grype JSON output into structured model."""
        findings: List[CveFindingModel] = []
        threshold_idx = _SEVERITY_ORDER.index(severity_threshold) if severity_threshold in _SEVERITY_ORDER else 4

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse grype JSON output")
            return self._empty_result(sbom_path)

        for match in data.get("matches") or []:
            vuln = match.get("vulnerability") or {}
            severity = (vuln.get("severity") or "Unknown").upper()
            if severity == "NEGLIGIBLE":
                severity = "LOW"

            # Filter by threshold
            sev_idx = _SEVERITY_ORDER.index(severity) if severity in _SEVERITY_ORDER else 4
            if sev_idx > threshold_idx:
                continue

            artifact = match.get("artifact") or {}
            fix_versions = vuln.get("fix", {}).get("versions") or []

            findings.append(
                CveFindingModel(
                    vulnerability_id=vuln.get("id", "UNKNOWN"),
                    severity=severity,
                    package_name=artifact.get("name", ""),
                    installed_version=artifact.get("version", ""),
                    fixed_version=fix_versions[0] if fix_versions else None,
                    title=(vuln.get("description") or "")[:120] or None,
                    purl=artifact.get("purl") or None,
                )
            )

        return self._build_result(findings, sbom_path)

    def _build_result(self, findings: List[CveFindingModel], sbom_path: Path) -> CveAuditResultModel:
        """Build result model with severity counts."""
        counts = {s: 0 for s in _SEVERITY_ORDER}
        for f in findings:
            if f.severity in counts:
                counts[f.severity] += 1

        return CveAuditResultModel(
            scanner=self._backend or "unknown",
            scanner_version=self.get_version() or "unknown",
            sbom_path=str(sbom_path),
            total_findings=len(findings),
            critical=counts["CRITICAL"],
            high=counts["HIGH"],
            medium=counts["MEDIUM"],
            low=counts["LOW"],
            unknown=counts["UNKNOWN"],
            findings=findings,
        )

    def _empty_result(self, sbom_path: Path) -> CveAuditResultModel:
        """Return an empty result when parsing fails."""
        return CveAuditResultModel(
            scanner=self._backend or "unknown",
            scanner_version=self.get_version() or "unknown",
            sbom_path=str(sbom_path),
            total_findings=0,
            critical=0,
            high=0,
            medium=0,
            low=0,
            unknown=0,
            findings=[],
        )

    @staticmethod
    def _severities_at_or_above(threshold: str) -> List[str]:
        """Return list of severity strings at or above the threshold."""
        if threshold not in _SEVERITY_ORDER:
            return _SEVERITY_ORDER
        idx = _SEVERITY_ORDER.index(threshold)
        return _SEVERITY_ORDER[: idx + 1]
