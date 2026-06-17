"""Tests for the --audit flag on the build sbom command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.builders.sbom_build_command import SbomBuildCommand
from strata.models.sbom_model import CveAuditResultModel, CveFindingModel


def _make_result(
    *,
    scanner: str = "trivy",
    critical: int = 0,
    high: int = 0,
    medium: int = 0,
    low: int = 0,
    unknown: int = 0,
    findings: list | None = None,
) -> CveAuditResultModel:
    if findings is None:
        findings = []
    return CveAuditResultModel(
        scanner=scanner,
        scanner_version="0.52.0",
        sbom_path="sbom.json",
        total_findings=len(findings),
        critical=critical,
        high=high,
        medium=medium,
        low=low,
        unknown=unknown,
        findings=findings,
    )


def _make_finding(
    vuln_id: str = "CVE-2024-0001",
    severity: str = "HIGH",
    pkg: str = "openssl",
    version: str = "3.0.1",
    fixed: str | None = "3.0.14",
) -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id=vuln_id,
        severity=severity,
        package_name=pkg,
        installed_version=version,
        fixed_version=fixed,
        title="Test vulnerability",
        purl=None,
    )


class TestExecuteAudit:
    """Test SbomBuildCommand._execute_audit in isolation."""

    def _make_command(self, **overrides):
        defaults = {
            "output": "console",
            "audit": True,
            "audit_severity": "MEDIUM",
            "fail_on": None,
        }
        defaults.update(overrides)
        cmd = SbomBuildCommand.__new__(SbomBuildCommand)
        cmd._output_format = defaults["output"]
        cmd._output_quiet = False
        cmd._audit = defaults["audit"]
        cmd._audit_severity = defaults["audit_severity"]
        cmd._fail_on = defaults["fail_on"]
        cmd._errors = []
        cmd._messages = []
        cmd._output_data = {}
        cmd.logger = MagicMock()
        return cmd

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_scanner_unavailable_returns_true(self, MockScanner):
        """When no scanner installed, audit is skipped gracefully."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (False, "not found")

        cmd = self._make_command()
        assert cmd._execute_audit(Path("sbom.json")) is True

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_clean_audit_returns_true(self, MockScanner):
        """No findings → success."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result()

        cmd = self._make_command()
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._output_data["audit"]["total_findings"] == 0

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_findings_without_fail_on_returns_true(self, MockScanner):
        """Findings exist but no --fail-on → success (advisory only)."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=2,
            findings=[_make_finding(), _make_finding(vuln_id="CVE-2024-0002")],
        )

        cmd = self._make_command(fail_on=None)
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._output_data["audit"]["high"] == 2

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_fail_on_breached_returns_false(self, MockScanner):
        """--fail-on HIGH with HIGH findings → failure."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=1,
            findings=[_make_finding(severity="HIGH")],
        )

        cmd = self._make_command(fail_on="HIGH")
        assert cmd._execute_audit(Path("sbom.json")) is False
        assert any("gate failed" in e for e in cmd._errors)

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_fail_on_critical_passes_when_only_high(self, MockScanner):
        """--fail-on CRITICAL with only HIGH findings → pass."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=3,
            findings=[_make_finding(severity="HIGH")] * 3,
        )

        cmd = self._make_command(fail_on="CRITICAL")
        assert cmd._execute_audit(Path("sbom.json")) is True

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_ndjson_output_emits_findings(self, MockScanner):
        """In NDJSON mode, each finding is emitted as a data event."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            critical=1,
            findings=[_make_finding(severity="CRITICAL")],
        )

        cmd = self._make_command(output="ndjson")
        cmd.emit_ndjson = MagicMock()

        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd.emit_ndjson.call_count == 1
        payload = cmd.emit_ndjson.call_args[0][0]
        assert payload["event"] == "data"
        assert payload["audit_finding"]["severity"] == "CRITICAL"

    @patch("strata.commands.builders.sbom_build_command.CveScannerIntegration")
    def test_runtime_error_from_scanner(self, MockScanner):
        """Scanner raises RuntimeError → audit fails."""
        instance = MockScanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.side_effect = RuntimeError("scanner crashed")

        cmd = self._make_command()
        assert cmd._execute_audit(Path("sbom.json")) is False
        assert any("scanner crashed" in e for e in cmd._errors)
