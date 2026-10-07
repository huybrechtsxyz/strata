#!/usr/bin/env python3
"""Tests for the Checkov models (docs/work/checkov-integration.md)."""

from strata.models.checkov_model import (
    CHECKOV_SEVERITY_ORDER,
    CheckovFindingModel,
    CheckovPolicyModel,
    CheckovScanResultModel,
)


def test_severity_order_is_most_to_least_severe():
    assert CHECKOV_SEVERITY_ORDER == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def test_checkov_finding_minimal():
    finding = CheckovFindingModel(
        check_id="CKV_AWS_20",
        check_name="S3 Bucket has an ACL defined which allows public READ access",
        resource="aws_s3_bucket.example",
        file_path="main.tf",
        severity="HIGH",
    )
    assert finding.file_line_range is None
    assert finding.guideline is None


def test_checkov_scan_result_defaults():
    result = CheckovScanResultModel(scanner_version="3.2.0", framework="terraform", scanned_path="build/infra")
    assert result.passed == 0
    assert result.failed == 0
    assert result.skipped == 0
    assert result.findings == []


def _finding(check_id: str, severity: str) -> CheckovFindingModel:
    return CheckovFindingModel(
        check_id=check_id,
        check_name="n",
        resource="r",
        file_path="f",
        severity=severity,
    )


def test_findings_at_or_above_filters_by_severity():
    result = CheckovScanResultModel(
        scanner_version="3.2.0",
        framework="terraform",
        scanned_path="build/infra",
        findings=[
            _finding("CKV_1", "CRITICAL"),
            _finding("CKV_2", "HIGH"),
            _finding("CKV_3", "MEDIUM"),
            _finding("CKV_4", "LOW"),
        ],
    )
    at_or_above_high = result.findings_at_or_above("HIGH")
    assert {f.check_id for f in at_or_above_high} == {"CKV_1", "CKV_2"}


def test_findings_at_or_above_treats_unrecognised_severity_as_unknown():
    result = CheckovScanResultModel(
        scanner_version="3.2.0",
        framework="terraform",
        scanned_path="build/infra",
        findings=[_finding("CKV_1", "WEIRD")],
    )
    assert result.findings_at_or_above("HIGH") == []
    assert result.findings_at_or_above("UNKNOWN") == [result.findings[0]]


def test_checkov_policy_defaults():
    policy = CheckovPolicyModel()
    assert policy.framework == "terraform"
    assert policy.severity_gate == "high"
    assert policy.scope == "staged"
    assert policy.skip_checks is None
    assert policy.include_checks is None
    assert policy.custom_checks_dir is None
    assert policy.timeout == 120
    assert policy.enforcement == "deny"
    assert policy.on_missing_data == "block"
