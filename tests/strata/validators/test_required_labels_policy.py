"""Tests for RequiredLabelsPolicy — label enforcement on platform artifact entities.

The policy runs at the ``build`` phase and verifies that every entity of the
configured ``targets`` (namespaces, resources, modules) in the platform
artifact carries all configured required labels, optionally narrowed by
``filter.name`` / ``filter.resource_type``.
"""

from unittest.mock import MagicMock

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.required_labels_policy import RequiredLabelsPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_CONFIGURATION = {"required_labels": ["environment", "project"]}


def make_policy(configuration=None) -> "PolicyModel":
    cfg = configuration if configuration is not None else _DEFAULT_CONFIGURATION
    return PolicyModel.model_validate(
        {
            "name": "test-policy",
            "type": "required_labels",
            "phase": "build",
            "enforcement": "deny",
            "configuration": cfg,
        }
    )


def make_entity(name: str, labels: dict | None, resource_type: str | None = None) -> MagicMock:
    entity = MagicMock()
    entity.name = name
    entity.labels = labels
    if resource_type is not None:
        entity.properties.resource_type = resource_type
    return entity


def make_context(namespaces=None, resources=None, modules=None, artifact: bool = True) -> "PolicyContext":
    artifact_mock = None
    if artifact:
        artifact_mock = MagicMock()
        artifact_mock.spec.namespaces = namespaces or []
        artifact_mock.spec.resources = resources or []
        artifact_mock.spec.modules = modules or []
    return PolicyContext(phase="build", work_path=None, platform_artifact=artifact_mock)


# ---------------------------------------------------------------------------
# Tests — default target (namespaces)
# ---------------------------------------------------------------------------


class TestRequiredLabelsPolicyNamespaces:
    def test_passes_when_all_namespaces_have_required_labels(self):
        """Two namespaces both carrying all required labels → passed, no violations."""
        namespaces = [
            make_entity("ns-alpha", {"environment": "prod", "project": "core"}),
            make_entity("ns-beta", {"environment": "staging", "project": "api"}),
        ]
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_namespace_missing_label(self):
        """Namespace missing 'environment' label → passed=False, violation names namespace and label."""
        namespaces = [
            make_entity("ns-alpha", {"project": "core"}),
        ]
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "ns-alpha" in result.violations[0]
        assert "environment" in result.violations[0]

    def test_fails_multiple_violations(self):
        """Two namespaces each missing a different label → two violations reported."""
        namespaces = [
            make_entity("ns-alpha", {"project": "core"}),  # missing 'environment'
            make_entity("ns-beta", {"environment": "prod"}),  # missing 'project'
        ]
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 2
        violation_text = " ".join(result.violations)
        assert "ns-alpha" in violation_text
        assert "ns-beta" in violation_text

    def test_skip_when_no_platform_artifact(self):
        """context.platform_artifact is None → passed=True, details['skipped'] is set."""
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(artifact=False)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_no_required_labels_configured(self):
        """configuration={} → passed=True, skipped (no labels to enforce)."""
        policy = RequiredLabelsPolicy(make_policy(configuration={}))
        ctx = make_context(namespaces=[make_entity("ns-alpha", {})])

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_required_labels_empty(self):
        """configuration={"required_labels": []} → passed=True, skipped."""
        policy = RequiredLabelsPolicy(make_policy(configuration={"required_labels": []}))
        ctx = make_context(namespaces=[make_entity("ns-alpha", {})])

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_namespace_with_none_labels(self):
        """namespace.labels is None → treated as empty dict → violation for each required label."""
        namespaces = [
            make_entity("ns-alpha", None),
        ]
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) >= 1
        assert "ns-alpha" in result.violations[0]

    def test_namespace_with_extra_labels_passes(self):
        """Namespace labels may have extra keys beyond what's required — should still pass."""
        namespaces = [
            make_entity(
                "ns-alpha",
                {"environment": "prod", "project": "core", "owner": "team-a", "cost-center": "42"},
            ),
        ]
        policy = RequiredLabelsPolicy(make_policy())
        ctx = make_context(namespaces=namespaces)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []


# ---------------------------------------------------------------------------
# Tests — targets
# ---------------------------------------------------------------------------


class TestRequiredLabelsPolicyTargets:
    def test_resources_target_checked_when_selected(self):
        """targets=[resources] checks resources, ignores namespaces entirely."""
        namespaces = [make_entity("ns-alpha", {})]  # would fail if namespaces were checked
        resources = [make_entity("vm-1", {"environment": "prod", "project": "core"})]
        policy = RequiredLabelsPolicy(make_policy(configuration={**_DEFAULT_CONFIGURATION, "targets": ["resources"]}))
        ctx = make_context(namespaces=namespaces, resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []

    def test_resources_target_reports_missing_label(self):
        resources = [make_entity("vm-1", {"environment": "prod"})]  # missing 'project'
        policy = RequiredLabelsPolicy(make_policy(configuration={**_DEFAULT_CONFIGURATION, "targets": ["resources"]}))
        ctx = make_context(resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert "vm-1" in result.violations[0]
        assert "project" in result.violations[0]

    def test_modules_target_checked_when_selected(self):
        modules = [make_entity("mod-1", {"project": "core"})]  # missing 'environment'
        policy = RequiredLabelsPolicy(make_policy(configuration={**_DEFAULT_CONFIGURATION, "targets": ["modules"]}))
        ctx = make_context(modules=modules)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert "mod-1" in result.violations[0]

    def test_multiple_targets_checked_together(self):
        namespaces = [make_entity("ns-alpha", {"environment": "prod", "project": "core"})]
        resources = [make_entity("vm-1", {"project": "core"})]  # missing 'environment'
        policy = RequiredLabelsPolicy(
            make_policy(configuration={**_DEFAULT_CONFIGURATION, "targets": ["namespaces", "resources"]})
        )
        ctx = make_context(namespaces=namespaces, resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "vm-1" in result.violations[0]

    def test_unknown_target_fails_with_explanatory_violation(self):
        policy = RequiredLabelsPolicy(make_policy(configuration={**_DEFAULT_CONFIGURATION, "targets": ["bogus"]}))
        ctx = make_context(namespaces=[make_entity("ns-alpha", {})])

        result = policy.evaluate(ctx)

        assert result.passed is False
        assert "bogus" in result.violations[0]
        assert "namespaces" in result.violations[0]


# ---------------------------------------------------------------------------
# Tests — filter
# ---------------------------------------------------------------------------


class TestRequiredLabelsPolicyFilter:
    def test_filter_by_name_pattern_restricts_scope(self):
        """Only resources matching filter.name are checked; others are ignored."""
        resources = [
            make_entity("prod-vm", {}),  # matches filter, missing all labels → violation
            make_entity("dev-vm", {}),  # doesn't match filter → ignored
        ]
        policy = RequiredLabelsPolicy(
            make_policy(
                configuration={
                    **_DEFAULT_CONFIGURATION,
                    "targets": ["resources"],
                    "filter": {"name": ["prod-*"]},
                }
            )
        )
        ctx = make_context(resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is False
        violation_text = " ".join(result.violations)
        assert "prod-vm" in violation_text
        assert "dev-vm" not in violation_text

    def test_filter_by_resource_type_restricts_scope(self):
        """Only resources whose properties.resource_type is in filter.resource_type are checked."""
        resources = [
            make_entity("vm-1", {}, resource_type="vm"),  # matches filter → violation
            make_entity("db-1", {}, resource_type="postgres"),  # doesn't match → ignored
        ]
        policy = RequiredLabelsPolicy(
            make_policy(
                configuration={
                    **_DEFAULT_CONFIGURATION,
                    "targets": ["resources"],
                    "filter": {"resource_type": ["vm"]},
                }
            )
        )
        ctx = make_context(resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is False
        violation_text = " ".join(result.violations)
        assert "vm-1" in violation_text
        assert "db-1" not in violation_text

    def test_filter_excluding_all_entities_passes(self):
        resources = [make_entity("dev-vm", {})]
        policy = RequiredLabelsPolicy(
            make_policy(
                configuration={
                    **_DEFAULT_CONFIGURATION,
                    "targets": ["resources"],
                    "filter": {"name": ["prod-*"]},
                }
            )
        )
        ctx = make_context(resources=resources)

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []
