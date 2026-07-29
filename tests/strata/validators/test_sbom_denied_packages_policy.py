"""Tests for SbomDeniedPackagesPolicy — block specific packages by purl glob.

The policy runs at the ``build`` phase and verifies that no SBOM component
matches a denied purl pattern (fnmatch-style glob).
"""

from strata.models.policy_model import PolicyModel
from strata.models.sbom_model import SbomComponentModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.sbom_denied_packages_policy import SbomDeniedPackagesPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-denied",
            "type": "sbom_denied_packages",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_component(
    name="requests",
    version="2.31.0",
    purl="pkg:pypi/requests@2.31.0",
    source_collector="deps",
):
    return SbomComponentModel(
        component_type="library",
        name=name,
        version=version,
        purl=purl,
        source_collector=source_collector,
        properties={},
    )


def make_context(components=None):
    return PolicyContext(phase="build", work_path=None, sbom_components=components)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSbomDeniedPackagesPolicy:
    def test_passes_when_no_denied_match(self):
        components = [
            make_component(name="requests", purl="pkg:pypi/requests@2.31.0"),
            make_component(name="flask", purl="pkg:pypi/flask@3.0.0"),
        ]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:pypi/evil-package@*"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_purl_matches_glob(self):
        components = [
            make_component(name="event-stream", purl="pkg:npm/event-stream@3.3.6"),
        ]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:npm/event-stream@*"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "event-stream" in result.violations[0]

    def test_fails_when_name_matches_glob(self):
        components = [
            make_component(name="log4j-core", purl="pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"),
        ]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["log4j*"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert "log4j-core" in result.violations[0]

    def test_custom_reason_in_violation(self):
        components = [
            make_component(name="bad-pkg", purl="pkg:pypi/bad-pkg@1.0.0"),
        ]
        policy = SbomDeniedPackagesPolicy(
            make_policy(
                configuration={
                    "denied": ["pkg:pypi/bad-pkg@*"],
                    "reason": "CVE-2026-12345",
                }
            )
        )
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert "CVE-2026-12345" in result.violations[0]

    def test_multiple_denied_patterns(self):
        components = [
            make_component(name="safe-pkg", purl="pkg:pypi/safe-pkg@1.0.0"),
            make_component(name="bad-one", purl="pkg:pypi/bad-one@1.0.0"),
            make_component(name="bad-two", purl="pkg:npm/bad-two@2.0.0"),
        ]
        policy = SbomDeniedPackagesPolicy(
            make_policy(configuration={"denied": ["pkg:pypi/bad-one@*", "pkg:npm/bad-two@*"]})
        )
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 2

    def test_wildcard_version_match(self):
        """Glob pattern with * matches any version."""
        components = [
            make_component(name="setuptools", purl="pkg:pypi/setuptools@69.5.1"),
        ]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:pypi/setuptools@*"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False

    def test_exact_purl_match(self):
        """Exact purl (no glob) matches only that specific version."""
        components = [
            make_component(name="requests", purl="pkg:pypi/requests@2.31.0"),
        ]
        # Denied specific version
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:pypi/requests@2.31.0"]}))
        result = policy.evaluate(make_context(components))
        assert result.passed is False

        # Different version not denied
        components2 = [
            make_component(name="requests", purl="pkg:pypi/requests@2.32.0"),
        ]
        result2 = policy.evaluate(make_context(components2))
        assert result2.passed is True

    def test_skips_when_no_components(self):
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:pypi/bad@*"]}))
        result = policy.evaluate(make_context(components=None))

        assert result.passed is True
        assert result.details == {"skipped": "no SBOM components available"}

    def test_skips_when_no_denied_configured(self):
        components = [make_component()]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.details == {"skipped": "no denied patterns configured"}

    def test_only_first_matching_pattern_reported_per_component(self):
        """Each component should produce at most one violation even with multiple matching patterns."""
        components = [
            make_component(name="bad", purl="pkg:pypi/bad@1.0.0"),
        ]
        policy = SbomDeniedPackagesPolicy(make_policy(configuration={"denied": ["pkg:pypi/bad@*", "bad"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1  # only one violation per component
