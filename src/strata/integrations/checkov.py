"""Checkov integration — IaC security scanner for Terraform artifacts.

Wraps the Checkov CLI (``checkov --directory ... --output json``) and parses
its JSON output into structured ``CheckovScanResult`` / ``CheckovFinding``
dataclasses.

Install Checkov: https://www.checkov.io/2.Basics/Installing%20Checkov.html

Example YAML declaration::

    integrations:
      - name: checkov
        type: checkov
        capabilities: [iac_security]
        required: false
        validation:
          command: checkov --version
          min_version: "2.0.0"
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.utils.system import CommandResult

logger = get_logger(__name__)

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CheckovFinding:
    """A single Checkov check failure."""

    check_id: str
    check_name: str
    resource: str  # e.g. "aws_s3_bucket.example"
    file_path: str
    file_line_range: List[int] = field(default_factory=list)
    severity: str = "UNKNOWN"  # CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN
    guideline: str = ""  # remediation URL / description from Checkov


@dataclass
class CheckovScanResult:
    """Aggregated result of a Checkov scan."""

    passed: int
    failed: int
    skipped: int
    findings: List[CheckovFinding]
    scanner_version: str
    framework: str
    scanned_path: str

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    def findings_at_or_above(self, severity: str) -> List[CheckovFinding]:
        """Return findings at or above the given severity level."""
        if severity not in _SEVERITY_ORDER:
            return self.findings
        threshold_idx = _SEVERITY_ORDER.index(severity)
        return [f for f in self.findings if _severity_index(f.severity) <= threshold_idx]


def _severity_index(severity: str) -> int:
    """Return sort index (lower = more severe). Unknown → last."""
    try:
        return _SEVERITY_ORDER.index(severity.upper())
    except ValueError:
        return len(_SEVERITY_ORDER) - 1


# ---------------------------------------------------------------------------
# Integration class
# ---------------------------------------------------------------------------


class CheckovIntegration(BaseIntegration):
    """Checkov IaC security scanner integration.

    Invokes ``checkov --directory <terraform_dir> --output json --compact``
    and parses the structured JSON output into ``CheckovScanResult``.

    Supports the ``--skip-check``, ``--check``, and ``--external-checks-dir``
    flags for filtering.  Gracefully degrades when Checkov is not installed.
    """

    COMMAND = "checkov"
    CAPABILITIES: list = []  # IaC security — no shared Protocol yet; capability name: iac_security

    def get_version_command(self) -> List[str]:
        return [self.command, "--version"]

    def parse_version(self, version_output: str) -> str:
        """Extract version from ``checkov --version`` output (e.g. '3.2.123')."""
        m = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return m.group(1) if m else version_output.strip()

    def ensure_available(self) -> Tuple[bool, str]:
        if not self.is_available():
            return False, (
                "Checkov CLI is not installed or not in PATH. Install: pip install checkov  or  https://www.checkov.io"
            )
        self._info = f"checkov {self.get_version()} is available"
        return True, ""

    def get_setup_info(self) -> Dict[str, Any]:
        return {
            "name": "checkov",
            "command": "checkov",
            "install_url": "https://www.checkov.io/2.Basics/Installing%20Checkov.html",
            "env_vars": [],
            "auth_methods": [],
            "yaml_example": (
                "- name: checkov\n"
                "  type: checkov\n"
                "  capabilities: [iac_security]\n"
                "  required: false\n"
                "  validation:\n"
                "    command: checkov --version\n"
                '    min_version: "2.0.0"'
            ),
        }

    # ------------------------------------------------------------------
    # Public scan method
    # ------------------------------------------------------------------

    def scan(
        self,
        terraform_dir: Path,
        framework: str = "terraform",
        skip_checks: Optional[List[str]] = None,
        include_checks: Optional[List[str]] = None,
        external_checks_dir: Optional[str] = None,
        timeout: int = 120,
    ) -> CheckovScanResult:
        """Run Checkov against a Terraform artifact directory.

        Args:
            terraform_dir: Path to the directory containing ``.tf`` files.
            framework: IaC framework to scan (default ``terraform``).
            skip_checks: List of CKV IDs to suppress (e.g. ``["CKV_AWS_1"]``).
            include_checks: If set, only these check IDs are run.
            external_checks_dir: Path to a directory with custom Checkov checks.
            timeout: Subprocess timeout in seconds.

        Returns:
            ``CheckovScanResult`` with findings and counts.

        Raises:
            RuntimeError: If Checkov is not available.
        """
        available, error = self.ensure_available()
        if not available:
            raise RuntimeError(error)

        args = [
            "--directory",
            str(terraform_dir),
            "--framework",
            framework,
            "--output",
            "json",
            "--compact",
            "--quiet",  # suppress progress bars
            "--exit-code",
            "0",  # never exit non-zero on findings; we handle it
        ]

        if skip_checks:
            args += ["--skip-check", ",".join(skip_checks)]
        if include_checks:
            args += ["--check", ",".join(include_checks)]
        if external_checks_dir:
            args += ["--external-checks-dir", external_checks_dir]

        result: CommandResult = self._run_integration(args, timeout=timeout)

        # Checkov exits 0 on success, 1 on findings — both are valid for us
        if result.returncode not in (0, 1) and not result.stdout:
            raise RuntimeError(f"Checkov scan failed (exit {result.returncode}): {result.stderr[:300]}")

        return self._parse_output(result.stdout, framework=framework, scanned_path=str(terraform_dir))

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_output(self, raw: str, framework: str, scanned_path: str) -> CheckovScanResult:
        """Parse Checkov JSON output into a ``CheckovScanResult``."""
        if not raw or not raw.strip():
            return self._empty_result(framework, scanned_path)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("checkov: failed to parse JSON output")
            return self._empty_result(framework, scanned_path)

        # Checkov output can be a single results dict or a list (multi-framework)
        if isinstance(data, list):
            return self._merge_results(data, framework, scanned_path)

        return self._parse_results_block(data, framework, scanned_path)

    def _parse_results_block(self, data: Dict[str, Any], framework: str, scanned_path: str) -> CheckovScanResult:
        """Parse a single Checkov results block."""
        results = data.get("results") or {}
        passed_checks = results.get("passed_checks") or []
        failed_checks = results.get("failed_checks") or []
        skipped_checks = results.get("skipped_checks") or []
        summary = data.get("summary") or {}

        findings: List[CheckovFinding] = []
        for check in failed_checks:
            findings.append(self._parse_finding(check))

        scanner_version = self.get_version() or "unknown"

        return CheckovScanResult(
            passed=summary.get("passed", len(passed_checks)),
            failed=summary.get("failed", len(failed_checks)),
            skipped=summary.get("skipped", len(skipped_checks)),
            findings=findings,
            scanner_version=scanner_version,
            framework=data.get("check_type") or framework,
            scanned_path=scanned_path,
        )

    def _merge_results(self, data_list: List[Dict], framework: str, scanned_path: str) -> CheckovScanResult:
        """Merge multiple results blocks (multi-framework output)."""
        passed = failed = skipped = 0
        findings: List[CheckovFinding] = []
        for block in data_list:
            r = self._parse_results_block(block, framework, scanned_path)
            passed += r.passed
            failed += r.failed
            skipped += r.skipped
            findings.extend(r.findings)
        return CheckovScanResult(
            passed=passed,
            failed=failed,
            skipped=skipped,
            findings=findings,
            scanner_version=self.get_version() or "unknown",
            framework=framework,
            scanned_path=scanned_path,
        )

    def _parse_finding(self, check: Dict[str, Any]) -> CheckovFinding:
        """Convert a single failed_check entry to a CheckovFinding."""
        severity = (check.get("severity") or "UNKNOWN").upper()
        if severity not in _SEVERITY_ORDER:
            severity = "UNKNOWN"

        return CheckovFinding(
            check_id=check.get("check_id", "UNKNOWN"),
            check_name=check.get("check_name", ""),
            resource=check.get("resource", ""),
            file_path=check.get("repo_file_path") or check.get("file_path", ""),
            file_line_range=check.get("file_line_range") or [],
            severity=severity,
            guideline=check.get("guideline") or "",
        )

    def _empty_result(self, framework: str, scanned_path: str) -> CheckovScanResult:
        return CheckovScanResult(
            passed=0,
            failed=0,
            skipped=0,
            findings=[],
            scanner_version=self.get_version() or "unknown",
            framework=framework,
            scanned_path=scanned_path,
        )
