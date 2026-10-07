#!/usr/bin/env python3
"""Tests for the CVE scanner models (docs/work/cve-scanner-integration.md)."""

import pytest
from pydantic import ValidationError

from strata.models.sbom_model import (
    CVE_SEVERITY_ORDER,
    CveAllowedEntryModel,
    CveAuditResultModel,
    CveFindingModel,
    CveMaxSeverityPolicyModel,
)


def test_severity_order_is_most_to_least_severe():
    assert CVE_SEVERITY_ORDER == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def test_cve_finding_minimal():
    finding = CveFindingModel(
        vulnerability_id="CVE-2024-1234",
        severity="HIGH",
        package_name="openssl",
        installed_version="1.1.1",
    )
    assert finding.fixed_version is None
    assert finding.title is None
    assert finding.purl is None


def test_cve_audit_result_minimal():
    result = CveAuditResultModel(
        scanner="trivy",
        scanner_version="0.50.0",
        sbom_path="build/sbom.json",
        total_findings=1,
        critical=1,
        findings=[
            CveFindingModel(
                vulnerability_id="CVE-2024-1234",
                severity="CRITICAL",
                package_name="openssl",
                installed_version="1.1.1",
            )
        ],
    )
    assert result.high == 0
    assert len(result.findings) == 1


def test_cve_allowed_entry_minimal():
    entry = CveAllowedEntryModel(id="CVE-2024-1234", reason="not exploitable in our config")
    assert entry.package is None
    assert entry.expires is None


def test_cve_allowed_entry_accepts_a_valid_iso_expires_date():
    entry = CveAllowedEntryModel(id="CVE-2024-1234", reason="tracked", expires="2026-12-31")
    assert entry.expires == "2026-12-31"


def test_cve_allowed_entry_rejects_a_non_iso_expires_date():
    with pytest.raises(ValidationError, match="not a valid ISO date"):
        CveAllowedEntryModel(id="CVE-2024-1234", reason="tracked", expires="31-12-2026")


def test_cve_max_severity_policy_defaults():
    policy = CveMaxSeverityPolicyModel(max_severity="HIGH")
    assert policy.max_count == 0
    assert policy.severity_threshold is None
    assert policy.enforcement == "deny"
    assert policy.on_missing_data == "block"


def test_cve_max_severity_policy_rejects_negative_max_count():
    with pytest.raises(ValidationError):
        CveMaxSeverityPolicyModel(max_severity="HIGH", max_count=-1)


def test_cve_max_severity_policy_rejects_unknown_as_max_severity():
    with pytest.raises(ValidationError):
        CveMaxSeverityPolicyModel(max_severity="UNKNOWN")


def test_cve_max_severity_policy_accepts_a_threshold_at_least_as_inclusive():
    policy = CveMaxSeverityPolicyModel(max_severity="LOW", severity_threshold="UNKNOWN")
    assert policy.severity_threshold == "UNKNOWN"
    policy = CveMaxSeverityPolicyModel(max_severity="LOW", severity_threshold="LOW")
    assert policy.severity_threshold == "LOW"


def test_cve_max_severity_policy_rejects_a_threshold_more_restrictive_than_max_severity():
    """severity_threshold: HIGH would make the scanner filter out LOW findings before the
    LOW-severity max_severity policy ever sees them — must be rejected, not silently wrong."""
    with pytest.raises(ValidationError, match="more restrictive"):
        CveMaxSeverityPolicyModel(max_severity="LOW", severity_threshold="HIGH")
