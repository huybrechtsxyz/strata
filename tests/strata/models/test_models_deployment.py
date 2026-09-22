#!/usr/bin/env python3
"""Tests for DeploymentModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.deployment_model import DeploymentModel


def _minimal_deployment() -> dict:
    return {
        "meta": {"name": "control-dev"},
        "spec": {
            "workspace": "control-workspace",
            "environments": ["core-env", "control-dev-env"],
        },
    }


def test_deployment_minimal_is_valid():
    """A deployable document needs a workspace and at least one environment."""
    model = DeploymentModel.model_validate(_minimal_deployment())
    assert model.meta.name == "control-dev"
    assert model.spec.workspace == "control-workspace"
    assert model.spec.environments == ["core-env", "control-dev-env"]
    assert model.spec.partial is False
    assert model.kind.value == "deployment"


def test_deployment_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _minimal_deployment()
    data["kind"] = "workspace"
    with pytest.raises(ValidationError, match="Expected kind 'deployment'"):
        DeploymentModel.model_validate(data)


# ---------------------------------------------------------------------------
# partial / extends
# ---------------------------------------------------------------------------


def test_deployment_requires_workspace_and_environments():
    """A non-partial deployment missing required fields is rejected."""
    with pytest.raises(ValidationError, match="partial"):
        DeploymentModel.model_validate({"meta": {"name": "incomplete"}, "spec": {}})


def test_partial_deployment_may_omit_required_fields():
    """partial: true marks a reusable base that is not deployable alone."""
    model = DeploymentModel.model_validate(
        {"meta": {"name": "deploy-base-customer"}, "spec": {"partial": True, "locking": {"enabled": True}}}
    )
    assert model.spec.partial is True
    assert model.spec.workspace is None


def test_deployment_rejects_self_extension():
    """The trivial cycle is caught at the model; longer chains need the index."""
    data = _minimal_deployment()
    data["spec"]["extends"] = "control-dev"
    with pytest.raises(ValidationError, match="cannot extend itself"):
        DeploymentModel.model_validate(data)


def test_deployment_accepts_extends_by_name():
    """extends names a base document, not a path (ADR-0015)."""
    data = _minimal_deployment()
    data["spec"]["extends"] = "deploy-base-customer"
    model = DeploymentModel.model_validate(data)
    assert model.spec.extends == "deploy-base-customer"


def test_extending_deployment_may_omit_required_fields():
    """A child is incomplete until merged — the base supplies the rest."""
    model = DeploymentModel.model_validate(
        {"meta": {"name": "leaf"}, "spec": {"extends": "deploy-base", "environments": ["prd"]}}
    )
    assert model.spec.workspace is None
    assert model.spec.partial is False


def test_deployment_rejects_extends_file_path():
    """A v1-style '@config/...' path fails PlatformName."""
    data = _minimal_deployment()
    data["spec"]["extends"] = "@config/stacks/customer/deployment.yaml"
    with pytest.raises(ValidationError):
        DeploymentModel.model_validate(data)


def test_deployment_rejects_duplicate_environments():
    """A repeated environment reference would merge twice."""
    data = _minimal_deployment()
    data["spec"]["environments"] = ["core-env", "core-env"]
    with pytest.raises(ValidationError, match="Duplicate"):
        DeploymentModel.model_validate(data)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def test_stage_references_a_provisioning_step():
    """A stage names a workspace provisioning step and carries runtime knobs."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "control_infra", "on_failure": "stop"}]
    model = DeploymentModel.model_validate(data)
    assert model.spec.stages[0].step == "control_infra"
    assert model.spec.stages[0].enabled is True


def test_stage_rejects_v1_provisioner_binding():
    """v1 bound provisioner/topology on the stage; that now lives in the workspace."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "control_infra", "provisioner": "terraform-main"}]
    with pytest.raises(ValidationError):
        DeploymentModel.model_validate(data)


def test_deployment_rejects_duplicate_stage_steps():
    """Each step may have parameters declared only once."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "infra"}, {"step": "infra"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        DeploymentModel.model_validate(data)


def test_stage_rejects_unknown_on_failure():
    """on_failure is a closed vocabulary."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "infra", "on_failure": "explode"}]
    with pytest.raises(ValidationError, match="on_failure"):
        DeploymentModel.model_validate(data)


def test_stage_enabled_accepts_bool_or_expression():
    """enabled is bool | str; a string is a conditional expression (engine not built)."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "a", "enabled": False}, {"step": "b", "enabled": "${var:DEPLOY_B}"}]
    model = DeploymentModel.model_validate(data)
    assert model.spec.stages[0].enabled is False
    assert model.spec.stages[1].enabled == "${var:DEPLOY_B}"


def test_stage_rejects_empty_expression():
    """An empty conditional expression is meaningless."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "a", "enabled": "   "}]
    with pytest.raises(ValidationError, match="must not be empty"):
        DeploymentModel.model_validate(data)


# ---------------------------------------------------------------------------
# Health checks, timeouts, locking
# ---------------------------------------------------------------------------


def test_http_health_check_requires_url():
    """An http probe without a url cannot run."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "a", "health_checks": [{"name": "api", "type": "http"}]}]
    with pytest.raises(ValidationError, match="'url' is required"):
        DeploymentModel.model_validate(data)


def test_tcp_health_check_requires_host_and_port():
    """A tcp probe needs both host and port."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "a", "health_checks": [{"name": "db", "type": "tcp", "host": "db"}]}]
    with pytest.raises(ValidationError, match="'host' and 'port' are required"):
        DeploymentModel.model_validate(data)


def test_valid_health_checks_are_accepted():
    """Well-formed http and tcp probes validate."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [
        {
            "step": "a",
            "health_checks": [
                {"name": "api", "type": "http", "url": "https://example.com/health", "expect_status": 204},
                {"name": "db", "type": "tcp", "host": "db.internal", "port": 5432},
            ],
            "timeouts": {"apply": 1800},
        }
    ]
    model = DeploymentModel.model_validate(data)
    assert model.spec.stages[0].health_checks[0].expect_status == 204
    assert model.spec.stages[0].timeouts.apply == 1800


def test_locking_rejects_unknown_strategy():
    """Locking strategy is a closed vocabulary."""
    data = _minimal_deployment()
    data["spec"]["locking"] = {"enabled": True, "strategy": "yolo"}
    with pytest.raises(ValidationError, match="strategy"):
        DeploymentModel.model_validate(data)


def test_locking_defaults_to_delegate():
    """Default is to leave locking to the provisioner's own backend."""
    data = _minimal_deployment()
    data["spec"]["locking"] = {"enabled": True}
    model = DeploymentModel.model_validate(data)
    assert model.spec.locking.strategy == "delegate"


def test_deployment_accepts_layers_block():
    """layers is modelled (10 of 15 real documents use it) though inert in v2."""
    data = _minimal_deployment()
    data["spec"]["layers"] = {"follows": "control-path", "segments": {"control": "dev"}}
    model = DeploymentModel.model_validate(data)
    assert model.spec.layers.segments == {"control": "dev"}


def test_deployment_rejects_unported_v1_fields():
    """versions/promotion/gates had zero real usage and are not ported."""
    for field in ("versions", "promotion", "gates"):
        data = _minimal_deployment()
        data["spec"][field] = []
        with pytest.raises(ValidationError):
            DeploymentModel.model_validate(data)
