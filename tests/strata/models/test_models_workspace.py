#!/usr/bin/env python3
"""Tests for WorkspaceModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.workspace_model import WorkspaceModel


def _minimal_workspace() -> dict:
    return {
        "meta": {"name": "myapp-workspace"},
        "spec": {
            "providers": ["azure-main"],
            "provisioners": [
                {
                    "name": "terraform-main",
                    "tool": "terraform",
                    "source": {"remote": "infra-repo", "source_path": "terraform/main"},
                }
            ],
        },
    }


def test_workspace_minimal_is_valid():
    """A minimal workspace document (only required fields) validates successfully."""
    model = WorkspaceModel.model_validate(_minimal_workspace())
    assert model.meta.name == "myapp-workspace"
    assert model.spec.providers[0] == "azure-main"
    assert model.spec.provisioners[0].tool == "terraform"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "workspace"


def test_workspace_requires_at_least_one_provider():
    """spec.providers must be non-empty."""
    data = _minimal_workspace()
    data["spec"]["providers"] = []
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_requires_at_least_one_provisioner():
    """spec.provisioners must be non-empty."""
    data = _minimal_workspace()
    data["spec"]["provisioners"] = []
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_topology_is_optional():
    """spec.topology may be omitted entirely (unlike v1) — Topology is optional grouping."""
    model = WorkspaceModel.model_validate(_minimal_workspace())
    assert model.spec.topology is None


def test_workspace_accepts_topology_reference():
    """spec.topology accepts a plain Topology document name, resolved by discovery."""
    data = _minimal_workspace()
    data["spec"]["topology"] = ["aks-platform"]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.topology[0] == "aks-platform"


def test_workspace_rejects_duplicate_provider_names():
    """Duplicate provider names are rejected."""
    data = _minimal_workspace()
    data["spec"]["providers"].append(data["spec"]["providers"][0])
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_rejects_duplicate_provisioner_names():
    """Duplicate provisioner names are rejected."""
    data = _minimal_workspace()
    data["spec"]["provisioners"].append(dict(data["spec"]["provisioners"][0]))
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


# ---------------------------------------------------------------------------
# WorkspaceResourceModel
# ---------------------------------------------------------------------------


def test_workspace_resource_requires_resource_or_managed_by():
    """A resource must name either a Resource document or managed_by."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "web-vm"}]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_resource_rejects_resource_and_managed_by_together():
    """A resource cannot have both a resource reference and managed_by."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "web-vm", "resource": "web-vm-class", "managed_by": "provisioner"}]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_resource_accepts_managed_by():
    """A resource with managed_by and no file is accepted."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "web-vm", "managed_by": "provisioner"}]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.resources[0].managed_by == "provisioner"


def test_workspace_resource_coerces_single_depends_on_string():
    """depends_on accepts a single string as shorthand for a one-element list."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [
        {"name": "db", "resource": "db-class"},
        {"name": "web-vm", "resource": "web-vm-class", "depends_on": "db"},
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.resources[1].depends_on == ["db"]


def test_workspace_resource_firewall_reference_must_exist():
    """A resource's firewall reference must exist in spec.firewalls."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "web-vm", "resource": "web-vm-class", "firewalls": ["web-fw"]}]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_resource_firewall_reference_valid():
    """A resource's firewall reference resolving to a real firewall is accepted."""
    data = _minimal_workspace()
    data["spec"]["firewalls"] = ["web-fw"]
    data["spec"]["resources"] = [{"name": "web-vm", "resource": "web-vm-class", "firewalls": ["web-fw"]}]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.resources[0].firewalls == ["web-fw"]


def test_workspace_resource_subnet_network_must_exist():
    """A resource's subnet.network reference must exist in spec.networks."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [
        {
            "name": "web-vm",
            "resource": "web-vm-class",
            "subnet": {"network": "vnet-main", "subnet": "web-subnet"},
        }
    ]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_resource_subnet_network_valid():
    """A resource's subnet.network resolving to a real workspace network is accepted."""
    data = _minimal_workspace()
    data["spec"]["networks"] = ["vnet-main"]
    data["spec"]["resources"] = [
        {
            "name": "web-vm",
            "resource": "web-vm-class",
            "subnet": {"network": "vnet-main", "subnet": "web-subnet"},
        }
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.resources[0].subnet.network == "vnet-main"
    assert model.spec.resources[0].subnet.subnet == "web-subnet"


# ---------------------------------------------------------------------------
# Execution recipe cross-references
# ---------------------------------------------------------------------------


def _workspace_with_execution(**step_overrides) -> dict:
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "aks_cluster", "resource": "aks-class"}]
    step = {"name": "provision-infra", "provisioner": "terraform-main", "targets": ["aks_cluster"]}
    step.update(step_overrides)
    data["spec"]["execution"] = [step]
    return data


def test_workspace_execution_step_accepted_with_valid_references():
    """An execution step referencing a real provisioner and target is accepted."""
    model = WorkspaceModel.model_validate(_workspace_with_execution())
    assert model.spec.execution[0].name == "provision-infra"


def test_workspace_execution_step_rejects_unknown_provisioner():
    """An execution step referencing an undefined provisioner is rejected."""
    data = _workspace_with_execution(provisioner="nonexistent")
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_execution_step_rejects_unknown_target():
    """An execution step targeting an undefined resource/namespace is rejected."""
    data = _workspace_with_execution(targets=["nonexistent"])
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_rejects_legacy_provisioning_key():
    """The recipe key is 'execution'; the old 'provisioning' name is not accepted."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "aks_cluster", "resource": "aks-class"}]
    data["spec"]["provisioning"] = [
        {"name": "provision-infra", "provisioner": "terraform-main", "targets": ["aks_cluster"]}
    ]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceModel.model_validate(data)


def test_workspace_execution_step_can_target_a_namespace():
    """An execution step may target a namespace, not just a resource."""
    data = _minimal_workspace()
    data["spec"]["namespaces"] = ["myapp"]
    data["spec"]["execution"] = [
        {"name": "deploy-app", "provisioner": "terraform-main", "targets": ["myapp"]}
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.execution[0].targets == ["myapp"]


def test_workspace_rejects_ambiguous_execution_order():
    """Two execution steps sharing a target with no depends_on ordering are rejected."""
    data = _minimal_workspace()
    data["spec"]["provisioners"].append(
        {
            "name": "ansible-init",
            "tool": "ansible",
            "source": {"remote": "infra-repo", "source_path": "ansible/init"},
        }
    )
    data["spec"]["resources"] = [{"name": "aks_cluster", "resource": "aks-class"}]
    data["spec"]["execution"] = [
        {"name": "a", "provisioner": "terraform-main", "targets": ["aks_cluster"]},
        {"name": "b", "provisioner": "ansible-init", "targets": ["aks_cluster"]},
    ]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_accepts_ordered_execution_steps():
    """Two execution steps sharing a target with an explicit depends_on are accepted."""
    data = _minimal_workspace()
    data["spec"]["provisioners"].append(
        {
            "name": "ansible-init",
            "tool": "ansible",
            "source": {"remote": "infra-repo", "source_path": "ansible/init"},
        }
    )
    data["spec"]["resources"] = [{"name": "aks_cluster", "resource": "aks-class"}]
    data["spec"]["execution"] = [
        {"name": "a", "provisioner": "terraform-main", "targets": ["aks_cluster"]},
        {"name": "b", "provisioner": "ansible-init", "targets": ["aks_cluster"], "depends_on": ["a"]},
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.execution[1].depends_on == ["a"]


def test_workspace_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_workspace()
    data["spec"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)
