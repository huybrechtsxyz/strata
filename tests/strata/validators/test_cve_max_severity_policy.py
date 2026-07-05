"""Tests for CveMaxSeverityPolicy — CVE threshold enforcement."""

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.models.sbom_model import CveAuditResultModel, CveFindingModel
    from strata.validators.policies.base_policy import PolicyContext
    from strata.validators.policies.cve_max_severity_policy import CveMaxSeverityPolicy

    IMPL_MISSING = False
except ImportError:
    CveMaxSeverityPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    CveAuditResultModel = None  # type: ignore[assignment,misc]
    CveFindingModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="CveMaxSeverityPolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-cve-severity",
            "type": "cve_max_severity",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_audit_result(*, critical=0, high=0, medium=0, low=0, unknown=0, findings=None):
    if findings is None:
        findings = []
    return CveAuditResultModel(
        scanner="trivy",
        scanner_version="0.52.0",
        sbom_path="sbom.json",
        total_findings=critical + high + medium + low + unknown,
        critical=critical,
        high=high,
        medium=medium,
        low=low,
        unknown=unknown,
        findings=findings,
    )


def make_finding(severity="HIGH", pkg="openssl"):
    return CveFindingModel(
        vulnerability_id=f"CVE-2024-{severity}",
        severity=severity,
        package_name=pkg,
        installed_version="1.0.0",
        fixed_version="1.0.1",
    )


def make_context(audit_result=None, work_path=None):
    return PolicyContext(
        phase="build",
        work_path=work_path,
        cve_audit_result=audit_result,
    )


# ---------------------------------------------------------------------------
# Tests: configuration guards
# ---------------------------------------------------------------------------


class TestCveMaxSeverityPolicyConfig:
    def test_skips_when_max_severity_not_set(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(critical=5)))
        assert result.passed is True
        assert "skipped" in (result.details or {})

    def test_skips_when_max_severity_invalid(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "BOGUS"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(critical=1)))
        assert result.passed is True
        assert "skipped" in (result.details or {})


# ---------------------------------------------------------------------------
# Tests: using pre-computed audit result from context
# ---------------------------------------------------------------------------


class TestCveMaxSeverityPolicyFromContext:
    def test_passes_when_no_findings(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result()))
        assert result.passed is True
        assert result.violations == []

    def test_fails_when_critical_exceeds_zero(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "CRITICAL"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(critical=1)))
        assert result.passed is False
        assert len(result.violations) == 1
        assert "CRITICAL" in result.violations[0]

    def test_fails_when_high_and_max_severity_is_high(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(high=2)))
        assert result.passed is False
        assert "2" in result.violations[0]

    def test_fails_counts_critical_and_high_together_for_high_threshold(self):
        """max_severity=HIGH means CRITICAL+HIGH combined against max_count."""
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH", "max_count": 2}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(critical=1, high=2)))
        # 3 total at or above HIGH, exceeds max_count=2
        assert result.passed is False

    def test_passes_when_only_low_and_max_severity_is_critical(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "CRITICAL"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(low=10)))
        assert result.passed is True

    def test_max_count_allows_some_findings(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH", "max_count": 5}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(high=3)))
        assert result.passed is True

    def test_max_count_zero_fails_on_any_finding(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH", "max_count": 0}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(high=1)))
        assert result.passed is False

    def test_warn_enforcement_does_not_affect_pass_field(self):
        """Enforcement level is surfaced by the engine, not by the policy result."""
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH"}, enforcement="warn"))
        result = policy.evaluate(make_context(audit_result=make_audit_result(high=1)))
        # Policy still reports passed=False; engine decides what to do with enforcement
        assert result.passed is False
        assert result.enforcement == "warn"

    def test_details_contains_scanner_info(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH"}))
        result = policy.evaluate(make_context(audit_result=make_audit_result(high=1)))
        assert result.details is not None
        assert result.details["scanner"] == "trivy"
        assert result.details["max_severity"] == "HIGH"
        assert result.details["breaching_count"] == 1


# ---------------------------------------------------------------------------
# Tests: no pre-computed result → scan runs (or skips gracefully)
# ---------------------------------------------------------------------------


class TestCveMaxSeverityPolicyNoContext:
    def test_skips_when_no_build_path(self):
        policy = CveMaxSeverityPolicy(make_policy(configuration={"max_severity": "HIGH"}))
        context = PolicyContext(phase="build", work_path=None, cve_audit_result=None, build_path=None)
        result = policy.evaluate(context)
        assert result.passed is True
        assert "skipped" in (result.details or {})


# ---------------------------------------------------------------------------
# Tests: policy engine registration
# ---------------------------------------------------------------------------


class TestCveMaxSeverityRegistration:
    def test_registered_in_engine(self):
        from strata.validators.policies.policy_engine import PolicyEngine

        model = make_policy(configuration={"max_severity": "HIGH"})
        engine = PolicyEngine([model])
        # Should not raise — policy was registered
        context = make_context(audit_result=make_audit_result())
        results = engine.evaluate("build", context)
        assert len(results) == 1
        assert results[0].policy_type == "cve_max_severity"
