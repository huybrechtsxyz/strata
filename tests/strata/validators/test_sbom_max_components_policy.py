"""Tests for SbomMaxComponentsPolicy — complexity budget enforcement.

The policy runs at the ``build`` phase and enforces upper bounds on total SBOM
component count and per-collector limits.
"""

from strata.models.policy_model import PolicyModel
from strata.models.sbom_model import SbomComponentModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.sbom_max_components_policy import SbomMaxComponentsPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None, enforcement="deny"):
    return PolicyModel.model_validate(
        {
            "name": "test-max-components",
            "type": "sbom_max_components",
            "phase": "build",
            "enforcement": enforcement,
            "configuration": configuration or {},
        }
    )


def make_component(name="comp", source_collector="image"):
    return SbomComponentModel(
        component_type="container",
        name=name,
        version="1.0.0",
        purl=f"pkg:docker/{name}@1.0.0",
        source_collector=source_collector,
        properties={},
    )


def make_context(components=None):
    return PolicyContext(phase="build", work_path=None, sbom_components=components)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSbomMaxComponentsPolicy:
    def test_passes_when_under_max_count(self):
        components = [make_component(name=f"app-{i}") for i in range(5)]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"max_count": 10}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_over_max_count(self):
        components = [make_component(name=f"app-{i}") for i in range(15)]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"max_count": 10}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "15" in result.violations[0]
        assert "10" in result.violations[0]

    def test_passes_at_exact_limit(self):
        components = [make_component(name=f"app-{i}") for i in range(10)]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"max_count": 10}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_per_collector_passes_when_under_limit(self):
        components = [
            make_component(name="img-1", source_collector="image"),
            make_component(name="img-2", source_collector="image"),
            make_component(name="dep-1", source_collector="deps"),
        ]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"per_collector": {"image": 5, "deps": 10}}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True

    def test_per_collector_fails_when_over_limit(self):
        components = [make_component(name=f"img-{i}", source_collector="image") for i in range(8)]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"per_collector": {"image": 5}}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 1
        assert "image" in result.violations[0]
        assert "8" in result.violations[0]
        assert "5" in result.violations[0]

    def test_both_max_count_and_per_collector_violations(self):
        components = [make_component(name=f"img-{i}", source_collector="image") for i in range(6)] + [
            make_component(name=f"dep-{i}", source_collector="deps") for i in range(6)
        ]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"max_count": 10, "per_collector": {"image": 5}}))
        result = policy.evaluate(make_context(components))

        assert result.passed is False
        assert len(result.violations) == 2  # total exceeded + image exceeded

    def test_skips_when_no_components(self):
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"max_count": 10}))
        result = policy.evaluate(make_context(components=None))

        assert result.passed is True
        assert result.details == {"skipped": "no SBOM components available"}

    def test_skips_when_no_limits_configured(self):
        components = [make_component()]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
        assert result.details == {"skipped": "no component limits configured"}

    def test_per_collector_ignores_unlisted_collectors(self):
        """Collectors not in per_collector config are not checked."""
        components = [make_component(name=f"dep-{i}", source_collector="deps") for i in range(100)]
        policy = SbomMaxComponentsPolicy(make_policy(configuration={"per_collector": {"image": 5}}))
        result = policy.evaluate(make_context(components))

        assert result.passed is True
