#!/usr/bin/env python3
"""Tests for WorkspaceModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.workspace_model import TopologySpecModel, WorkspaceModel


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


def test_workspace_accepts_inline_topology():
    """spec.topology accepts an inline topology block (ADR-0028, reverted from
    a standalone-document name reference, ADR-0011)."""
    data = _minimal_workspace()
    data["spec"]["resources"] = [{"name": "aks_cluster", "resource": "aks-cluster-class"}]
    data["spec"]["topology"] = [
        {"name": "aks-platform", "type": "kubernetes", "components": [{"resource": "aks_cluster"}]}
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.topology[0].name == "aks-platform"
    assert model.spec.topology[0].components[0].resource == "aks_cluster"


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
    data["spec"]["execution"] = [{"name": "deploy-app", "provisioner": "terraform-main", "targets": ["myapp"]}]
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


# ---------------------------------------------------------------------------
# Provisioner depends_on (ADR-0029) — materialising a source with no
# execution step; cross-document (sibling-provisioner) validation.
# ---------------------------------------------------------------------------


def _provisioner(name: str, **overrides) -> dict:
    data = {"name": name, "tool": "terraform", "source": {"remote": "infra-repo", "source_path": f"terraform/{name}"}}
    data.update(overrides)
    return data


def test_workspace_accepts_provisioner_depends_on_with_real_siblings():
    """A provisioner's depends_on resolving to real sibling provisioners is accepted."""
    data = _minimal_workspace()
    data["spec"]["provisioners"] = [
        _provisioner("spoke_infra", depends_on=["iac_components", "iac_primitives"]),
        _provisioner("iac_components"),
        _provisioner("iac_primitives"),
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.provisioners[0].depends_on == ["iac_components", "iac_primitives"]


def test_workspace_accepts_provisioner_with_no_execution_step_but_a_depends_on_consumer():
    """The actual point of ADR-0029: iac_components/iac_primitives need no
    execution step at all — they're reachable only via spoke_infra's own
    depends_on, never as a deployable step, so --scope/--stage filtering has
    nothing to accidentally include or exclude them from."""
    data = _minimal_workspace()
    data["spec"]["provisioners"] = [
        _provisioner("spoke_infra", depends_on=["iac_components", "iac_primitives"]),
        _provisioner("iac_components"),
        _provisioner("iac_primitives"),
    ]
    data["spec"]["resources"] = [{"name": "spoke_network", "resource": "network-class"}]
    data["spec"]["execution"] = [
        {"name": "spoke_infra_step", "provisioner": "spoke_infra", "targets": ["spoke_network"]}
    ]
    model = WorkspaceModel.model_validate(data)
    step_provisioners = {step.provisioner for step in model.spec.execution}
    assert step_provisioners == {"spoke_infra"}  # iac_components/iac_primitives are not, and need not be, steps


def test_workspace_rejects_unknown_provisioner_depends_on():
    """A provisioner's depends_on referencing an undeclared provisioner is rejected."""
    data = _minimal_workspace()
    data["spec"]["provisioners"][0]["depends_on"] = ["nonexistent"]
    with pytest.raises(ValidationError, match="not a declared provisioner"):
        WorkspaceModel.model_validate(data)


def test_workspace_rejects_provisioner_depends_on_cycle():
    """A cycle in provisioner depends_on (independent of any execution step) is rejected."""
    data = _minimal_workspace()
    data["spec"]["provisioners"] = [
        _provisioner("a", depends_on=["b"]),
        _provisioner("b", depends_on=["a"]),
    ]
    with pytest.raises(ValidationError, match="Circular dependency in provisioner depends_on"):
        WorkspaceModel.model_validate(data)


def test_workspace_accepts_transitive_provisioner_depends_on_chain():
    """A depends on B depends on C — a transitive chain with no cycle is accepted."""
    data = _minimal_workspace()
    data["spec"]["provisioners"] = [
        _provisioner("a", depends_on=["b"]),
        _provisioner("b", depends_on=["c"]),
        _provisioner("c"),
    ]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.provisioners[1].depends_on == ["c"]


# ---------------------------------------------------------------------------
# TopologySpecModel (inline on WorkspaceSpecModel.topology, ADR-0028)
# ---------------------------------------------------------------------------


def _minimal_topology() -> dict:
    return {
        "name": "aks-platform",
        "type": "kubernetes",
        "components": [{"resource": "aks_cluster"}, {"resource": "blobstore"}, {"resource": "keyvault"}],
    }


def test_topology_minimal_is_valid():
    """A minimal inline topology block (only required fields) validates successfully."""
    model = TopologySpecModel.model_validate(_minimal_topology())
    assert model.name == "aks-platform"
    assert model.type == "kubernetes"
    assert len(model.components) == 3


def test_topology_accepts_namespaces_and_volumes():
    """A topology may declare namespaces and volumes alongside components."""
    data = _minimal_topology()
    data["namespaces"] = [{"namespace": "myapp"}]
    data["volumes"] = [{"name": "cache", "size": "10Gi"}]
    model = TopologySpecModel.model_validate(data)
    assert model.namespaces[0].namespace == "myapp"
    assert model.volumes[0].name == "cache"


def test_topology_rejects_duplicate_component_resources():
    """Duplicate resource references within a topology are rejected."""
    data = _minimal_topology()
    data["components"].append({"resource": "aks_cluster"})
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_component_accepts_attached_module():
    """A resource may have a module attached directly (e.g. Function App code)."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [{"name": "deployinfo", "module": "deployinfo"}]
    model = TopologySpecModel.model_validate(data)
    assert model.components[0].modules[0].name == "deployinfo"
    assert model.components[0].modules[0].slot_type == "main"


def test_topology_component_rejects_duplicate_module_names():
    """Duplicate module names attached to the same resource are rejected."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [
        {"name": "deployinfo", "module": "mod-a"},
        {"name": "deployinfo", "module": "mod-b"},
    ]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_component_requires_one_main_slot_among_multiple_enabled_modules():
    """Multiple enabled modules on one resource with no 'main' slot are rejected."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [
        {"name": "a", "module": "mod-a", "slot_type": "canary"},
        {"name": "b", "module": "mod-b", "slot_type": "canary"},
    ]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_component_rejects_multiple_main_slots():
    """Multiple enabled modules both marked 'main' on the same resource are rejected."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [
        {"name": "a", "module": "mod-a", "slot_type": "main"},
        {"name": "b", "module": "mod-b", "slot_type": "main"},
    ]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_component_allows_multiple_modules_when_one_is_main():
    """Multiple enabled modules are fine as long as exactly one is 'main'."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [
        {"name": "a", "module": "mod-a", "slot_type": "main"},
        {"name": "b", "module": "mod-b", "slot_type": "canary"},
    ]
    model = TopologySpecModel.model_validate(data)
    assert len(model.components[0].modules) == 2


def test_topology_component_ignores_disabled_modules_for_main_slot_rule():
    """A disabled module doesn't count toward the 'must have exactly one main' rule."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [
        {"name": "a", "module": "mod-a", "slot_type": "canary", "enabled": False},
        {"name": "b", "module": "mod-b", "slot_type": "main"},
    ]
    model = TopologySpecModel.model_validate(data)
    assert model.components[0].modules[0].enabled is False


def test_topology_component_module_rejects_path_like_name():
    """A module reference names a document, not a path — '../' shapes fail PlatformName."""
    data = _minimal_topology()
    data["components"][0]["modules"] = [{"name": "a", "module": "../../etc/passwd"}]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_duplicate_namespace_refs():
    """Duplicate namespace references within a topology are rejected."""
    data = _minimal_topology()
    data["namespaces"] = [{"namespace": "myapp"}, {"namespace": "myapp"}]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_duplicate_volume_names():
    """Duplicate volume names within a topology are rejected."""
    data = _minimal_topology()
    data["volumes"] = [{"name": "cache"}, {"name": "cache"}]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_empty_components():
    """An empty components list is rejected (min_length=1)."""
    data = _minimal_topology()
    data["components"] = []
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_invalid_component_resource_syntax():
    """A resource reference that isn't a valid PlatformName (e.g. uppercase) is rejected.

    This only catches syntactically malformed names — existence-checking
    against the workspace's own `spec.resources` is a `WorkspaceSpecModel`
    cross-field validator now that topology is inline (see
    `test_workspace_rejects_topology_component_undefined_resource` below,
    ADR-0028).
    """
    data = _minimal_topology()
    data["components"][0]["resource"] = "AksCluster"
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_invalid_namespace_ref_syntax():
    """A namespace reference that isn't a valid PlatformName is rejected."""
    data = _minimal_topology()
    data["namespaces"] = [{"namespace": "My App"}]
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_provider_field():
    """provider is rejected — Topology is pure grouping, tool binding is a separate concern (ADR-0011)."""
    data = _minimal_topology()
    data["provider"] = "azure"
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_provisioner_field():
    """provisioner is rejected — Topology is pure grouping, tool binding is a separate concern (ADR-0011)."""
    data = _minimal_topology()
    data["provisioner"] = "terraform-main"
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


def test_topology_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_topology()
    data["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        TopologySpecModel.model_validate(data)


# ---------------------------------------------------------------------------
# WorkspaceSpecModel <-> inline topology cross-references (ADR-0028's Phase 1
# model validators, replacing WorkspaceService.validate_topology_references())
# ---------------------------------------------------------------------------


def _workspace_with_topology(**topology_overrides) -> dict:
    data = _minimal_workspace()
    data["spec"]["resources"] = [
        {"name": "aks_cluster", "resource": "aks-cluster-class"},
        {"name": "blobstore", "resource": "storage-account"},
    ]
    topology = _minimal_topology()
    topology["components"] = [{"resource": "aks_cluster"}, {"resource": "blobstore"}]
    topology.update(topology_overrides)
    data["spec"]["topology"] = [topology]
    return data


def test_workspace_rejects_topology_component_undefined_resource():
    """A topology component referencing a resource not in spec.resources is rejected
    (Phase 1, same-document — possible only because topology is inline, ADR-0028)."""
    data = _workspace_with_topology()
    data["spec"]["topology"][0]["components"].append({"resource": "ghost-vm"})
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_rejects_topology_namespace_undefined():
    """A topology namespace reference not in spec.namespaces is rejected (Phase 1, ADR-0028)."""
    data = _workspace_with_topology()
    data["spec"]["topology"][0]["namespaces"] = [{"namespace": "ghost-ns"}]
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_accepts_topology_namespace_reference():
    """A topology namespace reference that does exist in spec.namespaces is accepted."""
    data = _workspace_with_topology()
    data["spec"]["namespaces"] = ["ghost-ns"]
    data["spec"]["topology"][0]["namespaces"] = [{"namespace": "ghost-ns"}]
    model = WorkspaceModel.model_validate(data)
    assert model.spec.topology[0].namespaces[0].namespace == "ghost-ns"


def test_workspace_rejects_duplicate_topology_names():
    """Duplicate topology names within a workspace are rejected."""
    data = _workspace_with_topology()
    data["spec"]["topology"].append(dict(data["spec"]["topology"][0]))
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


# ---------------------------------------------------------------------------
# spec.cve_policy (docs/work/cve-scanner-integration.md)
# ---------------------------------------------------------------------------


def test_workspace_cve_policy_is_optional():
    """spec.cve_policy may be omitted entirely — the common case."""
    model = WorkspaceModel.model_validate(_minimal_workspace())
    assert model.spec.cve_policy is None


def test_workspace_accepts_cve_policy_with_defaults():
    """A minimal cve_policy (only the required max_severity) fills in its own defaults."""
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {"max_severity": "HIGH"}
    model = WorkspaceModel.model_validate(data)
    policy = model.spec.cve_policy
    assert policy.max_severity == "HIGH"
    assert policy.max_count == 0
    assert policy.severity_threshold is None
    assert policy.enforcement == "deny"
    assert policy.on_missing_data == "block"


def test_workspace_accepts_fully_specified_cve_policy():
    """Every cve_policy field can be set explicitly."""
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {
        "max_severity": "CRITICAL",
        "max_count": 3,
        "severity_threshold": "LOW",
        "enforcement": "warn",
        "on_missing_data": "skip",
    }
    model = WorkspaceModel.model_validate(data)
    policy = model.spec.cve_policy
    assert policy.max_count == 3
    assert policy.severity_threshold == "LOW"
    assert policy.enforcement == "warn"
    assert policy.on_missing_data == "skip"


def test_workspace_cve_policy_rejects_invalid_max_severity():
    """max_severity only accepts CRITICAL/HIGH/MEDIUM/LOW — not UNKNOWN (gating on 'unknown
    severity' findings is meaningless)."""
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {"max_severity": "UNKNOWN"}
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_cve_policy_rejects_negative_max_count():
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {"max_severity": "HIGH", "max_count": -1}
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_cve_policy_rejects_unknown_fields():
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {"max_severity": "HIGH", "unknown_field": "oops"}
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)


def test_workspace_cve_policy_rejects_a_threshold_more_restrictive_than_max_severity():
    data = _minimal_workspace()
    data["spec"]["cve_policy"] = {"max_severity": "LOW", "severity_threshold": "HIGH"}
    with pytest.raises(ValidationError, match="more restrictive"):
        WorkspaceModel.model_validate(data)
