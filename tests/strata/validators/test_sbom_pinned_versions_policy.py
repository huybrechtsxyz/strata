"""Tests for SbomPinnedVersionsPolicy — enforce pinned versions on SBOM components.

The policy runs at the ``build`` phase and verifies that SBOM components have
explicit, non-floating version tags.
"""

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.models.sbom_model import SbomComponentModel
    from strata.validators.policies.base_policy import PolicyContext
    from strata.validators.policies.sbom_pinned_versions_policy import SbomPinnedVersionsPolicy

    IMPL_MISSING = False
except ImportError:
    SbomPinnedVersionsPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    SbomComponentModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="SbomPinnedVersionsPolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-pinned",
            "type": "sbom_pinned_versions",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_component(
    name="nginx",
    version="1.25.3",
    purl="pkg:docker/library/nginx@1.25.3",
    source_collector="image",
    properties=None,
):
    return SbomComponentModel(
        component_type="container",
        name=name,
        version=version,
        purl=purl,
        source_collector=source_collector,
        properties=properties or {},
    )


def make_context(components=None):
    return PolicyContext(phase="build", work_path=None, sbom_components=components)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSbomPinnedVersionsPolicy:
    def test_passes_when_all_versions_pinned(self):
        components = [
            make_component(name="nginx", version="1.25.3"),
            make_component(name="redis", version="7.2.4", purl="pkg:docker/library/redis@7.2.4"),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy())
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_version_missing(self):
        components = [
            make_component(name="my-app", version=None, purl="pkg:docker/myorg/my-app"),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy())
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "my-app" in result.violations[0]
        assert "no version" in result.violations[0]

    def test_fails_when_tag_is_floating(self):
        components = [
            make_component(
                name="nginx",
                version="latest",
                purl="pkg:docker/library/nginx@latest",
                properties={"strata:tag-stability": "floating"},
            ),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy())
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "floating" in result.violations[0]

    def test_fails_when_latest_used_and_not_allowed(self):
        components = [
            make_component(name="nginx", version="latest", purl="pkg:docker/library/nginx@latest"),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy(configuration={"allow_latest": False}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert "latest" in result.violations[0]

    def test_passes_when_latest_allowed(self):
        components = [
            make_component(name="nginx", version="latest", purl="pkg:docker/library/nginx@latest"),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy(configuration={"allow_latest": True}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_filters_by_collector(self):
        components = [
            make_component(name="nginx", version=None, purl="pkg:docker/nginx", source_collector="image"),
            make_component(name="requests", version=None, purl="pkg:pypi/requests", source_collector="deps"),
        ]
        # Only check image collector
        policy = SbomPinnedVersionsPolicy(make_policy(configuration={"collectors": ["image"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "nginx" in result.violations[0]

    def test_require_digest(self):
        components = [
            make_component(
                name="nginx",
                version="1.25.3",
                purl="pkg:docker/library/nginx@1.25.3",
            ),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy(configuration={"require_digest": True}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert "digest" in result.violations[0]

    def test_require_digest_passes_with_sha(self):
        components = [
            make_component(
                name="nginx",
                version="1.25.3",
                purl="pkg:docker/library/nginx@sha256:abc123",
            ),
        ]
        policy = SbomPinnedVersionsPolicy(make_policy(configuration={"require_digest": True}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_skips_when_no_components(self):
        policy = SbomPinnedVersionsPolicy(make_policy())
        result = policy.evaluate(make_context(components=None))

        assert result.passed is True
        assert result.details == {"skipped": "no SBOM components available"}

    def test_skips_when_empty_components(self):
        policy = SbomPinnedVersionsPolicy(make_policy())
        result = policy.evaluate(make_context(components=[]))

        assert result.passed is True
