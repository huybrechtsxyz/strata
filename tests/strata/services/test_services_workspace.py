#!/usr/bin/env python3
"""Tests for WorkspaceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.topology_model import TopologyModel
from strata.services.workspace_service import WorkspaceService


def test_workspace_service_validates_from_data():
    """A WorkspaceService constructed from an in-memory dict validates successfully."""
    data = {
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
    service = WorkspaceService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.providers[0] == "azure-main"


def _workspace_with_topology() -> dict:
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
            "resources": [
                {"name": "control-vm", "resource": "control-vm-class", "role": "control-plane"},
                {"name": "worker-vm", "resource": "worker-vm-class", "role": "worker"},
            ],
            "topology": ["main-topology"],
        },
    }


def _topology_model(**component_overrides) -> TopologyModel:
    components = component_overrides.pop(
        "components",
        [{"resource": "control-vm"}, {"resource": "worker-vm"}],
    )
    return TopologyModel.model_validate(
        {
            "meta": {"name": "main-topology"},
            "spec": {"type": "kubernetes", "components": components, **component_overrides},
        }
    )


def test_validate_topology_references_accepts_valid_component_refs():
    """A loaded Topology whose components/namespaces all resolve to real workspace entries passes."""
    service = WorkspaceService(data=_workspace_with_topology())
    assert service.validate().ok

    result = service.validate_topology_references({"main-topology": _topology_model()})
    assert result.ok
    assert result.messages() == []


def test_validate_topology_references_rejects_undefined_resource():
    """A Topology component referencing an unknown resource is rejected."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    topology = _topology_model(components=[{"resource": "control-vm"}, {"resource": "ghost-vm"}])
    result = service.validate_topology_references({"main-topology": topology})
    assert not result.ok
    assert any("ghost-vm" in m for m in result.messages())


def test_validate_topology_references_rejects_missing_loaded_topology():
    """A declared spec.topology[] reference with no corresponding loaded TopologyModel is an error."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    result = service.validate_topology_references({})
    assert not result.ok
    assert any("main-topology" in m for m in result.messages())


def _topology_config_models(**overrides) -> dict:
    components = overrides.pop(
        "components",
        [
            {"role": "control-plane", "required": True, "min_count": 1, "max_count": 1},
            {"role": "worker", "required": False, "min_count": 0, "max_count": 0, "uses_module": False},
        ],
    )
    model = TopologyConfigModel.model_validate(
        {"meta": {"name": "kubernetes"}, "spec": {"components": components, **overrides}}
    )
    return {"kubernetes": model}


def _configuration(**overrides) -> ConfigurationModel:
    return ConfigurationModel.model_validate({"meta": {"name": "solution-config"}, "spec": {**overrides}})


def test_validate_topology_components_accepts_matching_registry():
    """A Topology whose resolved resource roles satisfy the registry's constraints passes."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    result = service.validate_topology_components(
        _configuration(), _topology_config_models(), {"main-topology": _topology_model()}
    )
    assert result.ok
    assert result.messages() == []


def test_validate_topology_components_rejects_missing_required_role():
    """A registry-required component role with zero matching resources is rejected."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    topology = _topology_model(components=[{"resource": "worker-vm"}])
    result = service.validate_topology_components(
        _configuration(), _topology_config_models(), {"main-topology": topology}
    )
    assert not result.ok
    assert any("control-plane" in m for m in result.messages())


def test_validate_topology_components_rejects_max_count_exceeded():
    """Exceeding a component role's registered max_count is rejected."""
    data = _workspace_with_topology()
    data["spec"]["resources"].append(
        {"name": "control-vm-2", "resource": "control-vm-class", "role": "control-plane"}
    )
    service = WorkspaceService(data=data)
    service.validate()

    topology = _topology_model(components=[{"resource": "control-vm"}, {"resource": "control-vm-2"}])
    result = service.validate_topology_components(
        _configuration(), _topology_config_models(), {"main-topology": topology}
    )
    assert not result.ok
    assert any("max" in m.lower() for m in result.messages())


def test_validate_topology_components_rejects_unregistered_type():
    """A Topology type not in the registry is rejected unless additional_topologies is True."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    topology = TopologyModel.model_validate(
        {
            "meta": {"name": "main-topology"},
            "spec": {"type": "dockerswarm", "components": [{"resource": "control-vm"}]},
        }
    )
    result = service.validate_topology_components(
        _configuration(), _topology_config_models(), {"main-topology": topology}
    )
    assert not result.ok
    assert any("dockerswarm" in m for m in result.messages())


def test_validate_topology_components_allows_unregistered_type_when_additional_topologies_true():
    """additional_topologies: True allows a topology type absent from the registry."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    topology = TopologyModel.model_validate(
        {
            "meta": {"name": "main-topology"},
            "spec": {"type": "dockerswarm", "components": [{"resource": "control-vm"}]},
        }
    )
    result = service.validate_topology_components(
        _configuration(additional_topologies=True), _topology_config_models(), {"main-topology": topology}
    )
    assert result.ok
    assert result.messages() == []


def test_validate_topology_components_rejects_unregistered_role_without_additional_components():
    """A component role not in the topology type's registry entry is rejected when additional_components is False."""
    data = _workspace_with_topology()
    data["spec"]["resources"].append(
        {"name": "cache-vm", "resource": "cache-vm-class", "role": "cache"}
    )
    service = WorkspaceService(data=data)
    service.validate()

    topology = _topology_model(
        components=[{"resource": "control-vm"}, {"resource": "worker-vm"}, {"resource": "cache-vm"}]
    )
    result = service.validate_topology_components(
        _configuration(), _topology_config_models(), {"main-topology": topology}
    )
    assert not result.ok
    assert any("cache" in m for m in result.messages())
