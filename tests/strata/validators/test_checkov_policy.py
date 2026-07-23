"""Tests for CheckovPolicy and CheckovIntegration JSON parsing.

Tests use subprocess mocking — no real Checkov binary required.
"""

import json
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

try:
    from strata.integrations.checkov import (
        CheckovFinding,
        CheckovIntegration,
        CheckovScanResult,
        _severity_index,
    )
    from strata.models.integration_model import IntegrationModel
    from strata.models.policy_model import PolicyModel
    from strata.validators.policies.base_policy import PolicyContext, PolicyResult
    from strata.validators.policies.checkov_policy import CheckovPolicy

    IMPL_MISSING = False
except ImportError:
    CheckovPolicy = None  # type: ignore[assignment,misc]
    CheckovIntegration = None  # type: ignore[assignment,misc]
    CheckovScanResult = None  # type: ignore[assignment,misc]
    CheckovFinding = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="CheckovPolicy not yet implemented")


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

_CHECKOV_SINGLE = {
    "check_type": "terraform",
    "results": {
        "passed_checks": [
            {
                "check_id": "CKV_AWS_23",
                "check_name": "Ensure logging is enabled",
                "resource": "aws_s3_bucket.logs",
                "file_path": "main.tf",
                "file_line_range": [1, 10],
                "severity": "LOW",
            },
        ],
        "failed_checks": [
            {
                "check_id": "CKV_AWS_144",
                "check_name": "Ensure S3 bucket versioning is enabled",
                "resource": "aws_s3_bucket.data",
                "file_path": "main.tf",
                "file_line_range": [20, 28],
                "severity": "HIGH",
            },
            {
                "check_id": "CKV_AWS_145",
                "check_name": "Ensure S3 bucket is encrypted",
                "resource": "aws_s3_bucket.data",
                "file_path": "main.tf",
                "file_line_range": [30, 40],
                "severity": "MEDIUM",
            },
        ],
        "skipped_checks": [],
    },
    "summary": {"passed": 1, "failed": 2, "skipped": 0},
}

_CHECKOV_CRITICAL = {
    "check_type": "terraform",
    "results": {
        "passed_checks": [],
        "failed_checks": [
            {
                "check_id": "CKV_AWS_1",
                "check_name": "Public S3",
                "resource": "aws_s3_bucket.pub",
                "file_path": "main.tf",
                "file_line_range": [1, 5],
                "severity": "CRITICAL",
            },
        ],
        "skipped_checks": [],
    },
    "summary": {"passed": 0, "failed": 1, "skipped": 0},
}

_CHECKOV_MULTI = [_CHECKOV_SINGLE, _CHECKOV_CRITICAL]


def _make_policy(severity_gate="high", skip_checks=None, enforcement="deny", include_checks=None):
    cfg = {"severity_gate": severity_gate}
    if skip_checks:
        cfg["skip_checks"] = skip_checks
    if include_checks:
        cfg["include_checks"] = include_checks
    return PolicyModel(name="test_checkov", type="checkov", phase="build", enforcement=enforcement, configuration=cfg)


def _make_context(build_path: Optional[Path] = None) -> "PolicyContext":
    return PolicyContext(phase="build", work_path=build_path, build_path=build_path)


def _make_integration() -> "CheckovIntegration":
    config = IntegrationModel(name="checkov", type="checkov")
    return CheckovIntegration(config)


# ===========================================================================
# CheckovIntegration — _parse_output
# ===========================================================================


class TestCheckovIntegrationParsing:
    def test_parse_single_block(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_SINGLE), "terraform", "/build/terraform")
        assert result.passed == 1
        assert result.failed == 2
        assert result.skipped == 0
        assert len(result.findings) == 2
        assert result.framework == "terraform"
        assert result.scanned_path == "/build/terraform"

    def test_parse_finding_fields(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_SINGLE), "terraform", "/build")
        high = next(f for f in result.findings if f.check_id == "CKV_AWS_144")
        assert high.severity == "HIGH"
        assert high.resource == "aws_s3_bucket.data"
        assert high.file_path == "main.tf"
        assert high.file_line_range == [20, 28]

    def test_parse_multi_block_list(self):
        integ = _make_integration()
        result = integ._parse_output(json.dumps(_CHECKOV_MULTI), "terraform", "/build")
        # single (1+2) + critical (0+1)
        assert result.passed == 1
        assert result.failed == 3
        assert len(result.findings) == 3

    def test_parse_empty_output_returns_empty_result(self):
        integ = _make_integration()
        result = integ._parse_output("", "terraform", "/build")
        assert result.failed == 0
        assert result.findings == []

    def test_parse_invalid_json_returns_empty(self):
        integ = _make_integration()
        result = integ._parse_output("not json", "terraform", "/build")
        assert result.findings == []

    def test_unknown_severity_normalised(self):
        block = {
            "results": {
                "passed_checks": [],
                "failed_checks": [
                    {
                        "check_id": "CKV_X_1",
                        "check_name": "foo",
                        "resource": "r",
                        "file_path": "f.tf",
                        "severity": "NOTAREAL",
                    },
                ],
                "skipped_checks": [],
            },
            "summary": {"passed": 0, "failed": 1, "skipped": 0},
        }
        integ = _make_integration()
        result = integ._parse_output(json.dumps(block), "terraform", "/build")
        assert result.findings[0].severity == "UNKNOWN"

    def test_missing_severity_defaults_to_unknown(self):
        block = {
            "results": {
                "passed_checks": [],
                "failed_checks": [
                    {"check_id": "CKV_X_2", "check_name": "bar", "resource": "r", "file_path": "f.tf"},
                ],
                "skipped_checks": [],
            },
            "summary": {"passed": 0, "failed": 1, "skipped": 0},
        }
        integ = _make_integration()
        result = integ._parse_output(json.dumps(block), "terraform", "/build")
        assert result.findings[0].severity == "UNKNOWN"


# ===========================================================================
# CheckovScanResult.findings_at_or_above
# ===========================================================================


class TestCheckovScanResultFiltering:
    def _make_result(self, severities):
        findings = [
            CheckovFinding(check_id=f"CKV_{i}", check_name="n", resource="r", file_path="f.tf", severity=sev)
            for i, sev in enumerate(severities)
        ]
        return CheckovScanResult(
            passed=0,
            failed=len(findings),
            skipped=0,
            findings=findings,
            scanner_version="3.0",
            framework="terraform",
            scanned_path="/b",
        )

    def test_at_or_above_high_returns_critical_and_high(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM", "LOW"])
        breaching = result.findings_at_or_above("HIGH")
        assert len(breaching) == 2
        severities = {f.severity for f in breaching}
        assert severities == {"CRITICAL", "HIGH"}

    def test_at_or_above_critical_only(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM"])
        breaching = result.findings_at_or_above("CRITICAL")
        assert len(breaching) == 1
        assert breaching[0].severity == "CRITICAL"

    def test_at_or_above_medium(self):
        result = self._make_result(["CRITICAL", "HIGH", "MEDIUM", "LOW"])
        breaching = result.findings_at_or_above("MEDIUM")
        assert len(breaching) == 3

    def test_no_findings_returns_empty(self):
        result = CheckovScanResult(
            passed=5, failed=0, skipped=0, findings=[], scanner_version="3", framework="terraform", scanned_path="/b"
        )
        assert result.findings_at_or_above("HIGH") == []

    def test_total_property(self):
        result = self._make_result(["HIGH", "MEDIUM"])
        result.passed = 3
        result.skipped = 1
        assert result.total == 6  # 3 + 2 + 1


# ===========================================================================
# _severity_index helper
# ===========================================================================


class TestSeverityIndex:
    def test_critical_is_lowest_index(self):
        assert _severity_index("CRITICAL") < _severity_index("HIGH")
        assert _severity_index("HIGH") < _severity_index("MEDIUM")
        assert _severity_index("MEDIUM") < _severity_index("LOW")

    def test_unknown_severity_maps_to_last(self):
        assert _severity_index("UNKNOWN") == _severity_index("UNKNOWN")
        assert _severity_index("GIBBERISH") == _severity_index("UNKNOWN")

    def test_case_insensitive(self):
        assert _severity_index("critical") == _severity_index("CRITICAL")
        assert _severity_index("High") == _severity_index("HIGH")


# ===========================================================================
# CheckovPolicy.evaluate
# ===========================================================================


class TestCheckovPolicyEvaluate:
    def _scan_result_from_block(self, block_json, framework="terraform"):
        integ = _make_integration()
        return integ._parse_output(json.dumps(block_json), framework, "/build/terraform")

    def _mock_policy_scan(self, scan_result):
        """Patch CheckovPolicy._run_scan to return a fixed result."""
        return patch.object(CheckovPolicy, "_run_scan", return_value=scan_result)

    def _mock_resolve_dir(self, path: Path):
        return patch.object(CheckovPolicy, "_resolve_terraform_dir", return_value=path)

    def test_skip_no_build_path(self):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=None)
        result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_skip_no_terraform_dir(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dir(None):
            context = _make_context(build_path=tmp_path)
            result = policy.evaluate(context)
        assert result.passed
        assert "no Terraform artifacts" in (result.details or {}).get("skipped", "")

    def test_skip_checkov_unavailable(self, tmp_path):
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(None):
            context = _make_context(build_path=tmp_path)
            result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_pass_no_findings_above_gate(self, tmp_path):
        scan = self._scan_result_from_block(
            {
                "results": {
                    "passed_checks": [
                        {
                            "check_id": "CKV_X",
                            "check_name": "ok",
                            "resource": "r",
                            "file_path": "f.tf",
                            "severity": "LOW",
                        }
                    ],
                    "failed_checks": [],
                    "skipped_checks": [],
                },
                "summary": {"passed": 1, "failed": 0, "skipped": 0},
            }
        )
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert result.passed
        assert result.violations == []
        assert result.details["passed"] == 1

    def test_fail_high_finding_above_gate(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 1
        assert "CKV_AWS_144" in result.violations[0]
        assert "[HIGH]" in result.violations[0]

    def test_fail_critical_finding(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_CRITICAL)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert "CKV_AWS_1" in result.violations[0]

    def test_medium_gate_catches_medium_findings(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="medium"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 2  # HIGH + MEDIUM

    def test_high_gate_ignores_medium_findings(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy(severity_gate="high"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert len(result.violations) == 1  # only HIGH

    def test_warn_enforcement_fails_but_does_not_deny(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_CRITICAL)
        policy = CheckovPolicy(_make_policy(severity_gate="high", enforcement="warn"))
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        assert not result.passed
        assert result.enforcement == "warn"

    def test_invalid_severity_gate_skips(self, tmp_path):
        policy = CheckovPolicy(_make_policy(severity_gate="extreme"))
        context = _make_context(tmp_path)
        with self._mock_resolve_dir(tmp_path / "terraform"):
            result = policy.evaluate(context)
        assert result.passed
        assert "invalid severity_gate" in (result.details or {}).get("skipped", "")

    def test_details_populated(self, tmp_path):
        scan = self._scan_result_from_block(_CHECKOV_SINGLE)
        policy = CheckovPolicy(_make_policy())
        with self._mock_resolve_dir(tmp_path / "terraform"), self._mock_policy_scan(scan):
            result = policy.evaluate(_make_context(tmp_path))
        d = result.details or {}
        assert d["scanner"] == "checkov"
        assert d["passed"] == 1
        assert d["failed"] == 2
        assert d["severity_gate"] == "HIGH"
        assert d["breaching_count"] == 1  # only the HIGH one (gate=high)


# ===========================================================================
# CheckovPolicy._resolve_terraform_dir
# ===========================================================================


class TestCheckovPolicyResolveDir:
    def test_finds_terraform_subdir(self, tmp_path):
        tf_dir = tmp_path / "terraform"
        tf_dir.mkdir()
        (tf_dir / "main.tf").write_text('resource "aws_s3_bucket" "x" {}')
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=tmp_path)
        result = policy._resolve_terraform_dir(context)
        assert result == tf_dir

    def test_falls_back_to_build_path(self, tmp_path):
        (tmp_path / "main.tf").write_text('resource "aws_s3_bucket" "x" {}')
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=tmp_path)
        result = policy._resolve_terraform_dir(context)
        assert result == tmp_path

    def test_returns_none_when_no_tf_files(self, tmp_path):
        (tmp_path / "terraform").mkdir()  # dir exists but no .tf files
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=tmp_path)
        result = policy._resolve_terraform_dir(context)
        assert result is None

    def test_returns_none_when_no_build_path(self):
        policy = CheckovPolicy(_make_policy())
        context = _make_context(build_path=None)
        result = policy._resolve_terraform_dir(context)
        assert result is None
