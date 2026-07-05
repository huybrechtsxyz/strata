"""Tests for --audit / --severity / --fail-on on the build run command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.commands.builders.run_build_command import RunBuildCommand
from strata.models.sbom_model import CveAllowedEntryModel, CveAuditResultModel, CveFindingModel


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
        cmd._work_path = Path("/tmp/fake-workspace")
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


class TestCveAllowlist:
    """Tests for CVE allowlist (.strata/cve-allowed.yaml) filtering."""

    def _make_command(self, **overrides):
        defaults = {
            "output": "console",
            "audit_severity": "MEDIUM",
            "fail_on": "HIGH",
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
        cmd._work_path = Path("/tmp/fake-workspace")
        cmd.logger = MagicMock()
        return cmd

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    @patch.object(BaseBuildCommand, "_load_cve_allowed")
    def test_allowed_cve_suppressed_from_gate(self, mock_load, mock_scanner):
        """An allowed CVE is filtered out before the fail-on check."""
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=1,
            findings=[_make_finding(severity="HIGH")],
        )
        mock_load.return_value = [
            CveAllowedEntryModel(id="CVE-2024-0001", reason="Accepted risk"),
        ]

        cmd = self._make_command(fail_on="HIGH")
        assert cmd._execute_audit(Path("sbom.json")) is True
        assert cmd._output_data["audit"]["total_findings"] == 0
        assert cmd._output_data["audit"]["high"] == 0

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    @patch.object(BaseBuildCommand, "_load_cve_allowed")
    def test_allowed_with_package_scope(self, mock_load, mock_scanner):
        """Allowlist entry scoped to a specific package only suppresses that package."""
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=2,
            findings=[
                CveFindingModel(
                    vulnerability_id="CVE-2024-0001",
                    severity="HIGH",
                    package_name="openssl",
                    installed_version="3.0.1",
                    fixed_version="3.0.14",
                ),
                CveFindingModel(
                    vulnerability_id="CVE-2024-0001",
                    severity="HIGH",
                    package_name="curl",
                    installed_version="7.81.0",
                    fixed_version="7.88.0",
                ),
            ],
        )
        mock_load.return_value = [
            CveAllowedEntryModel(id="CVE-2024-0001", reason="Only openssl", package="openssl"),
        ]

        cmd = self._make_command(fail_on="HIGH")
        # Only curl remains — still breaches gate
        assert cmd._execute_audit(Path("sbom.json")) is False
        assert cmd._output_data["audit"]["total_findings"] == 1
        assert cmd._output_data["audit"]["high"] == 1

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    @patch.object(BaseBuildCommand, "_load_cve_allowed")
    def test_expired_entry_not_suppressed(self, mock_load, mock_scanner):
        """An expired allowlist entry does not suppress the finding."""
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            high=1,
            findings=[_make_finding(severity="HIGH")],
        )
        mock_load.return_value = [
            CveAllowedEntryModel(id="CVE-2024-0001", reason="Was accepted", expires="2020-01-01"),
        ]

        cmd = self._make_command(fail_on="HIGH")
        assert cmd._execute_audit(Path("sbom.json")) is False

    @patch("strata.commands.builders.base_build_command.CveScannerIntegration")
    @patch.object(BaseBuildCommand, "_load_cve_allowed")
    def test_no_allowlist_file_returns_empty(self, mock_load, mock_scanner):
        """When no cve-allowed.yaml exists, no filtering occurs."""
        instance = mock_scanner.return_value
        instance.ensure_available.return_value = (True, "")
        instance.scan_sbom.return_value = _make_result(
            critical=1,
            findings=[_make_finding(severity="CRITICAL")],
        )
        mock_load.return_value = []

        cmd = self._make_command(fail_on="CRITICAL")
        assert cmd._execute_audit(Path("sbom.json")) is False


class TestLoadCveAllowed:
    """Tests for BaseBuildCommand._load_cve_allowed file loading."""

    def test_missing_file_returns_empty(self, tmp_path):
        result = BaseBuildCommand._load_cve_allowed(tmp_path)
        assert result == []

    def test_valid_file_parsed(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        allowed_file = strata_dir / "cve-allowed.yaml"
        allowed_file.write_text(
            "allowed:\n"
            "  - id: CVE-2024-0001\n"
            '    reason: "Test reason"\n'
            "  - id: CVE-2024-0002\n"
            '    reason: "Another reason"\n'
            "    package: openssl\n"
            '    expires: "2099-12-31"\n'
        )
        result = BaseBuildCommand._load_cve_allowed(tmp_path)
        assert len(result) == 2
        assert result[0].id == "CVE-2024-0001"
        assert result[1].package == "openssl"
        assert result[1].expires == "2099-12-31"

    def test_invalid_yaml_returns_empty(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        allowed_file = strata_dir / "cve-allowed.yaml"
        allowed_file.write_text("{{invalid yaml")
        result = BaseBuildCommand._load_cve_allowed(tmp_path)
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        allowed_file = strata_dir / "cve-allowed.yaml"
        allowed_file.write_text("")
        result = BaseBuildCommand._load_cve_allowed(tmp_path)
        assert result == []
