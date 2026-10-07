#!/usr/bin/env python3
"""Tests for `CheckovIntegration` (docs/work/checkov-integration.md)."""

import json
from pathlib import Path

import pytest

from strata.integrations.capabilities import ScannerIntegration
from strata.integrations.checkov import CheckovIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability
from strata.utils.transport import CommandResult


def test_class_declares_its_contract():
    assert CheckovIntegration.TYPE == "checkov"
    assert CheckovIntegration.CAPABILITIES == {Capability.SCANNER}
    assert CheckovIntegration.TRANSPORTS == {"cli"}
    assert CheckovIntegration.COMMAND == "checkov"
    assert issubclass(CheckovIntegration, ScannerIntegration)


def _patch_which(monkeypatch, available: bool):
    """Stub `shutil.which` where `Integration.is_available()` actually looks it up."""

    def _fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if available else None

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module.shutil, "which", _fake_which)


def _patch_run(monkeypatch, result: CommandResult):
    """Stub `run_command` where `Integration.run()` actually looks it up,
    matching `test_integrations_cve_scanner.py`'s own precedent. Records
    every call's argv (not just the last) — `scan()` calls `run()` twice
    (the scan itself, then `get_version()`), so a single overwritten slot
    would silently assert against the wrong call.
    """
    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return result

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    return calls


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_command_is_none_when_not_available(monkeypatch):
    _patch_which(monkeypatch, False)
    assert CheckovIntegration().is_available() is False


def test_ensure_available_raises_when_not_installed(monkeypatch):
    _patch_which(monkeypatch, False)
    with pytest.raises(IntegrationError, match="checkov not found on PATH"):
        CheckovIntegration().ensure_available()


def test_ensure_available_is_a_noop_when_installed(monkeypatch):
    _patch_which(monkeypatch, True)
    CheckovIntegration().ensure_available()  # does not raise


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------

_CHECKOV_JSON = json.dumps(
    {
        "summary": {"passed": 10, "failed": 2, "skipped": 1},
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_20",
                    "check_name": "S3 Bucket has an ACL defined which allows public READ access",
                    "resource": "aws_s3_bucket.example",
                    "file_path": "/main.tf",
                    "file_line_range": [10, 15],
                    "severity": "high",
                    "guideline": "https://docs.checkov.io/CKV_AWS_20",
                },
                {
                    "check_id": "CKV_AWS_21",
                    "check_name": "another check",
                    "resource": "aws_s3_bucket.other",
                    "file_path": "/main.tf",
                },
            ]
        },
    }
)


def test_scan_parses_findings_and_summary(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, True)
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_CHECKOV_JSON, stderr=""))

    result = CheckovIntegration().scan(tmp_path, framework="terraform")

    assert result.passed == 10
    assert result.failed == 2
    assert result.skipped == 1
    assert len(result.findings) == 2
    assert captured[0] == [
        "checkov",
        "--directory",
        str(tmp_path),
        "--output",
        "json",
        "--framework",
        "terraform",
        "--compact",
        "--soft-fail",
    ]
    finding = next(f for f in result.findings if f.check_id == "CKV_AWS_20")
    assert finding.severity == "HIGH"
    assert finding.file_line_range == [10, 15]
    assert finding.guideline == "https://docs.checkov.io/CKV_AWS_20"
    # Missing optional fields on the second finding degrade cleanly.
    other = next(f for f in result.findings if f.check_id == "CKV_AWS_21")
    assert other.severity == "UNKNOWN"
    assert other.file_line_range is None


def test_scan_passes_include_checks_not_skip_checks_when_both_given(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, True)
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_CHECKOV_JSON, stderr=""))

    CheckovIntegration().scan(tmp_path, skip_checks=["CKV_1"], include_checks=["CKV_2"])

    assert "--check" in captured[0]
    assert "--skip-check" not in captured[0]


def test_scan_passes_skip_checks_when_include_checks_is_unset(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, True)
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_CHECKOV_JSON, stderr=""))

    CheckovIntegration().scan(tmp_path, skip_checks=["CKV_1", "CKV_2"])

    assert "--skip-check" in captured[0]
    assert captured[0][captured[0].index("--skip-check") + 1] == "CKV_1,CKV_2"


def test_scan_passes_custom_checks_dir(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, True)
    captured = _patch_run(monkeypatch, CommandResult(returncode=0, stdout=_CHECKOV_JSON, stderr=""))

    CheckovIntegration().scan(tmp_path, custom_checks_dir=".strata/checkov/custom/")

    assert "--external-checks-dir" in captured[0]
    assert captured[0][captured[0].index("--external-checks-dir") + 1] == ".strata/checkov/custom/"


def test_scan_raises_when_not_available(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, False)
    with pytest.raises(IntegrationError, match="checkov not found on PATH"):
        CheckovIntegration().scan(tmp_path)


def test_scan_raises_on_process_failure(monkeypatch, tmp_path: Path):
    _patch_which(monkeypatch, True)
    _patch_run(monkeypatch, CommandResult(returncode=1, stdout="", stderr="boom"))

    with pytest.raises(IntegrationError, match="boom"):
        CheckovIntegration().scan(tmp_path)


def test_scan_raises_a_clean_integration_error_on_malformed_json(monkeypatch, tmp_path: Path):
    """Malformed scanner output must not let json.JSONDecodeError escape uncaught —
    a future evaluator should only ever need to catch IntegrationError."""
    _patch_which(monkeypatch, True)
    _patch_run(monkeypatch, CommandResult(returncode=0, stdout="not json at all", stderr=""))

    with pytest.raises(IntegrationError, match="unparseable output"):
        CheckovIntegration().scan(tmp_path)


def test_scan_raises_a_clean_integration_error_on_unexpected_json_shape(monkeypatch, tmp_path: Path):
    """Valid JSON missing an expected key (schema drift) must also degrade
    to IntegrationError, not an uncaught KeyError."""
    _patch_which(monkeypatch, True)
    _patch_run(
        monkeypatch,
        CommandResult(returncode=0, stdout=json.dumps({"results": {"failed_checks": [{}]}}), stderr=""),
    )

    with pytest.raises(IntegrationError, match="unparseable output"):
        CheckovIntegration().scan(tmp_path)


def test_scan_version_is_unknown_when_get_version_fails(monkeypatch, tmp_path: Path):
    """`get_version()` calls `run()` a second time (VERSION_ARGS) — make
    that call fail and confirm `scan()` still degrades to 'unknown' rather
    than raising."""
    _patch_which(monkeypatch, True)

    import strata.integrations.base as base_module

    calls: list[list[str]] = []

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        if "--version" in args:
            return CommandResult(returncode=1, stdout="", stderr="no version")
        return CommandResult(returncode=0, stdout=_CHECKOV_JSON, stderr="")

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)

    result = CheckovIntegration().scan(tmp_path)

    assert result.scanner_version == "unknown"
