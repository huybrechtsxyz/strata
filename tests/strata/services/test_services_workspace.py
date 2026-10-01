#!/usr/bin/env python3
"""Tests for WorkspaceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.topology_config_model import TopologyConfigModel
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


def _workspace_with_topology(**topology_overrides) -> dict:
    """A workspace with an inline topology block (ADR-0028). `components`'
    internal reference checks (`component.resource` must be in
    `spec.resources`) now run as Phase 1 `WorkspaceSpecModel` model
    validators — see `test_models_workspace.py` for those; this file only
    tests `WorkspaceService.validate_topology_components()`, the remaining
    Phase 2 registry (`TopologyConfigModel`) cross-check.
    """
    components = topology_overrides.pop(
        "components",
        [{"resource": "control-vm"}, {"resource": "worker-vm"}],
    )
    topology_type = topology_overrides.pop("type", "kubernetes")
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
            "topology": [
                {"name": "main-topology", "type": topology_type, "components": components, **topology_overrides}
            ],
        },
    }


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
    """A topology whose resource roles satisfy the registry's constraints passes."""
    service = WorkspaceService(data=_workspace_with_topology())
    service.validate()

    result = service.validate_topology_components(_configuration(), _topology_config_models())
    assert result.ok
    assert result.messages() == []


def test_validate_topology_components_rejects_missing_required_role():
    """A registry-required component role with zero matching resources is rejected."""
    data = _workspace_with_topology(components=[{"resource": "worker-vm"}])
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_topology_components(_configuration(), _topology_config_models())
    assert not result.ok
    assert any("control-plane" in m for m in result.messages())


def test_validate_topology_components_rejects_max_count_exceeded():
    """Exceeding a component role's registered max_count is rejected."""
    data = _workspace_with_topology(components=[{"resource": "control-vm"}, {"resource": "control-vm-2"}])
    data["spec"]["resources"].append({"name": "control-vm-2", "resource": "control-vm-class", "role": "control-plane"})
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_topology_components(_configuration(), _topology_config_models())
    assert not result.ok
    assert any("max" in m.lower() for m in result.messages())


def test_validate_topology_components_rejects_unregistered_type():
    """A topology type not in the registry is rejected unless additional_topologies is True."""
    data = _workspace_with_topology(type="dockerswarm", components=[{"resource": "control-vm"}])
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_topology_components(_configuration(), _topology_config_models())
    assert not result.ok
    assert any("dockerswarm" in m for m in result.messages())


def test_validate_topology_components_allows_unregistered_type_when_additional_topologies_true():
    """additional_topologies: True allows a topology type absent from the registry."""
    data = _workspace_with_topology(type="dockerswarm", components=[{"resource": "control-vm"}])
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_topology_components(_configuration(additional_topologies=True), _topology_config_models())
    assert result.ok
    assert result.messages() == []


def test_validate_topology_components_rejects_unregistered_role_without_additional_components():
    """A component role not in the topology type's registry entry is rejected when additional_components is False."""
    data = _workspace_with_topology(
        components=[{"resource": "control-vm"}, {"resource": "worker-vm"}, {"resource": "cache-vm"}]
    )
    data["spec"]["resources"].append({"name": "cache-vm", "resource": "cache-vm-class", "role": "cache"})
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_topology_components(_configuration(), _topology_config_models())
    assert not result.ok
    assert any("cache" in m for m in result.messages())


# ---------------------------------------------------------------------------
# claimed_document_names() — the ownership half of `${output:...}` resolution
# (docs/design/gap_fit_v1.md gap #12, generalized to namespaces so a Module reached
# through a targeted namespace counts as claimed too, not just directly-
# targeted DNS/network/firewall documents — docs/design/deploy-command.md's
# "Cross-invocation output access" section).
# ---------------------------------------------------------------------------


def _workspace_with_execution(**spec_overrides) -> dict:
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
            **spec_overrides,
        },
    }


def test_claimed_document_names_returns_empty_when_no_execution_steps():
    service = WorkspaceService(data=_workspace_with_execution(dns_zones=["public-dns"]))
    assert service.validate().ok
    assert service.claimed_document_names() == set()


def test_claimed_document_names_returns_empty_before_validate():
    """No model loaded yet — must not raise."""
    service = WorkspaceService(data=_workspace_with_execution())
    assert service.claimed_document_names() == set()


def test_claimed_document_names_includes_a_targeted_dns_zone():
    data = _workspace_with_execution(
        dns_zones=["public-dns"],
        resources=[{"name": "vm", "resource": "vm-class", "role": "node"}],
        execution=[{"name": "apply", "provisioner": "terraform-main", "targets": ["vm", "public-dns"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    # A resource ("vm") is a valid target too, but is never itself claimable —
    # it has no Value-token-bearing field of its own (see the method's own
    # docstring). Only "public-dns" is claimable here.
    assert service.claimed_document_names() == {"public-dns"}


def test_claimed_document_names_includes_a_targeted_namespace_not_just_dns_network_firewall():
    """gap #12's rule generalized: a namespace is claimable too, so a Module
    reached through it inherits the claim (docs/design/deploy-command.md's
    "Expanded finding" — modules hit the identical bug DNS/network/firewall
    did)."""
    data = _workspace_with_execution(
        namespaces=["apps"],
        execution=[{"name": "deploy", "provisioner": "terraform-main", "targets": ["apps"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"apps"}


def test_claimed_document_names_excludes_an_untargeted_document():
    data = _workspace_with_execution(
        dns_zones=["public-dns"],
        resources=[{"name": "vm", "resource": "vm-class", "role": "node"}],
        execution=[{"name": "apply", "provisioner": "terraform-main", "targets": ["vm"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    # "public-dns" is never targeted by any step, so it's excluded — and
    # "vm" (a resource) is never claimable at all regardless of targeting.
    assert service.claimed_document_names() == set()
