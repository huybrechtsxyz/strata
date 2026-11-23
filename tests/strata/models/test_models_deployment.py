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


def test_deployment_accepts_provider_bindings():
    data = _minimal_deployment()
    data["spec"]["providers"] = [{"slot": "azure", "provider": "azure-americas"}]
    model = DeploymentModel.model_validate(data)
    assert model.spec.providers[0].slot == "azure"
    assert model.spec.providers[0].provider == "azure-americas"


def test_deployment_rejects_duplicate_provider_slot_bindings():
    data = _minimal_deployment()
    data["spec"]["providers"] = [
        {"slot": "azure", "provider": "azure-americas"},
        {"slot": "azure", "provider": "azure-brazil"},
    ]
    with pytest.raises(ValidationError, match="provider slot bindings"):
        DeploymentModel.model_validate(data)


def test_deployment_provider_binding_has_no_bare_string_shorthand():
    data = _minimal_deployment()
    data["spec"]["providers"] = ["azure-americas"]
    with pytest.raises(ValidationError):
        DeploymentModel.model_validate(data)


def test_deployment_accepts_asset_bindings():
    data = _minimal_deployment()
    data["spec"]["assets"] = [{"slot": "api", "asset": "contoso-api"}]
    model = DeploymentModel.model_validate(data)
    assert (model.spec.assets[0].slot, model.spec.assets[0].asset) == ("api", "contoso-api")


def test_deployment_rejects_duplicate_asset_slot_bindings():
    data = _minimal_deployment()
    data["spec"]["assets"] = [{"slot": "api", "asset": "a"}, {"slot": "api", "asset": "b"}]
    with pytest.raises(ValidationError, match="asset slot bindings"):
        DeploymentModel.model_validate(data)


def test_deployment_asset_binding_has_no_bare_string_shorthand():
    data = _minimal_deployment()
    data["spec"]["assets"] = ["contoso-api"]
    with pytest.raises(ValidationError):
        DeploymentModel.model_validate(data)


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
# Removed health checks, timeouts, locking
# ---------------------------------------------------------------------------


def test_removed_health_checks_are_refused_with_their_replacement():
    """`health_checks` was removed in 2.9 (ADR-0068): the error writes the `verify.checks` entries to use."""
    data = _minimal_deployment()
    data["spec"]["stages"] = [
        {"step": "a", "health_checks": [{"name": "api", "type": "http", "url": "https://example.com/health"}]}
    ]
    with pytest.raises(ValidationError, match="removed in strata 2.9") as error:
        DeploymentModel.model_validate(data)
    assert "checks: [{name: api, type: http, url: https://example.com/health}]" in str(error.value)


def test_stage_timeouts_are_accepted():
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "a", "timeouts": {"apply": 1800}}]
    model = DeploymentModel.model_validate(data)
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


# ---------------------------------------------------------------------------
# stages[].verify (docs/design/post-deploy-verification.md, D2, D5)
# ---------------------------------------------------------------------------


def _stage_with_verify(verify: dict) -> dict:
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "apply_web", "verify": verify}]
    return data


def test_a_verify_block_defaults_to_about_five_minutes_of_waiting():
    model = DeploymentModel.model_validate(_stage_with_verify({"checks": [{"name": "web", "run": ["check"]}]}))
    block = model.spec.stages[0].verify
    assert (block.initial_delay, block.period, block.timeout, block.success_threshold, block.failure_threshold) == (
        10,
        10,
        30,
        1,
        30,
    )
    assert (block.enabled, block.enforcement, block.on_unknown) == (True, "deny", "block")
    [check] = block.checks
    assert (check.type, check.exit_codes, check.failure_threshold) == ("script", "binary", None)


@pytest.mark.parametrize(
    ("verify", "match"),
    [
        ({"checks": []}, "checks"),
        ({"checks": [{"name": "web", "run": []}]}, "run"),
        ({"checks": [{"name": "web", "run": [" "]}]}, "executable"),
        ({"checks": [{"name": "web", "run": ["check"], "exit_codes": "nagios"}]}, "exit_codes"),
        ({"checks": [{"name": "web", "run": ["check"], "type": "http"}]}, "type"),
        ({"enforcement": "block", "checks": [{"name": "web", "run": ["check"]}]}, "enforcement"),
        ({"period": 0, "checks": [{"name": "web", "run": ["check"]}]}, "period"),
        ({"checks": [{"name": "web", "run": ["a"]}, {"name": "web", "run": ["b"]}]}, "web"),
    ],
)
def test_a_verify_block_rejects(verify, match):
    with pytest.raises(ValidationError, match=match):
        DeploymentModel.model_validate(_stage_with_verify(verify))


def test_a_stage_takes_a_verify_timeout():
    data = _minimal_deployment()
    data["spec"]["stages"] = [{"step": "apply_web", "timeouts": {"verify": 600}}]
    assert DeploymentModel.model_validate(data).spec.stages[0].timeouts.verify == 600


def test_the_deployment_takes_its_own_verify_block():
    """docs/design/post-deploy-verification.md, D2: `spec.verify`, run after every step."""
    data = _minimal_deployment()
    data["spec"]["verify"] = {
        "checks": [{"name": "e2e", "run": ["check"]}, {"name": "old", "run": ["x"], "enabled": False}]
    }
    block = DeploymentModel.model_validate(data).spec.verify
    assert [(check.name, check.enabled) for check in block.checks] == [("e2e", True), ("old", False)]
