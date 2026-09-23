#!/usr/bin/env python3
"""Tests for DeploymentService — extends merging and workspace cross-checks."""

from strata.models.workspace_model import WorkspaceModel
from strata.services.deployment_service import DeploymentService, merge_deployment_specs


def _workspace(*step_names) -> WorkspaceModel:
    return WorkspaceModel.model_validate(
        {
            "meta": {"name": "control-workspace"},
            "spec": {
                "providers": ["azure-main"],
                "provisioners": [
                    {
                        "name": "terraform-main",
                        "tool": "terraform",
                        "source": {"remote": "infra", "source_path": "terraform/main"},
                    }
                ],
                "resources": [{"name": "aks", "resource": "aks-class"}],
                "execution": [
                    {"name": name, "provisioner": "terraform-main", "targets": ["aks"]} for name in step_names
                ],
            },
        }
    )


def _deployment(**spec_overrides) -> DeploymentService:
    spec = {"workspace": "control-workspace", "environments": ["core-env"]}
    spec.update(spec_overrides)
    service = DeploymentService(data={"meta": {"name": "control-dev"}, "spec": spec})
    service.validate()
    return service


def test_deployment_service_validates_from_data():
    """A DeploymentService constructed from an in-memory dict validates."""
    service = _deployment()
    assert service.model is not None
    assert service.model.spec.workspace == "control-workspace"


# ---------------------------------------------------------------------------
# Stage / workspace cross-check
# ---------------------------------------------------------------------------


def test_stages_matching_workspace_steps_pass():
    """A stage naming a real provisioning step is accepted."""
    service = _deployment(stages=[{"step": "control_infra"}])
    is_valid, errors = service.validate_stages_against_workspace(_workspace("control_infra"))
    assert is_valid
    assert errors == []


def test_stage_naming_unknown_step_is_rejected():
    """Parameters for a nonexistent step would silently apply to nothing."""
    service = _deployment(stages=[{"step": "ghost_step"}])
    is_valid, errors = service.validate_stages_against_workspace(_workspace("control_infra"))
    assert not is_valid
    assert "ghost_step" in errors[0]
    assert "control_infra" in errors[0]


def test_deployment_without_stages_passes_the_cross_check():
    """Stages are optional — a deployment may just run the recipe as declared."""
    is_valid, errors = _deployment().validate_stages_against_workspace(_workspace("control_infra"))
    assert is_valid
    assert errors == []


# ---------------------------------------------------------------------------
# extends merging
# ---------------------------------------------------------------------------


def test_merge_child_top_level_fields_replace_base():
    """Child always wins on scalar/top-level fields."""
    merged = merge_deployment_specs(
        {"workspace": "base-ws", "locking": {"enabled": True, "strategy": "wrap"}},
        {"workspace": "child-ws"},
    )
    assert merged["workspace"] == "child-ws"
    assert merged["locking"] == {"enabled": True, "strategy": "wrap"}


def test_merge_nested_blocks_per_leaf_key():
    """Overriding one field of a block keeps the base's other fields.

    A shallow merge dropped 'strategy' here, letting it fall back to its
    schema default ('delegate') — a silent change to a value nobody wrote.
    """
    merged = merge_deployment_specs(
        {"workspace": "main", "locking": {"enabled": True, "strategy": "wrap", "wait_timeout": "10m"}},
        {"locking": {"wait_timeout": "15m"}},
    )
    assert merged["locking"] == {"enabled": True, "strategy": "wrap", "wait_timeout": "15m"}


def test_merge_nested_blocks_survive_validation():
    """The deep-merged result validates with the base's settings intact."""
    merged = merge_deployment_specs(
        {
            "partial": True,
            "workspace": "main",
            "locking": {"enabled": True, "strategy": "wrap", "wait_timeout": "10m"},
        },
        {"extends": "base", "environments": ["prd"], "locking": {"wait_timeout": "15m"}},
    )
    service = DeploymentService(data={"meta": {"name": "leaf"}, "spec": merged})
    is_valid, errors = service.validate()
    assert is_valid, errors
    assert service.model.spec.locking.strategy == "wrap"
    assert service.model.spec.locking.wait_timeout == "15m"


def test_merge_stage_timeouts_merge_per_leaf():
    """A stage's nested timeouts block merges rather than being replaced."""
    merged = merge_deployment_specs(
        {"stages": [{"step": "infra", "timeouts": {"apply": 1800, "plan": 600}}]},
        {"stages": [{"step": "infra", "timeouts": {"plan": 900}}]},
    )
    assert merged["stages"][0]["timeouts"] == {"apply": 1800, "plan": 900}


def test_merge_stages_are_merged_by_step():
    """A child stage overrides the base's field-by-field; new steps append."""
    merged = merge_deployment_specs(
        {"stages": [{"step": "infra", "on_failure": "stop", "description": "base"}, {"step": "apps"}]},
        {"stages": [{"step": "infra", "on_failure": "continue"}, {"step": "extra"}]},
    )
    steps = {s["step"]: s for s in merged["stages"]}
    assert steps["infra"]["on_failure"] == "continue"
    assert steps["infra"]["description"] == "base"
    assert [s["step"] for s in merged["stages"]] == ["infra", "apps", "extra"]


def test_merge_environments_append_base_first():
    """Base environments come first so the child's win at value resolution."""
    merged = merge_deployment_specs(
        {"environments": ["core-env"]},
        {"environments": ["leaf-env"]},
    )
    assert merged["environments"] == ["core-env", "leaf-env"]


def test_merge_environments_do_not_duplicate():
    """A reference already in the base is not appended twice."""
    merged = merge_deployment_specs(
        {"environments": ["core-env", "shared-env"]},
        {"environments": ["shared-env", "leaf-env"]},
    )
    assert merged["environments"] == ["core-env", "shared-env", "leaf-env"]


def test_merge_strips_extends_control_fields():
    """partial/extends are consumed by resolution, not carried into the result."""
    merged = merge_deployment_specs(
        {"partial": True, "workspace": "base-ws"},
        {"extends": "base", "environments": ["core-env"]},
    )
    assert "partial" not in merged
    assert "extends" not in merged


def test_merged_result_validates_as_a_real_deployment():
    """A partial base plus a child produce a valid, deployable document."""
    merged = merge_deployment_specs(
        {"partial": True, "workspace": "control-workspace", "stages": [{"step": "infra", "on_failure": "stop"}]},
        {"extends": "deploy-base", "environments": ["core-env"]},
    )
    service = DeploymentService(data={"meta": {"name": "leaf"}, "spec": merged})
    is_valid, errors = service.validate()
    assert is_valid, errors
    assert service.model.spec.workspace == "control-workspace"
    assert service.model.spec.stages[0].step == "infra"


def test_merge_does_not_mutate_inputs():
    """Merging is pure — callers may reuse the base for several children."""
    base = {"stages": [{"step": "infra"}], "environments": ["core-env"]}
    child = {"stages": [{"step": "extra"}], "environments": ["leaf-env"]}
    merge_deployment_specs(base, child)
    assert base == {"stages": [{"step": "infra"}], "environments": ["core-env"]}
    assert child == {"stages": [{"step": "extra"}], "environments": ["leaf-env"]}
