#!/usr/bin/env python3
"""Tests for TopologyModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.topology_model import TopologyModel


def _minimal_topology() -> dict:
    return {
        "meta": {"name": "aks-platform"},
        "spec": {
            "type": "kubernetes",
            "components": [{"resource": "aks_cluster"}, {"resource": "blobstore"}, {"resource": "keyvault"}],
        },
    }


def test_topology_minimal_is_valid():
    """A minimal topology document (only required fields) validates successfully."""
    model = TopologyModel.model_validate(_minimal_topology())
    assert model.meta.name == "aks-platform"
    assert model.spec.type == "kubernetes"
    assert len(model.spec.components) == 3
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "topology"


def test_topology_accepts_namespaces_and_volumes():
    """A topology may declare namespaces and volumes alongside components."""
    data = _minimal_topology()
    data["spec"]["namespaces"] = [{"namespace": "myapp"}]
    data["spec"]["volumes"] = [{"name": "cache", "size": "10Gi"}]
    model = TopologyModel.model_validate(data)
    assert model.spec.namespaces[0].namespace == "myapp"
    assert model.spec.volumes[0].name == "cache"


def test_topology_rejects_duplicate_component_resources():
    """Duplicate resource references within a topology are rejected."""
    data = _minimal_topology()
    data["spec"]["components"].append({"resource": "aks_cluster"})
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_component_accepts_attached_module():
    """A resource may have a module attached directly (e.g. Function App code)."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "deployinfo", "file": "modules/deployinfo.yaml"},
    ]
    model = TopologyModel.model_validate(data)
    assert model.spec.components[0].modules[0].name == "deployinfo"
    assert model.spec.components[0].modules[0].slot_type == "main"


def test_topology_component_rejects_duplicate_module_names():
    """Duplicate module names attached to the same resource are rejected."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "deployinfo", "file": "modules/a.yaml"},
        {"name": "deployinfo", "file": "modules/b.yaml"},
    ]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_component_requires_one_main_slot_among_multiple_enabled_modules():
    """Multiple enabled modules on one resource with no 'main' slot are rejected."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "a", "file": "modules/a.yaml", "slot_type": "canary"},
        {"name": "b", "file": "modules/b.yaml", "slot_type": "canary"},
    ]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_component_rejects_multiple_main_slots():
    """Multiple enabled modules both marked 'main' on the same resource are rejected."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "a", "file": "modules/a.yaml", "slot_type": "main"},
        {"name": "b", "file": "modules/b.yaml", "slot_type": "main"},
    ]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_component_allows_multiple_modules_when_one_is_main():
    """Multiple enabled modules are fine as long as exactly one is 'main'."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "a", "file": "modules/a.yaml", "slot_type": "main"},
        {"name": "b", "file": "modules/b.yaml", "slot_type": "canary"},
    ]
    model = TopologyModel.model_validate(data)
    assert len(model.spec.components[0].modules) == 2


def test_topology_component_ignores_disabled_modules_for_main_slot_rule():
    """A disabled module doesn't count toward the 'must have exactly one main' rule."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [
        {"name": "a", "file": "modules/a.yaml", "slot_type": "canary", "enabled": False},
        {"name": "b", "file": "modules/b.yaml", "slot_type": "main"},
    ]
    model = TopologyModel.model_validate(data)
    assert model.spec.components[0].modules[0].enabled is False


def test_topology_component_module_rejects_path_traversal():
    """A module file reference escaping via '..' is rejected."""
    data = _minimal_topology()
    data["spec"]["components"][0]["modules"] = [{"name": "a", "file": "../../etc/passwd"}]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_duplicate_namespace_refs():
    """Duplicate namespace references within a topology are rejected."""
    data = _minimal_topology()
    data["spec"]["namespaces"] = [{"namespace": "myapp"}, {"namespace": "myapp"}]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_duplicate_volume_names():
    """Duplicate volume names within a topology are rejected."""
    data = _minimal_topology()
    data["spec"]["volumes"] = [{"name": "cache"}, {"name": "cache"}]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_empty_components():
    """An empty components list is rejected (min_length=1)."""
    data = _minimal_topology()
    data["spec"]["components"] = []
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_invalid_component_resource_syntax():
    """A resource reference that isn't a valid PlatformName (e.g. uppercase) is rejected.

    This only catches syntactically malformed names — it can't confirm the
    resource actually exists (that's Workspace's Phase 2 job, ADR-0011).
    """
    data = _minimal_topology()
    data["spec"]["components"][0]["resource"] = "AksCluster"
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_invalid_namespace_ref_syntax():
    """A namespace reference that isn't a valid PlatformName is rejected."""
    data = _minimal_topology()
    data["spec"]["namespaces"] = [{"namespace": "My App"}]
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_provider_field():
    """spec.provider is rejected — Topology is pure grouping, tool binding is a separate concern (ADR-0011)."""
    data = _minimal_topology()
    data["spec"]["provider"] = "azure"
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_provisioner_field():
    """spec.provisioner is rejected — Topology is pure grouping, tool binding is a separate concern (ADR-0011)."""
    data = _minimal_topology()
    data["spec"]["provisioner"] = "terraform-main"
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)


def test_topology_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_topology()
    data["spec"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        TopologyModel.model_validate(data)
