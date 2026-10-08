#!/usr/bin/env python3
"""Tests for `CveScannerIntegration` (docs/archive/cve-scanner-integration.md)."""

import json
from pathlib import Path

import pytest

import strata.integrations.cve_scanner as cve_scanner_module
from strata.integrations.capabilities import ScannerIntegration
from strata.integrations.cve_scanner import CveScannerIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability
from strata.utils.transport import CommandResult


def test_class_declares_its_contract():
    assert CveScannerIntegration.TYPE == "cve_scanner"
    assert CveScannerIntegration.CAPABILITIES == {Capability.SCANNER}
    assert CveScannerIntegration.TRANSPORTS == {"cli"}
    assert issubclass(CveScannerIntegration, ScannerIntegration)


def _patch_which(monkeypatch, available: set[str]):
    """Stub `shutil.which` where `CveScannerIntegration` actually looks it up."""

    def _fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    monkeypatch.setattr(cve_scanner_module.shutil, "which", _fake_which)


def _patch_run(monkeypatch, result: CommandResult):
    """Stub `run_command` where `Integration.run()` actually looks it up
    (`strata.integrations.base`), matching `test_integrations_terraform.py`'s
    own precedent. Records every call's argv (not just the last) — `scan_sbom()`
    calls `run()` twice (the scan itself, then `get_version()` inside
    `_summarize()`), so a single overwritten slot would silently assert
    against the wrong call."""
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return result

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    return calls


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


def test_prefers_trivy_when_both_are_available(monkeypatch):
    _patch_which(monkeypatch, {"trivy", "grype"})
    scanner = CveScannerIntegration()
    assert scanner.command == "trivy"


def test_falls_back_to_grype_when_only_grype_is_available(monkeypatch):
    _patch_which(monkeypatch, {"grype"})
    scanner = CveScannerIntegration()
    assert scanner.command == "grype"


def test_command_is_none_when_neither_is_available(monkeypatch):
    _patch_which(monkeypatch, set())
    scanner = CveScannerIntegration()
    assert scanner.command is None
    assert scanner.is_available() is False


def test_ensure_available_raises_when_neither_is_installed(monkeypatch):
    _patch_which(monkeypatch, set())
    scanner = CveScannerIntegration()
    with pytest.raises(IntegrationError, match="no CVE scanner found on PATH"):
        scanner.ensure_available()


def test_ensure_available_is_a_noop_when_a_backend_is_installed(monkeypatch):
    _patch_which(monkeypatch, {"trivy"})
    CveScannerIntegration().ensure_available()  # does not raise


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Version: 0.50.1\nVulnerability DB: ...", "0.50.1"),
        ("0.74.6", "0.74.6"),
        ("garbage", "garbage"),
    ],
)
def test_parse_version(raw, expected):
    assert CveScannerIntegration().parse_version(raw) == expected


def test_get_version_uses_dashdash_version_for_trivy(monkeypatch):
    _patch_which(monkeypatch, {"trivy"})
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout="Version: 0.50.1", stderr=""))
    version = CveScannerIntegration().get_version()
    assert version == "0.50.1"
    assert captured[0] == ["trivy", "--version"]


def test_get_version_uses_bare_version_subcommand_for_grype(monkeypatch):
    _patch_which(monkeypatch, {"grype"})
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout="0.74.6", stderr=""))
    version = CveScannerIntegration().get_version()
    assert version == "0.74.6"
    assert captured[0] == ["grype", "version"]


def test_get_version_is_none_without_a_backend(monkeypatch):
    _patch_which(monkeypatch, set())
    assert CveScannerIntegration().get_version() is None


# ---------------------------------------------------------------------------
# scan_sbom — Trivy
# ---------------------------------------------------------------------------

_TRIVY_JSON = json.dumps(
    {
        "Results": [
            {
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-1111",
                        "Severity": "CRITICAL",
                        "PkgName": "openssl",
                        "InstalledVersion": "1.1.1",
                        "FixedVersion": "1.1.1n",
                        "Title": "a critical openssl issue",
                        "PkgIdentifier": {"PURL": "pkg:deb/openssl@1.1.1"},
                    },
                    {
                        "VulnerabilityID": "CVE-2024-2222",
                        "Severity": "LOW",
                        "PkgName": "curl",
                        "InstalledVersion": "7.0",
                    },
                ]
            }
        ]
    }
)


def test_scan_sbom_with_trivy_parses_findings(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, {"trivy"})
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_TRIVY_JSON, stderr=""))
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    result = CveScannerIntegration().scan_sbom(sbom_path, severity_threshold="LOW")

    assert result.scanner == "trivy"
    assert result.total_findings == 2
    assert result.critical == 1
    assert result.low == 1
    assert captured[0] == [
        "trivy",
        "sbom",
        str(sbom_path),
        "--format",
        "json",
        "--severity",
        "CRITICAL,HIGH,MEDIUM,LOW",
        "--exit-code",
        "0",
    ]
    finding = next(f for f in result.findings if f.vulnerability_id == "CVE-2024-1111")
    assert finding.fixed_version == "1.1.1n"
    assert finding.purl == "pkg:deb/openssl@1.1.1"


def test_scan_sbom_with_trivy_raises_on_scan_failure(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, {"trivy"})
    _patch_run(monkeypatch, CommandResult(returncode=1, stdout="", stderr="boom"))
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    with pytest.raises(IntegrationError, match="boom"):
        CveScannerIntegration().scan_sbom(sbom_path)


def test_scan_sbom_raises_when_no_backend_is_available(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, set())
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    with pytest.raises(IntegrationError, match="no CVE scanner found on PATH"):
        CveScannerIntegration().scan_sbom(sbom_path)


def test_scan_sbom_raises_a_clean_integration_error_on_malformed_json(monkeypatch, tmp_path: Path):
    """Malformed scanner output must not let json.JSONDecodeError escape uncaught —
    evaluate_cve_policy() only catches IntegrationError, and an uncaught parser
    exception would crash 'build run' instead of degrading via on_missing_data."""
    _patch_which(monkeypatch, {"trivy"})
    _patch_run(monkeypatch, CommandResult(returncode=0, stdout="not json at all", stderr=""))
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    with pytest.raises(IntegrationError, match="unparseable output"):
        CveScannerIntegration().scan_sbom(sbom_path)


def test_scan_sbom_raises_a_clean_integration_error_on_unexpected_json_shape(monkeypatch, tmp_path: Path):
    """Valid JSON that's missing an expected key (e.g. a schema drift) must also
    degrade to IntegrationError, not an uncaught KeyError."""
    _patch_which(monkeypatch, {"trivy"})
    _patch_run(
        monkeypatch,
        CommandResult(returncode=0, stdout=json.dumps({"Results": [{"Vulnerabilities": [{}]}]}), stderr=""),
    )
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    with pytest.raises(IntegrationError, match="unparseable output"):
        CveScannerIntegration().scan_sbom(sbom_path)


# ---------------------------------------------------------------------------
# scan_sbom — Grype (no --severity flag; filtered in Python, NEGLIGIBLE->LOW)
# ---------------------------------------------------------------------------

_GRYPE_JSON = json.dumps(
    {
        "matches": [
            {
                "vulnerability": {
                    "id": "CVE-2024-3333",
                    "severity": "High",
                    "description": "a high severity issue",
                    "fix": {"versions": ["2.0.1"]},
                },
                "artifact": {"name": "libfoo", "version": "2.0.0", "purl": "pkg:deb/libfoo@2.0.0"},
            },
            {
                "vulnerability": {"id": "CVE-2024-4444", "severity": "Negligible"},
                "artifact": {"name": "libbar", "version": "1.0.0"},
            },
        ]
    }
)


def test_scan_sbom_with_grype_parses_and_remaps_negligible_to_low(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, {"grype"})
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_GRYPE_JSON, stderr=""))
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    result = CveScannerIntegration().scan_sbom(sbom_path, severity_threshold="LOW")

    assert result.scanner == "grype"
    assert captured[0] == ["grype", "sbom:" + str(sbom_path), "--output", "json"]
    assert result.total_findings == 2
    assert result.high == 1
    assert result.low == 1  # NEGLIGIBLE remapped to LOW
    fixed = next(f for f in result.findings if f.vulnerability_id == "CVE-2024-3333")
    assert fixed.fixed_version == "2.0.1"
    assert fixed.purl == "pkg:deb/libfoo@2.0.0"


def test_scan_sbom_with_grype_filters_in_python_by_severity_threshold(monkeypatch, tmp_path: Path):
    """Grype has no --severity flag (v1 evidence) — filtering happens here, in Python."""
    _patch_which(monkeypatch, {"grype"})
    _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_GRYPE_JSON, stderr=""))
    sbom_path = tmp_path / "sbom.json"
    sbom_path.write_text("{}")

    result = CveScannerIntegration().scan_sbom(sbom_path, severity_threshold="HIGH")

    assert result.total_findings == 1  # the remapped-to-LOW finding is filtered out
    assert result.high == 1
    assert result.low == 0
