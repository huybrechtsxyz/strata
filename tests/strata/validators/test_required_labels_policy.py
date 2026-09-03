"""Tests for RequiredTagsPolicy — label enforcement on platform artifact namespaces.

The policy runs at the ``build`` phase and verifies that every namespace in the
platform artifact carries all configured required labels.
"""

from unittest.mock import MagicMock

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.required_tags_policy import RequiredTagsPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_CONFIGURATION = {"required_labels": ["environment", "project"]}


def make_policy(configuration=None) -> "PolicyModel":
    cfg = configuration if configuration is not None else _DEFAULT_CONFIGURATION
    return PolicyModel.model_validate(
        {
            "name": "test-policy",
            "type": "required_tags",
            "phase": "build",
            "enforcement": "deny",
            "configuration": cfg,
        }
    )


def make_namespace(name: str, labels: dict | None) -> MagicMock:
    ns = MagicMock()
    ns.name = name
    ns.labels = labels
    return ns


def make_context(namespaces=None, artifact: bool = True) -> "PolicyContext":
    artifact_mock = None
    if artifact:
        artifact_mock = MagicMock()
        artifact_mock.spec.namespaces = namespaces or []
    return PolicyContext(phase="build", work_path=None, platform_artifact=artifact_mock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRequiredTagsPolicy:
    def test_passes_when_all_namespaces_have_required_labels(self):
        """Two namespaces both carrying all required labels → passed, no violations."""
        namespaces = [
            make_namespace("ns-alpha", {"environment": "prod", "project": "core"}),
            make_namespace("ns-beta", {"environment": "staging", "project": "api"}),
        ]
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_namespace_missing_label(self):
        """Namespace missing 'environment' label → passed=False, violation names namespace and label."""
        namespaces = [
            make_namespace("ns-alpha", {"project": "core"}),
        ]
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "ns-alpha" in result.violations[0]
        assert "environment" in result.violations[0]

    def test_fails_multiple_violations(self):
        """Two namespaces each missing a different label → two violations reported."""
        namespaces = [
            make_namespace("ns-alpha", {"project": "core"}),  # missing 'environment'
            make_namespace("ns-beta", {"environment": "prod"}),  # missing 'project'
        ]
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 2
        violation_text = " ".join(result.violations)
        assert "ns-alpha" in violation_text
        assert "ns-beta" in violation_text

    def test_skip_when_no_platform_artifact(self):
        """context.platform_artifact is None → passed=True, details['skipped'] is set."""
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(artifact=False)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_no_required_labels_configured(self):
        """configuration={} → passed=True, skipped (no labels to enforce)."""
        policy = RequiredTagsPolicy(make_policy(configuration={}))
        ctx = make_context(namespaces=[make_namespace("ns-alpha", {})])

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_required_labels_empty(self):
        """configuration={"required_labels": []} → passed=True, skipped."""
        policy = RequiredTagsPolicy(make_policy(configuration={"required_labels": []}))
        ctx = make_context(namespaces=[make_namespace("ns-alpha", {})])

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_namespace_with_none_labels(self):
        """namespace.labels is None → treated as empty dict → violation for each required label."""
        namespaces = [
            make_namespace("ns-alpha", None),
        ]
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) >= 1
        assert "ns-alpha" in result.violations[0]

    def test_namespace_with_extra_labels_passes(self):
        """Namespace labels may have extra keys beyond what's required — should still pass."""
        namespaces = [
            make_namespace(
                "ns-alpha",
                {"environment": "prod", "project": "core", "owner": "team-a", "cost-center": "42"},
            ),
        ]
        policy = RequiredTagsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []
