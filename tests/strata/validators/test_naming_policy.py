"""Tests for NamingPolicy — regex pattern enforcement on platform names.

The policy runs at the ``validate`` phase.  By default it checks
``configuration.meta.name``; the ``targets`` configuration key extends
coverage to deployment stages, workspace topologies, resources, etc.
"""

from unittest.mock import MagicMock

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.validators.policies.base_policy import PolicyContext, PolicyResult
    from strata.validators.policies.naming_policy import _ALL_TARGETS, NamingPolicy

    IMPL_MISSING = False
except ImportError:
    NamingPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyResult = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    _ALL_TARGETS = set()  # type: ignore[assignment]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="NamingPolicy not yet implemented")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_PATTERN = "^[a-z][a-z0-9-]*$"


def make_policy(pattern: str = _DEFAULT_PATTERN, configuration=None) -> "PolicyModel":
    cfg = configuration if configuration is not None else {"pattern": pattern}
    return PolicyModel.model_validate(
        {
            "name": "naming-policy",
            "type": "naming_pattern",
            "phase": "validate",
            "enforcement": "deny",
            "configuration": cfg,
        }
    )


def make_context(
    config_name: str | None = None,
    config_service: bool = True,
    dep_service: MagicMock | None = None,
) -> "PolicyContext":
    cfg_svc = None
    if config_service:
        cfg_svc = MagicMock()
        if config_name is not None:
            cfg_svc.model.meta.name = config_name
        else:
            cfg_svc.model = None
    return PolicyContext(
        phase="validate",
        work_path=None,
        configuration_service=cfg_svc,
        deployment_service=dep_service,
    )


def _make_dep_service(
    dep_name: str = "my-deploy",
    stage_names: list[str] | None = None,
    workspace_name: str = "my-workspace",
    topology_names: list[str] | None = None,
    resource_names: list[str] | None = None,
    namespace_names: list[str] | None = None,
    provisioner_names: list[str] | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.model.meta.name = dep_name
    stages = []
    for name in stage_names or []:
        s = MagicMock()
        s.name = name
        stages.append(s)
    svc.model.spec.stages = stages

    ws_svc = MagicMock()
    ws_svc.model.meta.name = workspace_name
    spec = ws_svc.model.spec

    topos = []
    for name in topology_names or []:
        t = MagicMock()
        t.name = name
        t.volumes = []
        topos.append(t)
    spec.topologies = topos

    resources = []
    for name in resource_names or []:
        r = MagicMock()
        r.name = name
        r.modules = []
        resources.append(r)
    spec.resources = resources

    namespaces = []
    for name in namespace_names or []:
        n = MagicMock()
        n.name = name
        namespaces.append(n)
    spec.namespaces = namespaces

    provisioners = []
    for name in provisioner_names or []:
        p = MagicMock()
        p.name = name
        provisioners.append(p)
    spec.provisioners = provisioners

    svc.get_workspace_service.return_value = ws_svc
    return svc


# ---------------------------------------------------------------------------
# Existing config_name tests (backward-compatible)
# ---------------------------------------------------------------------------


class TestNamingPolicy:
    def test_passes_when_name_matches_pattern(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_name="my-config"))
        assert result.passed is True
        assert result.violations == []

    def test_fails_when_name_does_not_match(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_name="MyConfig"))
        assert result.passed is False
        assert len(result.violations) == 1
        assert "MyConfig" in result.violations[0]
        assert _DEFAULT_PATTERN in result.violations[0]

    def test_skip_when_no_configuration_service(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_service=False))
        assert result.passed is True
        # target is skipped, not a hard failure
        assert result.details is not None

    def test_skip_when_no_pattern_configured(self):
        policy = NamingPolicy(make_policy(configuration={}))
        result = policy.evaluate(make_context(config_name="my-config"))
        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_skip_when_model_is_none(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_name=None, config_service=True))
        assert result.passed is True
        assert result.details is not None

    def test_fails_with_underscore_in_name(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_name="my_config"))
        assert result.passed is False
        assert "my_config" in result.violations[0]

    def test_passes_with_alphanumeric_name(self):
        policy = NamingPolicy(make_policy())
        result = policy.evaluate(make_context(config_name="abc123"))
        assert result.passed is True


# ---------------------------------------------------------------------------
# targets — unknown / validation
# ---------------------------------------------------------------------------


class TestNamingPolicyTargetsValidation:
    def test_unknown_target_returns_violation(self):
        policy = NamingPolicy(make_policy(configuration={"pattern": ".*", "targets": ["unknown_thing"]}))
        result = policy.evaluate(make_context(config_name="x"))
        assert result.passed is False
        assert "unknown_thing" in result.violations[0]

    def test_all_known_targets_accepted(self):
        for target in _ALL_TARGETS:
            policy = NamingPolicy(make_policy(configuration={"pattern": ".*", "targets": [target]}))
            ctx = make_context(config_service=False)
            # Should not produce an "unknown target" violation
            result = policy.evaluate(ctx)
            if not result.passed:
                assert "Unknown" not in (result.violations[0] if result.violations else "")


# ---------------------------------------------------------------------------
# targets — deployment_name / stage_names
# ---------------------------------------------------------------------------


class TestNamingPolicyDeploymentTargets:
    def test_deployment_name_passes(self):
        dep_svc = _make_dep_service(dep_name="my-deploy")
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["deployment_name"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_deployment_name_fails(self):
        dep_svc = _make_dep_service(dep_name="MyDeploy")
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["deployment_name"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is False
        assert "MyDeploy" in result.violations[0]

    def test_stage_names_all_pass(self):
        dep_svc = _make_dep_service(stage_names=["infra", "app", "network"])
        policy = NamingPolicy(make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["stage_names"]}))
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_stage_names_one_fails(self):
        dep_svc = _make_dep_service(stage_names=["infra", "MyStage"])
        policy = NamingPolicy(make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["stage_names"]}))
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is False
        assert any("MyStage" in v for v in result.violations)

    def test_deployment_target_skipped_when_no_dep_service(self):
        policy = NamingPolicy(make_policy(configuration={"pattern": ".*", "targets": ["deployment_name"]}))
        result = policy.evaluate(make_context(config_service=False))
        assert result.passed is True
        assert result.details is not None
        assert any("deployment_name" in s for s in result.details.get("skipped_targets", []))


# ---------------------------------------------------------------------------
# targets — workspace targets
# ---------------------------------------------------------------------------


class TestNamingPolicyWorkspaceTargets:
    def test_topology_names_pass(self):
        dep_svc = _make_dep_service(topology_names=["prod-cluster", "staging-cluster"])
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["topology_names"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_topology_name_fails(self):
        dep_svc = _make_dep_service(topology_names=["prod_cluster"])
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["topology_names"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is False
        assert any("prod_cluster" in v for v in result.violations)

    def test_resource_names_pass(self):
        dep_svc = _make_dep_service(resource_names=["storage", "database"])
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["resource_names"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_namespace_names_fail(self):
        dep_svc = _make_dep_service(namespace_names=["myNS"])
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["namespace_names"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is False

    def test_provisioner_names_pass(self):
        dep_svc = _make_dep_service(provisioner_names=["tf-azure", "tf-k8s"])
        policy = NamingPolicy(
            make_policy(configuration={"pattern": "^[a-z][a-z0-9-]*$", "targets": ["provisioner_names"]})
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_workspace_target_skipped_when_no_workspace_service(self):
        dep_svc = _make_dep_service()
        dep_svc.get_workspace_service.return_value = None
        policy = NamingPolicy(make_policy(configuration={"pattern": ".*", "targets": ["topology_names"]}))
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True
        assert any("topology_names" in s for s in result.details.get("skipped_targets", []))


# ---------------------------------------------------------------------------
# targets — multiple targets in one policy
# ---------------------------------------------------------------------------


class TestNamingPolicyMultipleTargets:
    def test_multiple_targets_all_pass(self):
        dep_svc = _make_dep_service(
            dep_name="my-deploy",
            stage_names=["infra", "app"],
            topology_names=["prod-cluster"],
        )
        policy = NamingPolicy(
            make_policy(
                configuration={
                    "pattern": "^[a-z][a-z0-9-]*$",
                    "targets": ["deployment_name", "stage_names", "topology_names"],
                }
            )
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is True

    def test_multiple_targets_one_violation_across_targets(self):
        dep_svc = _make_dep_service(
            dep_name="my-deploy",
            stage_names=["infra", "BadStage"],
            topology_names=["prod_cluster"],
        )
        policy = NamingPolicy(
            make_policy(
                configuration={
                    "pattern": "^[a-z][a-z0-9-]*$",
                    "targets": ["deployment_name", "stage_names", "topology_names"],
                }
            )
        )
        result = policy.evaluate(make_context(dep_service=dep_svc))
        assert result.passed is False
        assert len(result.violations) == 2  # BadStage + prod_cluster
