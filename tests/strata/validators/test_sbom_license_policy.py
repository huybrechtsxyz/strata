"""Tests for SbomLicensePolicy — enforce license allow/deny lists.

The policy runs at the ``build`` phase and checks each component's
``strata:license`` property against configurable allow and deny lists.
"""

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.models.sbom_model import SbomComponentModel
    from strata.validators.policies.base_policy import PolicyContext
    from strata.validators.policies.sbom_license_policy import SbomLicensePolicy

    IMPL_MISSING = False
except ImportError:
    SbomLicensePolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    SbomComponentModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="SbomLicensePolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-license",
            "type": "sbom_license",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_component(
    name="requests",
    version="2.31.0",
    purl="pkg:pypi/requests@2.31.0",
    license_id=None,
):
    props = {}
    if license_id is not None:
        props["strata:license"] = license_id
    return SbomComponentModel(
        component_type="library",
        name=name,
        version=version,
        purl=purl,
        source_collector="deps",
        properties=props,
    )


def make_context(components=None):
    return PolicyContext(phase="build", work_path=None, sbom_components=components)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSbomLicensePolicy:
    # --- Skip scenarios ---

    def test_skips_when_no_components(self):
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT"]}))
        result = policy.evaluate(make_context(None))
        assert result.passed is True
        assert result.details["skipped"] == "no SBOM components available"

    def test_skips_when_no_lists_configured(self):
        components = [make_component(license_id="MIT")]
        policy = SbomLicensePolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True
        assert result.details["skipped"] == "no allowed or denied licenses configured"

    # --- Allow-list scenarios ---

    def test_passes_when_license_in_allowed(self):
        components = [
            make_component(name="requests", license_id="MIT"),
            make_component(name="flask", license_id="BSD-3-Clause"),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT", "BSD-3-Clause"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True
        assert result.violations == []

    def test_fails_when_license_not_in_allowed(self):
        components = [
            make_component(name="copyleft-lib", license_id="GPL-3.0-only"),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT", "Apache-2.0"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert len(result.violations) == 1
        assert "GPL-3.0-only" in result.violations[0]
        assert "not in allowed list" in result.violations[0]

    def test_allowed_supports_glob_patterns(self):
        components = [
            make_component(name="bsd-lib", license_id="BSD-2-Clause"),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["BSD-*", "MIT"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True

    # --- Deny-list scenarios ---

    def test_fails_when_license_in_denied(self):
        components = [
            make_component(name="gpl-lib", license_id="GPL-3.0-only"),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"denied": ["GPL-*", "AGPL-*"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert "denied license" in result.violations[0]

    def test_passes_when_license_not_in_denied(self):
        components = [
            make_component(name="mit-lib", license_id="MIT"),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"denied": ["GPL-3.0-only"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True

    # --- Combined allow + deny ---

    def test_deny_wins_over_allow(self):
        """A license explicitly denied should fail even if it matches the allow glob."""
        components = [
            make_component(name="agpl-lib", license_id="AGPL-3.0-only"),
        ]
        config = {
            "allowed": ["*"],  # allow everything
            "denied": ["AGPL-*"],  # but explicitly deny AGPL
        }
        policy = SbomLicensePolicy(make_policy(configuration=config))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert "denied license" in result.violations[0]

    # --- Unknown license handling ---

    def test_unknown_action_warn_produces_warning(self):
        components = [
            make_component(name="mystery-lib"),  # no license property
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT"], "unknown_action": "warn"}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True  # warnings don't fail
        assert result.details is not None
        assert len(result.details["warnings"]) == 1
        assert "no license metadata" in result.details["warnings"][0]

    def test_unknown_action_deny_produces_violation(self):
        components = [
            make_component(name="mystery-lib"),  # no license property
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT"], "unknown_action": "deny"}))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert "no license metadata" in result.violations[0]

    def test_unknown_action_allow_silently_passes(self):
        components = [
            make_component(name="mystery-lib"),  # no license property
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT"], "unknown_action": "allow"}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True
        assert result.violations == []

    def test_default_unknown_action_is_warn(self):
        components = [
            make_component(name="no-license"),  # no license property
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"denied": ["GPL-*"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is True
        assert result.details is not None
        assert "warnings" in result.details

    # --- Mixed components ---

    def test_mixed_licensed_and_unlicensed(self):
        components = [
            make_component(name="good-lib", license_id="MIT"),
            make_component(name="bad-lib", license_id="GPL-3.0-only"),
            make_component(name="unknown-lib"),
        ]
        config = {
            "allowed": ["MIT", "Apache-2.0"],
            "unknown_action": "warn",
        }
        policy = SbomLicensePolicy(make_policy(configuration=config))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert len(result.violations) == 1
        assert "GPL-3.0-only" in result.violations[0]
        assert len(result.details["warnings"]) == 1

    def test_empty_license_string_treated_as_unknown(self):
        components = [
            make_component(name="empty-lic", license_id=""),
        ]
        policy = SbomLicensePolicy(make_policy(configuration={"allowed": ["MIT"], "unknown_action": "deny"}))
        result = policy.evaluate(make_context(components))
        assert result.passed is False
        assert "no license metadata" in result.violations[0]
