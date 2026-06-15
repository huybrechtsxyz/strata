"""Tests for PolicyController."""

from unittest.mock import MagicMock

from strata.controllers.policy_controller import PolicyController


def _make_service_with_policies(policies):
    """Return a mock ConfigurationService with the given policy list on spec."""
    svc = MagicMock()
    svc.model.spec.policies = policies
    return svc


def _make_deployment_service(stages):
    """Return a mock DeploymentService with the given stage list."""
    svc = MagicMock()
    svc.model.spec.stages = stages
    return svc


def _make_stage(provisioner="", topology=""):
    stage = MagicMock()
    stage.provisioner = provisioner
    stage.topology = topology
    return stage


class TestGetDeclaredPolicies:
    def test_returns_empty_when_service_is_none(self):
        ctrl = PolicyController()
        assert ctrl.get_declared_policies(None) == []

    def test_returns_empty_when_model_is_none(self):
        ctrl = PolicyController()
        svc = MagicMock()
        svc.model = None
        assert ctrl.get_declared_policies(svc) == []

    def test_returns_empty_when_spec_is_none(self):
        ctrl = PolicyController()
        svc = MagicMock()
        svc.model.spec = None
        assert ctrl.get_declared_policies(svc) == []

    def test_returns_empty_when_policies_is_none(self):
        ctrl = PolicyController()
        svc = _make_service_with_policies(None)
        assert ctrl.get_declared_policies(svc) == []

    def test_returns_empty_when_policies_list_is_empty(self):
        ctrl = PolicyController()
        svc = _make_service_with_policies([])
        assert ctrl.get_declared_policies(svc) == []

    def test_returns_all_declared_policies(self):
        ctrl = PolicyController()
        p1, p2 = MagicMock(), MagicMock()
        svc = _make_service_with_policies([p1, p2])
        result = ctrl.get_declared_policies(svc)
        assert result == [p1, p2]

    def test_returns_a_list_copy_not_the_original(self):
        ctrl = PolicyController()
        p = MagicMock()
        original = [p]
        svc = _make_service_with_policies(original)
        result = ctrl.get_declared_policies(svc)
        result.append(MagicMock())
        assert len(ctrl.get_declared_policies(svc)) == 1


class TestGetDeploymentPhases:
    def test_returns_validate_when_service_is_none(self):
        ctrl = PolicyController()
        result = ctrl.get_deployment_phases(None)
        assert result == ["validate"]

    def test_returns_validate_when_model_is_none(self):
        ctrl = PolicyController()
        svc = MagicMock()
        svc.model = None
        result = ctrl.get_deployment_phases(svc)
        assert result == ["validate"]

    def test_returns_validate_when_stages_is_empty(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([])
        result = ctrl.get_deployment_phases(svc)
        assert result == ["validate"]

    def test_validate_always_present(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="tf_prod")])
        result = ctrl.get_deployment_phases(svc)
        assert "validate" in result

    def test_terraform_provisioner_adds_build_plan_deploy(self):
        ctrl = PolicyController()
        # "tf_" prefix is the common short form for terraform provisioner names
        svc = _make_deployment_service([_make_stage(provisioner="tf_hetzner")])
        result = ctrl.get_deployment_phases(svc)
        assert set(result) >= {"validate", "build", "plan", "deploy"}

    def test_terraform_full_name_provisioner_adds_plan(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="terraform_prod")])
        result = ctrl.get_deployment_phases(svc)
        assert "plan" in result

    def test_helm_provisioner_adds_build_and_deploy_not_plan(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="helm_apps")])
        result = ctrl.get_deployment_phases(svc)
        assert "build" in result
        assert "deploy" in result
        assert "plan" not in result

    def test_ansible_provisioner_adds_build_and_deploy(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="ansible_config")])
        result = ctrl.get_deployment_phases(svc)
        assert "build" in result
        assert "deploy" in result

    def test_unknown_provisioner_falls_back_to_build_and_deploy(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="my_custom")])
        result = ctrl.get_deployment_phases(svc)
        assert "build" in result
        assert "deploy" in result

    def test_topology_keyword_used_when_provisioner_is_empty(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="", topology="helm_charts")])
        result = ctrl.get_deployment_phases(svc)
        assert "build" in result
        assert "deploy" in result

    def test_result_is_sorted(self):
        ctrl = PolicyController()
        svc = _make_deployment_service([_make_stage(provisioner="tf_prod")])
        result = ctrl.get_deployment_phases(svc)
        assert result == sorted(result)

    def test_phases_are_deduplicated_across_stages(self):
        ctrl = PolicyController()
        stages = [
            _make_stage(provisioner="tf_prod"),
            _make_stage(provisioner="tf_staging"),
        ]
        svc = _make_deployment_service(stages)
        result = ctrl.get_deployment_phases(svc)
        assert len(result) == len(set(result))

    def test_mixed_provisioners_union_of_phases(self):
        ctrl = PolicyController()
        stages = [
            _make_stage(provisioner="tf_infra"),  # tf_ prefix → plan included
            _make_stage(provisioner="helm_apps"),
        ]
        svc = _make_deployment_service(stages)
        result = ctrl.get_deployment_phases(svc)
        assert set(result) >= {"validate", "build", "plan", "deploy"}
