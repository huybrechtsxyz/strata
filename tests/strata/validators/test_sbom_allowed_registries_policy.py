"""Tests for SbomAllowedRegistriesPolicy — restrict container images to approved registries.

The policy runs at the ``build`` phase and verifies that container image SBOM
components originate only from explicitly allowed registry prefixes.
"""

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.models.sbom_model import SbomComponentModel
    from strata.validators.policies.base_policy import PolicyContext
    from strata.validators.policies.sbom_allowed_registries_policy import SbomAllowedRegistriesPolicy

    IMPL_MISSING = False
except ImportError:
    SbomAllowedRegistriesPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    SbomComponentModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="SbomAllowedRegistriesPolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-registries",
            "type": "sbom_allowed_registries",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_component(
    name="my-app",
    version="1.0.0",
    purl="pkg:docker/ghcr.io/myorg/my-app@1.0.0",
    source_collector="image",
):
    return SbomComponentModel(
        component_type="container",
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


class TestSbomAllowedRegistriesPolicy:
    def test_passes_when_all_images_from_allowed_registry(self):
        components = [
            make_component(purl="pkg:docker/ghcr.io/myorg/app@1.0.0"),
            make_component(purl="pkg:docker/ghcr.io/myorg/api@2.0.0", name="api"),
        ]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["ghcr.io/myorg"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_image_from_unapproved_registry(self):
        components = [
            make_component(purl="pkg:docker/ghcr.io/myorg/app@1.0.0"),
            make_component(purl="pkg:docker/evil.io/hacker/malware@1.0.0", name="malware"),
        ]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["ghcr.io/myorg"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "malware" in result.violations[0]
        assert "evil.io/hacker" in result.violations[0]

    def test_multiple_allowed_registries(self):
        components = [
            make_component(purl="pkg:docker/ghcr.io/myorg/app@1.0.0"),
            make_component(purl="pkg:docker/mcr.microsoft.com/dotnet/sdk@8.0", name="sdk"),
        ]
        policy = SbomAllowedRegistriesPolicy(
            make_policy(configuration={"allowed": ["ghcr.io/myorg", "mcr.microsoft.com"]})
        )
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_docker_library_images_default_registry(self):
        """Single-segment images like 'nginx' default to docker.io/library."""
        components = [
            make_component(purl="pkg:docker/nginx@1.25.3", name="nginx"),
        ]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["docker.io/library"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_docker_library_images_rejected_when_not_allowed(self):
        components = [
            make_component(purl="pkg:docker/nginx@1.25.3", name="nginx"),
        ]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["ghcr.io/myorg"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert "nginx" in result.violations[0]

    def test_ignores_non_docker_purls(self):
        """Non-docker/oci purls (e.g., pypi, helm) are not checked."""
        components = [
            make_component(purl="pkg:pypi/requests@2.31.0", name="requests", source_collector="deps"),
            make_component(purl="pkg:helm/bitnami/redis@18.0.0", name="redis", source_collector="helm"),
        ]
        policy = SbomAllowedRegistriesPolicy(
            make_policy(
                configuration={"allowed": ["ghcr.io/myorg"], "collectors": ["image", "compose", "deps", "helm"]}
            )
        )
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_filters_by_collector(self):
        """Only checks collectors specified in configuration."""
        components = [
            make_component(purl="pkg:docker/evil.io/bad/image@1.0", source_collector="compose"),
            make_component(purl="pkg:docker/evil.io/bad/other@1.0", source_collector="image"),
        ]
        # Only check "image" collector
        policy = SbomAllowedRegistriesPolicy(
            make_policy(configuration={"allowed": ["ghcr.io/myorg"], "collectors": ["image"]})
        )
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1  # only the "image" collector one

    def test_skips_when_no_components(self):
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["ghcr.io/myorg"]}))
        result = policy.evaluate(make_context(components=None))

        assert result.passed is True
        assert result.details == {"skipped": "no SBOM components available"}

    def test_skips_when_no_allowed_configured(self):
        components = [make_component()]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.details == {"skipped": "no allowed registries configured"}

    def test_oci_purl_scheme(self):
        """OCI purl scheme is also checked."""
        components = [
            make_component(purl="pkg:oci/mcr.microsoft.com/dotnet/aspnet@8.0", name="aspnet"),
        ]
        policy = SbomAllowedRegistriesPolicy(make_policy(configuration={"allowed": ["mcr.microsoft.com"]}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
