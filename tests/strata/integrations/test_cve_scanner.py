"""Tests for the CVE scanner integration."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from strata.integrations.cve_scanner import CveScannerIntegration, _detect_backend
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult


@pytest.fixture()
def config():
    return IntegrationModel(name="cve_scanner", type="cve_scanner")


TRIVY_JSON = json.dumps(
    {
        "Results": [
            {
                "Target": "sbom.json",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-0001",
                        "Severity": "CRITICAL",
                        "PkgName": "openssl",
                        "InstalledVersion": "3.0.1",
                        "FixedVersion": "3.0.14",
                        "Title": "Buffer overflow in openssl",
                    },
                    {
                        "VulnerabilityID": "CVE-2024-0002",
                        "Severity": "HIGH",
                        "PkgName": "curl",
                        "InstalledVersion": "7.81.0",
                        "FixedVersion": "7.88.0",
                        "Title": "Use after free in curl",
                    },
                    {
                        "VulnerabilityID": "CVE-2024-0003",
                        "Severity": "LOW",
                        "PkgName": "zlib",
                        "InstalledVersion": "1.2.11",
                        "FixedVersion": None,
                        "Title": "Minor info leak",
                    },
                ],
            }
        ]
    }
)

GRYPE_JSON = json.dumps(
    {
        "matches": [
            {
                "vulnerability": {
                    "id": "CVE-2024-1000",
                    "severity": "Critical",
                    "description": "Remote code execution",
                    "fix": {"versions": ["2.0.1"]},
                },
                "artifact": {
                    "name": "lodash",
                    "version": "4.17.20",
                    "purl": "pkg:npm/lodash@4.17.20",
                },
            },
            {
                "vulnerability": {
                    "id": "CVE-2024-1001",
                    "severity": "Medium",
                    "description": "XSS vulnerability",
                    "fix": {"versions": []},
                },
                "artifact": {
                    "name": "express",
                    "version": "4.17.1",
                },
            },
        ]
    }
)


class TestDetectBackend:
    def test_prefers_trivy(self):
        with patch("strata.integrations.cve_scanner.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/trivy" if cmd == "trivy" else None
            assert _detect_backend() == "trivy"

    def test_falls_back_to_grype(self):
        with patch("strata.integrations.cve_scanner.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/grype" if cmd == "grype" else None
            assert _detect_backend() == "grype"

    def test_returns_none_when_nothing_found(self):
        with patch("strata.integrations.cve_scanner.shutil.which", return_value=None):
            assert _detect_backend() is None


class TestCveScannerInit:
    @patch("strata.integrations.cve_scanner._detect_backend", return_value="trivy")
    @patch("strata.integrations.base_integration.BaseIntegration.__init__", return_value=None)
    def test_backend_set_to_trivy(self, _base_init, _detect, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        assert scanner.backend == "trivy"

    @patch("strata.integrations.cve_scanner._detect_backend", return_value="grype")
    @patch("strata.integrations.base_integration.BaseIntegration.__init__", return_value=None)
    def test_backend_set_to_grype(self, _base_init, _detect, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "grype"
        assert scanner.backend == "grype"

    @patch("strata.integrations.cve_scanner._detect_backend", return_value=None)
    @patch("strata.integrations.base_integration.BaseIntegration.__init__", return_value=None)
    def test_backend_none_when_unavailable(self, _base_init, _detect, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = None
        assert scanner.backend is None


class TestEnsureAvailable:
    def test_unavailable_when_no_backend(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = None
        ok, msg = scanner.ensure_available()
        assert not ok
        assert "No CVE scanner found" in msg

    @patch.object(CveScannerIntegration, "is_available", return_value=False)
    def test_unavailable_when_not_in_path(self, _is_avail, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner.command = "trivy"
        ok, msg = scanner.ensure_available()
        assert not ok
        assert "not available in PATH" in msg


class TestParseTrivyOutput:
    def test_parses_findings(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner.command = "trivy"
        scanner._version = "0.52.0"

        result = scanner._parse_trivy_output(TRIVY_JSON, Path("sbom.json"))

        assert result.total_findings == 3
        assert result.critical == 1
        assert result.high == 1
        assert result.low == 1
        assert result.findings[0].vulnerability_id == "CVE-2024-0001"
        assert result.findings[0].severity == "CRITICAL"
        assert result.findings[0].fixed_version == "3.0.14"

    def test_handles_invalid_json(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner._version = "0.52.0"

        result = scanner._parse_trivy_output("not json", Path("sbom.json"))
        assert result.total_findings == 0

    def test_handles_empty_results(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner._version = "0.52.0"

        result = scanner._parse_trivy_output(json.dumps({"Results": []}), Path("sbom.json"))
        assert result.total_findings == 0


class TestParseGrypeOutput:
    def test_parses_findings(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "grype"
        scanner._version = "0.74.0"

        result = scanner._parse_grype_output(GRYPE_JSON, Path("sbom.json"), "MEDIUM")

        assert result.total_findings == 2
        assert result.critical == 1
        assert result.medium == 1
        assert result.findings[0].vulnerability_id == "CVE-2024-1000"
        assert result.findings[0].purl == "pkg:npm/lodash@4.17.20"

    def test_severity_threshold_filters(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "grype"
        scanner._version = "0.74.0"

        result = scanner._parse_grype_output(GRYPE_JSON, Path("sbom.json"), "CRITICAL")
        assert result.total_findings == 1
        assert result.findings[0].severity == "CRITICAL"

    def test_handles_invalid_json(self, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "grype"
        scanner._version = "0.74.0"

        result = scanner._parse_grype_output("bad json", Path("sbom.json"), "MEDIUM")
        assert result.total_findings == 0


class TestSeveritiesAtOrAbove:
    def test_critical(self):
        assert CveScannerIntegration._severities_at_or_above("CRITICAL") == ["CRITICAL"]

    def test_high(self):
        assert CveScannerIntegration._severities_at_or_above("HIGH") == ["CRITICAL", "HIGH"]

    def test_medium(self):
        assert CveScannerIntegration._severities_at_or_above("MEDIUM") == [
            "CRITICAL",
            "HIGH",
            "MEDIUM",
        ]

    def test_low(self):
        assert CveScannerIntegration._severities_at_or_above("LOW") == [
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
        ]

    def test_unknown_threshold_returns_all(self):
        assert CveScannerIntegration._severities_at_or_above("BOGUS") == [
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "UNKNOWN",
        ]


class TestScanSbom:
    @patch.object(CveScannerIntegration, "ensure_available", return_value=(False, "not found"))
    def test_raises_when_unavailable(self, _ensure, config):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = None
        with pytest.raises(RuntimeError, match="not found"):
            scanner.scan_sbom(Path("sbom.json"))

    @patch.object(CveScannerIntegration, "_run_integration")
    @patch.object(CveScannerIntegration, "ensure_available", return_value=(True, ""))
    def test_trivy_scan(self, _ensure, mock_run, config):
        mock_run.return_value = CommandResult(
            returncode=0, stdout=TRIVY_JSON, stderr="", command="trivy", duration_ms=100.0
        )

        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner.command = "trivy"
        scanner._version = "0.52.0"

        result = scanner.scan_sbom(Path("sbom.json"), severity_threshold="MEDIUM")
        assert result.total_findings == 3
        assert result.scanner == "trivy"

    @patch.object(CveScannerIntegration, "_run_integration")
    @patch.object(CveScannerIntegration, "ensure_available", return_value=(True, ""))
    def test_grype_scan(self, _ensure, mock_run, config):
        mock_run.return_value = CommandResult(
            returncode=0, stdout=GRYPE_JSON, stderr="", command="grype", duration_ms=100.0
        )

        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "grype"
        scanner.command = "grype"
        scanner._version = "0.74.0"

        result = scanner.scan_sbom(Path("sbom.json"), severity_threshold="MEDIUM")
        assert result.total_findings == 2
        assert result.scanner == "grype"

    @patch.object(CveScannerIntegration, "_run_integration")
    @patch.object(CveScannerIntegration, "ensure_available", return_value=(True, ""))
    def test_trivy_scan_failure_raises(self, _ensure, mock_run, config):
        mock_run.return_value = CommandResult(
            returncode=1, stdout="", stderr="crash", command="trivy", duration_ms=50.0
        )

        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        scanner._backend = "trivy"
        scanner.command = "trivy"

        with pytest.raises(RuntimeError, match="Trivy scan failed"):
            scanner.scan_sbom(Path("sbom.json"))


class TestParseVersion:
    def test_trivy_format(self):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        assert scanner.parse_version("Version: 0.52.0") == "0.52.0"

    def test_grype_format(self):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        assert scanner.parse_version("grype 0.74.0") == "0.74.0"

    def test_unknown_format(self):
        scanner = CveScannerIntegration.__new__(CveScannerIntegration)
        assert scanner.parse_version("unknown") == "unknown"


class TestCveScannerCapability:
    def test_icve_scanner_in_capabilities(self) -> None:
        from strata.integrations.capabilities import ICveScanner

        caps = CveScannerIntegration.CAPABILITIES
        assert ICveScanner in caps, "CveScannerIntegration.CAPABILITIES must include ICveScanner"

    def test_cve_scanner_in_factory_known_types(self) -> None:
        from strata.integrations.factory import IntegrationFactory

        assert "cve_scanner" in IntegrationFactory.get_known_types()

    def test_tools_status_row_includes_icve_scanner(self) -> None:
        """ToolsController.status() must return ICveScanner in caps for cve_scanner."""
        from strata.integrations.factory import IntegrationFactory

        integration = IntegrationFactory.create_by_type("cve_scanner")
        caps = [c.__name__ for c in (integration.CAPABILITIES if hasattr(integration, "CAPABILITIES") else [])]
        assert "ICveScanner" in caps
