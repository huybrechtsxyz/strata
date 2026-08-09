"""Integration tests for CveScannerIntegration.

These tests require Trivy or Grype to be installed in PATH.
They are automatically skipped when no scanner is available.

The SBOM fixture (tests/data/sbom/sbom_vulnerable.json) contains deliberately
outdated packages with known public CVEs so a real scanner always returns findings:
  - openssl 1.0.1e   → CVE-2014-0160 (Heartbleed) and many others
  - log4j-core 2.14.1 → CVE-2021-44228 (Log4Shell)
  - lodash 4.17.15   → CVE-2021-23337 (prototype pollution)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from strata.integrations.cve_scanner import CveScannerIntegration
from strata.models.integration_model import IntegrationModel

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_SCANNER_AVAILABLE = shutil.which("trivy") is not None or shutil.which("grype") is not None

pytestmark = pytest.mark.skipif(
    not _SCANNER_AVAILABLE,
    reason="No CVE scanner (trivy or grype) found in PATH — skipping integration tests",
)

_FIXTURE_SBOM = Path(__file__).parent.parent.parent / "data" / "sbom" / "sbom_vulnerable.json"


@pytest.fixture()
def scanner() -> CveScannerIntegration:
    cfg = IntegrationModel(name="cve_scanner", type="cve_scanner")
    CveScannerIntegration._instances.clear()
    return CveScannerIntegration(config=cfg)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestCveScannerAvailability:
    def test_backend_detected(self, scanner: CveScannerIntegration) -> None:
        assert scanner.backend in ("trivy", "grype")

    def test_is_available(self, scanner: CveScannerIntegration) -> None:
        assert scanner.is_available() is True

    def test_ensure_available_returns_true(self, scanner: CveScannerIntegration) -> None:
        ok, msg = scanner.ensure_available()
        assert ok is True
        assert msg == ""

    def test_version_non_empty(self, scanner: CveScannerIntegration) -> None:
        version = scanner.get_version()
        assert version is not None
        assert len(version) > 0

    def test_capability_registered(self, scanner: CveScannerIntegration) -> None:
        from strata.models.capabilities import ICveScanner

        caps = getattr(scanner, "CAPABILITIES", [])
        assert ICveScanner in caps


# ---------------------------------------------------------------------------
# Scan with known-vulnerable SBOM fixture
# ---------------------------------------------------------------------------


class TestCveScannerRealScan:
    def test_fixture_sbom_exists(self) -> None:
        assert _FIXTURE_SBOM.exists(), f"Fixture SBOM not found: {_FIXTURE_SBOM}"

    def test_scan_returns_findings(self, scanner: CveScannerIntegration) -> None:
        """Real scan against a deliberately vulnerable SBOM must return ≥1 finding."""
        result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="LOW")
        assert result.total_findings > 0, (
            "Expected at least one CVE finding for the known-vulnerable fixture SBOM "
            "(openssl 1.0.1e, log4j-core 2.14.1, lodash 4.17.15). "
            f"Got total_findings={result.total_findings}. Check that the scanner has an up-to-date DB."
        )

    def test_scan_findings_have_required_fields(self, scanner: CveScannerIntegration) -> None:
        result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="LOW")
        assert result.findings, "Expected at least one finding"
        for f in result.findings:
            assert f.vulnerability_id, "Each finding must have a vulnerability_id (CVE ID)"
            assert f.severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"), f"Unexpected severity: {f.severity}"
            assert f.package_name, "Each finding must have a package_name"

    def test_scan_severity_threshold_filters_results(self, scanner: CveScannerIntegration) -> None:
        """HIGH threshold must return fewer (or equal) findings than LOW threshold."""
        low_result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="LOW")
        high_result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="HIGH")
        assert high_result.total_findings <= low_result.total_findings, (
            "Stricter severity threshold should not produce more findings than a looser one"
        )

    def test_scan_severity_counts_consistent(self, scanner: CveScannerIntegration) -> None:
        result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="LOW")
        total_from_counts = result.critical + result.high + result.medium + result.low + result.unknown
        assert total_from_counts == result.total_findings, (
            f"severity counts sum ({total_from_counts}) must equal total_findings ({result.total_findings})"
        )

    def test_scan_high_threshold_excludes_low(self, scanner: CveScannerIntegration) -> None:
        """With HIGH threshold, no LOW findings should appear in the list."""
        result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="HIGH")
        low_findings = [f for f in result.findings if f.severity == "LOW"]
        assert low_findings == [], (
            f"Expected no LOW findings when threshold=HIGH, got: {[f.vulnerability_id for f in low_findings]}"
        )

    def test_scan_critical_severity_detected(self, scanner: CveScannerIntegration) -> None:
        """The fixture SBOM must trigger at least one CRITICAL CVE (openssl 1.0.1e / log4j)."""
        result = scanner.scan_sbom(_FIXTURE_SBOM, severity_threshold="CRITICAL")
        assert result.critical > 0, (
            "Expected at least one CRITICAL CVE for openssl 1.0.1e or log4j-core 2.14.1. "
            "Ensure the scanner vulnerability database is up to date."
        )
