"""Tests for CycloneDX VEX and SARIF audit report writers."""

import json

from strata.models.sbom_model import CveAuditResultModel, CveFindingModel
from strata.utils.audit_report import write_sarif, write_vex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    vuln_id: str = "CVE-2024-0001",
    severity: str = "HIGH",
    pkg: str = "openssl",
    version: str = "3.0.1",
    fixed: str | None = "3.0.14",
    title: str | None = "Buffer overflow",
    purl: str | None = "pkg:deb/openssl@3.0.1",
) -> CveFindingModel:
    return CveFindingModel(
        vulnerability_id=vuln_id,
        severity=severity,
        package_name=pkg,
        installed_version=version,
        fixed_version=fixed,
        title=title,
        purl=purl,
    )


def _make_result(findings: list[CveFindingModel] | None = None) -> CveAuditResultModel:
    if findings is None:
        findings = []
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    for f in findings:
        if f.severity in counts:
            counts[f.severity] += 1
    return CveAuditResultModel(
        scanner="trivy",
        scanner_version="0.52.0",
        sbom_path="sbom.json",
        total_findings=len(findings),
        critical=counts["CRITICAL"],
        high=counts["HIGH"],
        medium=counts["MEDIUM"],
        low=counts["LOW"],
        unknown=counts["UNKNOWN"],
        findings=findings,
    )


# ===========================================================================
# VEX tests
# ===========================================================================


class TestWriteVex:
    def test_creates_file(self, tmp_path):
        result = _make_result([_make_finding()])
        path = write_vex(result, tmp_path, strata_version="0.15.0")
        assert path == tmp_path / "vex.json"
        assert path.exists()

    def test_valid_json(self, tmp_path):
        result = _make_result([_make_finding()])
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["bomFormat"] == "CycloneDX"
        assert doc["specVersion"] == "1.6"

    def test_vulnerabilities_populated(self, tmp_path):
        findings = [
            _make_finding(vuln_id="CVE-2024-0001", severity="CRITICAL"),
            _make_finding(vuln_id="CVE-2024-0002", severity="HIGH", pkg="curl"),
        ]
        result = _make_result(findings)
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        vulns = doc["vulnerabilities"]
        assert len(vulns) == 2
        assert vulns[0]["id"] == "CVE-2024-0001"
        assert vulns[0]["ratings"][0]["severity"] == "critical"
        assert vulns[1]["id"] == "CVE-2024-0002"

    def test_recommendation_from_fixed_version(self, tmp_path):
        result = _make_result([_make_finding(fixed="3.0.14")])
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["vulnerabilities"][0]["recommendation"] == "Upgrade to 3.0.14"

    def test_no_recommendation_when_no_fix(self, tmp_path):
        result = _make_result([_make_finding(fixed=None)])
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert "recommendation" not in doc["vulnerabilities"][0]

    def test_affects_contains_purl(self, tmp_path):
        result = _make_result([_make_finding(purl="pkg:deb/openssl@3.0.1")])
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        affects = doc["vulnerabilities"][0]["affects"]
        assert any(a.get("ref") == "pkg:deb/openssl@3.0.1" for a in affects)

    def test_empty_findings(self, tmp_path):
        result = _make_result([])
        path = write_vex(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["vulnerabilities"] == []

    def test_tools_include_scanner(self, tmp_path):
        result = _make_result([_make_finding()])
        path = write_vex(result, tmp_path, strata_version="0.15.0")
        doc = json.loads(path.read_text(encoding="utf-8"))
        tool_names = [t["name"] for t in doc["metadata"]["tools"]]
        assert "strata" in tool_names
        assert "trivy" in tool_names

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        result = _make_result([])
        path = write_vex(result, nested)
        assert path.exists()


# ===========================================================================
# SARIF tests
# ===========================================================================


class TestWriteSarif:
    def test_creates_file(self, tmp_path):
        result = _make_result([_make_finding()])
        path = write_sarif(result, tmp_path, strata_version="0.15.0")
        assert path == tmp_path / "audit.sarif"
        assert path.exists()

    def test_valid_json_with_schema(self, tmp_path):
        result = _make_result([_make_finding()])
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["version"] == "2.1.0"
        assert "$schema" in doc

    def test_results_populated(self, tmp_path):
        findings = [
            _make_finding(vuln_id="CVE-2024-0001", severity="CRITICAL"),
            _make_finding(vuln_id="CVE-2024-0002", severity="LOW", pkg="zlib"),
        ]
        result = _make_result(findings)
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        run = doc["runs"][0]
        assert len(run["results"]) == 2
        assert run["results"][0]["ruleId"] == "CVE-2024-0001"
        assert run["results"][1]["ruleId"] == "CVE-2024-0002"

    def test_severity_mapping(self, tmp_path):
        findings = [
            _make_finding(vuln_id="CVE-C", severity="CRITICAL"),
            _make_finding(vuln_id="CVE-H", severity="HIGH"),
            _make_finding(vuln_id="CVE-M", severity="MEDIUM"),
            _make_finding(vuln_id="CVE-L", severity="LOW"),
            _make_finding(vuln_id="CVE-U", severity="UNKNOWN"),
        ]
        result = _make_result(findings)
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        levels = [r["level"] for r in doc["runs"][0]["results"]]
        assert levels == ["error", "error", "warning", "note", "note"]

    def test_rules_deduplicated(self, tmp_path):
        """Same CVE affecting two packages should produce one rule."""
        findings = [
            _make_finding(vuln_id="CVE-2024-0001", pkg="openssl"),
            _make_finding(vuln_id="CVE-2024-0001", pkg="libssl"),
        ]
        result = _make_result(findings)
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 1
        assert rules[0]["id"] == "CVE-2024-0001"
        # But 2 results
        assert len(doc["runs"][0]["results"]) == 2

    def test_message_includes_package_and_fix(self, tmp_path):
        result = _make_result([_make_finding(pkg="openssl", version="3.0.1", fixed="3.0.14")])
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        msg = doc["runs"][0]["results"][0]["message"]["text"]
        assert "openssl@3.0.1" in msg
        assert "3.0.14" in msg

    def test_sbom_path_in_location(self, tmp_path):
        sbom = tmp_path / "sbom.json"
        result = _make_result([_make_finding()])
        path = write_sarif(result, tmp_path, sbom_path=sbom)
        doc = json.loads(path.read_text(encoding="utf-8"))
        uri = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert uri == "sbom.json"

    def test_empty_findings(self, tmp_path):
        result = _make_result([])
        path = write_sarif(result, tmp_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["runs"][0]["results"] == []
        assert doc["runs"][0]["tool"]["driver"]["rules"] == []

    def test_tool_driver_info(self, tmp_path):
        result = _make_result([])
        path = write_sarif(result, tmp_path, strata_version="0.15.0")
        doc = json.loads(path.read_text(encoding="utf-8"))
        driver = doc["runs"][0]["tool"]["driver"]
        assert driver["name"] == "strata-cve-audit"
        assert driver["version"] == "0.15.0"
