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
                    {"name": name, "provisioner": "terraform-main", "resources": ["aks"]} for name in step_names
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
    result = service.validate_stages_against_workspace(_workspace("control_infra"))
    assert result.ok
    assert result.messages() == []


def test_stage_naming_unknown_step_is_rejected():
    """Parameters for a nonexistent step would silently apply to nothing."""
    service = _deployment(stages=[{"step": "ghost_step"}])
    result = service.validate_stages_against_workspace(_workspace("control_infra"))
    assert not result.ok
    message = result.messages()[0]
    assert "ghost_step" in message
    assert "control_infra" in message


def test_deployment_without_stages_passes_the_cross_check():
    """Stages are optional — a deployment may just run the recipe as declared."""
    result = _deployment().validate_stages_against_workspace(_workspace("control_infra"))
    assert result.ok
    assert result.messages() == []


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
    result = service.validate()
    assert result.ok, result.messages()
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


def test_merge_providers_keeps_base_bindings_for_other_slots():
    """Binding one slot in the child does not drop the base's other slots."""
    merged = merge_deployment_specs(
        {"providers": [{"slot": "azure", "provider": "azure-americas"}]},
        {"providers": [{"slot": "dns", "provider": "dns-americas"}]},
    )
    assert merged["providers"] == [
        {"slot": "azure", "provider": "azure-americas"},
        {"slot": "dns", "provider": "dns-americas"},
    ]


def test_merge_providers_child_wins_on_same_slot():
    """Same slot in base and child: the child's binding wins, position kept."""
    merged = merge_deployment_specs(
        {"providers": [{"slot": "azure", "provider": "azure-americas"}, {"slot": "dns", "provider": "dns-base"}]},
        {"providers": [{"slot": "azure", "provider": "azure-brazil"}]},
    )
    assert merged["providers"] == [
        {"slot": "azure", "provider": "azure-brazil"},
        {"slot": "dns", "provider": "dns-base"},
    ]


def test_merge_assets_keeps_base_bindings_for_other_slots():
    merged = merge_deployment_specs(
        {"assets": [{"slot": "api", "asset": "region-api"}]},
        {"assets": [{"slot": "certs", "asset": "c0062-certs"}]},
    )
    assert merged["assets"] == [
        {"slot": "api", "asset": "region-api"},
        {"slot": "certs", "asset": "c0062-certs"},
    ]


def test_merge_assets_child_wins_on_same_slot():
    merged = merge_deployment_specs(
        {"assets": [{"slot": "api", "asset": "region-api"}, {"slot": "certs", "asset": "base-certs"}]},
        {"assets": [{"slot": "api", "asset": "c0062-api"}]},
    )
    assert merged["assets"] == [
        {"slot": "api", "asset": "c0062-api"},
        {"slot": "certs", "asset": "base-certs"},
    ]


def test_merge_unkeyed_lists_are_replaced():
    """A list not in the merge-key table is still replaced wholesale."""
    merged = merge_deployment_specs(
        {"configurations": ["base-config"]},
        {"configurations": ["child-config"]},
    )
    assert merged["configurations"] == ["child-config"]


def test_merge_keyed_list_never_merges_keyless_items():
    """An item without its key has no identity, so it is kept as-is rather than collapsed into another."""
    merged = merge_deployment_specs(
        {"providers": [{"provider": "a"}]},
        {"providers": [{"provider": "b"}, {"slot": "azure", "provider": "c"}]},
    )
    assert merged["providers"] == [{"provider": "a"}, {"provider": "b"}, {"slot": "azure", "provider": "c"}]


def test_merge_tenant_defaults_layer_keeps_the_deployments_provider_bindings():
    """Tenant defaults merge underneath a deployment via the same function; they carry no
    `providers`, so the deployment's bindings must pass through untouched."""
    merged = merge_deployment_specs(
        {"environments": ["tenant-env"], "properties": {}, "custom": {}},
        {"environments": ["prd"], "providers": [{"slot": "azure", "provider": "azure-north"}]},
    )
    assert merged["providers"] == [{"slot": "azure", "provider": "azure-north"}]
    assert merged["environments"] == ["tenant-env", "prd"]


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
    result = service.validate()
    assert result.ok, result.messages()
    assert service.model.spec.workspace == "control-workspace"
    assert service.model.spec.stages[0].step == "infra"


def test_merge_does_not_mutate_inputs():
    """Merging is pure — callers may reuse the base for several children."""
    base = {"stages": [{"step": "infra"}], "environments": ["core-env"]}
    child = {"stages": [{"step": "extra"}], "environments": ["leaf-env"]}
    merge_deployment_specs(base, child)
    assert base == {"stages": [{"step": "infra"}], "environments": ["core-env"]}
    assert child == {"stages": [{"step": "extra"}], "environments": ["leaf-env"]}
