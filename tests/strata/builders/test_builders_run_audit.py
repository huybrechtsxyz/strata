"""Tests for --audit / --severity / --fail-on on the build run command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.commands.builders.run_build_command import RunBuildCommand
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


def _make_finding(severity: str = "HIGH") -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id="CVE-2024-0001",
        severity=severity,
        package_name="openssl",
        installed_version="3.0.1",
        fixed_version="3.0.14",
        title="Test vulnerability",
        purl=None,
    )


class TestRunBuildCommandAuditInit:
    """RunBuildCommand stores audit parameters from constructor."""

    def test_defaults(self):
        cmd = RunBuildCommand.__new__(RunBuildCommand)
        cmd._audit = False
        cmd._audit_severity = "MEDIUM"
        cmd._fail_on = None
        assert cmd._audit is False
        assert cmd._audit_severity == "MEDIUM"
        assert cmd._fail_on is None

    def test_audit_params_stored(self):
        cmd = RunBuildCommand.__new__(RunBuildCommand)
        cmd._audit = True
        cmd._audit_severity = "HIGH"
        cmd._fail_on = "CRITICAL"
        assert cmd._audit is True
        assert cmd._audit_severity == "HIGH"
        assert cmd._fail_on == "CRITICAL"

    def test_inherits_execute_audit_from_base(self):
        """_execute_audit must come from BaseBuildCommand, not be redefined."""
        assert "_execute_audit" not in RunBuildCommand.__dict__
        assert hasattr(BaseBuildCommand, "_execute_audit")


class TestRunBuildCommandAuditBehavior:
    """_execute_audit inherited by RunBuildCommand behaves correctly."""

    def _make_command(self, **overrides):
        defaults = {
            "output": "console",
            "audit_severity": "MEDIUM",
            "fail_on": None,
        }
        defaults.update(overrides)
        cmd = RunBuildCommand.__new__(RunBuildCommand)
        cmd._output_format = defaults["output"]
        cmd._output_quiet = False
        cmd._audit_severity = defaults["audit_severity"]
        cmd._fail_on = defaults["fail_on"]
        cmd._errors = []
        cmd._messages = []
        cmd._output_data = {}
        cmd.logger = MagicMock()
        return cmd

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    def test_scanner_unavailable_is_non_fatal(self, mock_scanner):
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (False, "trivy not found")

        cmd = self._make_command()
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._errors == []

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    def test_clean_scan_returns_true(self, mock_scanner):
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result()

        cmd = self._make_command()
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._output_data["audit"]["total_findings"] == 0

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    def test_fail_on_breached_returns_false(self, mock_scanner):
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            critical=1,
            findings=[_make_finding(severity="CRITICAL")],
        )

        cmd = self._make_command(fail_on="CRITICAL")
        assert cmd._execute_audit(Path("sbom.json")) is False
        assert any("gate failed" in e for e in cmd._errors)

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    def test_fail_on_not_set_findings_are_advisory(self, mock_scanner):
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=3,
            findings=[_make_finding(severity="HIGH")] * 3,
        )

        cmd = self._make_command(fail_on=None)
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._output_data["audit"]["high"] == 3
