#!/usr/bin/env python3
"""Tests for ProvisionerModel/ProvisioningStepModel validation."""

import pytest
from pydantic import ValidationError

from strata.models.provisioning_model import (
    ProvisionerModel,
    ProvisioningStepModel,
    validate_provisioning_steps,
)


def _minimal_provisioner() -> dict:
    return {
        "name": "terraform-main",
        "tool": "terraform",
        "source": {"remote": "infra-repo", "source_path": "terraform/main"},
    }


def test_provisioner_minimal_is_valid():
    """A minimal provisioner (terraform, with source) validates successfully."""
    model = ProvisionerModel.model_validate(_minimal_provisioner())
    assert model.name == "terraform-main"
    assert model.tool == "terraform"
    assert model.source.remote == "infra-repo"


def test_provisioner_terraform_requires_source():
    """terraform (a non-sync tool) requires a source."""
    data = _minimal_provisioner()
    del data["source"]
    with pytest.raises(ValidationError):
        ProvisionerModel.model_validate(data)


def test_provisioner_sync_type_does_not_require_source():
    """argocd/flux (sync/GitOps types) don't require a source."""
    model = ProvisionerModel.model_validate({"name": "argocd-main", "tool": "argocd"})
    assert model.source is None


def test_provisioner_unknown_tool_does_not_require_source():
    """An unrecognized (custom plugin) tool isn't forced to have a source either way.

    Its own plugin code is the only consumer, and could legitimately be a
    sync-like tool by its own design, not just built-in argocd/flux.
    """
    model = ProvisionerModel.model_validate({"name": "pulumi-main", "tool": "pulumi"})
    assert model.source is None


def test_provisioner_backend_accepted_for_terraform():
    """backend is accepted when tool is terraform."""
    data = _minimal_provisioner()
    data["backend"] = {"type": "azurerm", "configuration": {"resource_group_name": "tfstate-rg"}}
    model = ProvisionerModel.model_validate(data)
    assert model.backend.type == "azurerm"


def test_provisioner_backend_rejected_for_non_terraform():
    """backend is rejected when tool is a known non-terraform type."""
    data = {
        "name": "ansible-init",
        "tool": "ansible",
        "source": {"remote": "infra-repo", "source_path": "ansible/init"},
        "backend": {"type": "azurerm", "configuration": {}},
    }
    with pytest.raises(ValidationError):
        ProvisionerModel.model_validate(data)


def test_provisioner_backend_allowed_for_unknown_tool():
    """backend isn't rejected for an unrecognized (custom plugin) tool.

    Its own plugin code, not strata core, is the consumer of `backend` for a
    custom tool — the schema has no basis to say it's wrong.
    """
    data = {
        "name": "custom-main",
        "tool": "pulumi",
        "source": {"remote": "infra-repo", "source_path": "pulumi/main"},
        "backend": {"type": "s3", "configuration": {}},
    }
    model = ProvisionerModel.model_validate(data)
    assert model.backend.type == "s3"


def test_provisioner_backend_accepted_for_opentofu():
    """backend is accepted for opentofu — a recognized, terraform-compatible tool."""
    data = {
        "name": "opentofu-main",
        "tool": "opentofu",
        "source": {"remote": "infra-repo", "source_path": "terraform/main"},
        "backend": {"type": "azurerm", "configuration": {"resource_group_name": "tfstate-rg"}},
    }
    model = ProvisionerModel.model_validate(data)
    assert model.backend.type == "azurerm"


def test_provisioner_output_template_accepted_for_terraform():
    data = _minimal_provisioner()
    data["output"] = {"template": "variables.json.j2"}
    model = ProvisionerModel.model_validate(data)
    assert model.output.template == "variables.json.j2"


def test_provisioner_output_template_accepted_for_ansible():
    """Unlike `backend`/`properties`, `output` is valid for any tool — ADR-0023 D3's
    "same field, same mechanism on both pipelines" (tool-agnostic escape hatch)."""
    data = {
        "name": "ansible-init",
        "tool": "ansible",
        "source": {"remote": "infra-repo", "source_path": "ansible/init"},
        "output": {"template": "extra-vars.json.j2"},
    }
    model = ProvisionerModel.model_validate(data)
    assert model.output.template == "extra-vars.json.j2"


def test_provisioner_output_is_optional():
    model = ProvisionerModel.model_validate(_minimal_provisioner())
    assert model.output is None


def test_provisioner_opentofu_requires_source():
    """opentofu (a recognized, non-sync tool) requires a source, same as terraform."""
    with pytest.raises(ValidationError):
        ProvisionerModel.model_validate({"name": "opentofu-main", "tool": "opentofu"})


def test_provisioner_properties_accepted_for_ansible():
    """properties is accepted when tool is ansible."""
    data = {
        "name": "ansible-init",
        "tool": "ansible",
        "source": {"remote": "infra-repo", "source_path": "ansible/init"},
        "properties": {"playbook": "site.yml"},
    }
    model = ProvisionerModel.model_validate(data)
    assert model.properties.playbook == "site.yml"


def test_provisioner_properties_rejected_for_non_ansible():
    """properties is rejected when tool is a known non-ansible type."""
    data = _minimal_provisioner()
    data["properties"] = {"playbook": "site.yml"}
    with pytest.raises(ValidationError):
        ProvisionerModel.model_validate(data)


def test_provisioner_properties_allowed_for_unknown_tool():
    """properties isn't rejected for an unrecognized (custom plugin) tool."""
    data = {
        "name": "custom-main",
        "tool": "pulumi",
        "source": {"remote": "infra-repo", "source_path": "pulumi/main"},
        "properties": {"playbook": "site.yml"},
    }
    model = ProvisionerModel.model_validate(data)
    assert model.properties.playbook == "site.yml"


def test_provisioner_accepts_configuration_passthrough():
    """configuration is a freeform passthrough dict, unvalidated."""
    data = _minimal_provisioner()
    data["configuration"] = {"skip_provider_registration": True}
    model = ProvisionerModel.model_validate(data)
    assert model.configuration == {"skip_provider_registration": True}


def test_provisioner_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_provisioner()
    data["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        ProvisionerModel.model_validate(data)


# ---------------------------------------------------------------------------
# integration (ADR-0021 D4) — names an Integration document; the sole owner
# of the expected tool version. `.version` was removed from this model.
# ---------------------------------------------------------------------------


def test_provisioner_has_no_version_field():
    """Removed by ADR-0021 D4 — the Integration document owns this fact now."""
    assert "version" not in ProvisionerModel.model_fields


def test_provisioner_accepts_integration_reference():
    data = _minimal_provisioner()
    data["integration"] = "terraform-main"
    model = ProvisionerModel.model_validate(data)
    assert model.integration == "terraform-main"


def test_provisioner_integration_is_optional():
    model = ProvisionerModel.model_validate(_minimal_provisioner())
    assert model.integration is None


def test_provisioner_integration_valid_for_any_tool():
    """Unlike v1 (terraform/ansible/bicep only) — every tool eventually needs
    a binding, not just the ones v1 built CLI checks for."""
    data = {
        "name": "forge-apps",
        "tool": "helm",
        "source": {"remote": "charts-repo", "source_path": "charts/forge"},
        "integration": "helm-main",
    }
    model = ProvisionerModel.model_validate(data)
    assert model.tool == "helm"
    assert model.integration == "helm-main"


# ---------------------------------------------------------------------------
# ProvisioningStepModel
# ---------------------------------------------------------------------------


def _minimal_step(**overrides) -> dict:
    data = {"name": "provision-infra", "provisioner": "terraform-main", "targets": ["aks_cluster", "blobstore"]}
    data.update(overrides)
    return data


def test_provisioning_step_minimal_is_valid():
    """A minimal provisioning step validates successfully."""
    model = ProvisioningStepModel.model_validate(_minimal_step())
    assert model.name == "provision-infra"
    assert model.targets == ["aks_cluster", "blobstore"]


def test_provisioning_step_rejects_empty_targets():
    """An empty targets list is rejected (min_length=1)."""
    data = _minimal_step(targets=[])
    with pytest.raises(ValidationError):
        ProvisioningStepModel.model_validate(data)


def test_provisioning_step_rejects_duplicate_targets():
    """Duplicate targets within a step are rejected."""
    data = _minimal_step(targets=["aks_cluster", "aks_cluster"])
    with pytest.raises(ValidationError):
        ProvisioningStepModel.model_validate(data)


def test_provisioning_step_rejects_self_dependency():
    """A step cannot depend on itself."""
    data = _minimal_step(depends_on=["provision-infra"])
    with pytest.raises(ValidationError):
        ProvisioningStepModel.model_validate(data)


def test_provisioning_step_accepts_depends_on():
    """depends_on accepts references to other step names (existence checked at the container level)."""
    data = _minimal_step(depends_on=["provision-network"])
    model = ProvisioningStepModel.model_validate(data)
    assert model.depends_on == ["provision-network"]


# ---------------------------------------------------------------------------
# validate_provisioning_steps (cross-step validation)
# ---------------------------------------------------------------------------


def test_validate_provisioning_steps_accepts_ordered_shared_target():
    """Two steps sharing a target are fine when one depends_on the other."""
    steps = [
        ProvisioningStepModel.model_validate({"name": "infra", "provisioner": "tf", "targets": ["aks_cluster"]}),
        ProvisioningStepModel.model_validate(
            {"name": "init", "provisioner": "ansible", "targets": ["aks_cluster"], "depends_on": ["infra"]}
        ),
    ]
    validate_provisioning_steps(steps)  # should not raise


def test_validate_provisioning_steps_rejects_ambiguous_shared_target():
    """Two steps sharing a target with no ordering between them are rejected."""
    steps = [
        ProvisioningStepModel.model_validate({"name": "a", "provisioner": "tf", "targets": ["aks_cluster"]}),
        ProvisioningStepModel.model_validate({"name": "b", "provisioner": "ansible", "targets": ["aks_cluster"]}),
    ]
    with pytest.raises(ValueError, match="Ambiguous provisioning order"):
        validate_provisioning_steps(steps)


def test_validate_provisioning_steps_rejects_unknown_depends_on():
    """A depends_on referencing a step not in the list is rejected."""
    steps = [
        ProvisioningStepModel.model_validate(
            {"name": "a", "provisioner": "tf", "targets": ["x"], "depends_on": ["nonexistent"]}
        ),
    ]
    with pytest.raises(ValueError, match="not a defined step"):
        validate_provisioning_steps(steps)


def test_validate_provisioning_steps_rejects_cycle():
    """A cycle in depends_on is rejected."""
    steps = [
        ProvisioningStepModel.model_validate(
            {"name": "a", "provisioner": "tf", "targets": ["x"], "depends_on": ["b"]}
        ),
        ProvisioningStepModel.model_validate(
            {"name": "b", "provisioner": "tf", "targets": ["y"], "depends_on": ["a"]}
        ),
    ]
    with pytest.raises(ValueError, match="Circular dependency"):
        validate_provisioning_steps(steps)


def test_validate_provisioning_steps_rejects_duplicate_step_names():
    """Duplicate step names across the list are rejected."""
    steps = [
        ProvisioningStepModel.model_validate({"name": "a", "provisioner": "tf", "targets": ["x"]}),
        ProvisioningStepModel.model_validate({"name": "a", "provisioner": "tf", "targets": ["y"]}),
    ]
    with pytest.raises(ValueError, match="Duplicate"):
        validate_provisioning_steps(steps)


def test_validate_provisioning_steps_accepts_transitive_ordering():
    """Ordering can be transitive: c depends on b, b depends on a — c and a sharing a target is fine."""
    steps = [
        ProvisioningStepModel.model_validate({"name": "a", "provisioner": "tf", "targets": ["shared"]}),
        ProvisioningStepModel.model_validate(
            {"name": "b", "provisioner": "ansible", "targets": ["other"], "depends_on": ["a"]}
        ),
        ProvisioningStepModel.model_validate(
            {"name": "c", "provisioner": "script", "targets": ["shared"], "depends_on": ["b"]}
        ),
    ]
    validate_provisioning_steps(steps)  # should not raise


def test_validate_provisioning_steps_accepts_disjoint_targets_with_no_ordering():
    """Two steps with no shared targets and no depends_on are fine."""
    steps = [
        ProvisioningStepModel.model_validate({"name": "a", "provisioner": "tf", "targets": ["x"]}),
        ProvisioningStepModel.model_validate({"name": "b", "provisioner": "tf", "targets": ["y"]}),
    ]
    validate_provisioning_steps(steps)  # should not raise


def test_validate_provisioning_steps_empty_list_is_noop():
    """An empty steps list is a no-op."""
    validate_provisioning_steps([])
