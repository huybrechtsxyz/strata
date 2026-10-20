#!/usr/bin/env python3
"""Tests for WorkspaceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel, ConfigurationSecurityModel
from strata.models.deployment_model import DeploymentModel
from strata.models.network_model import NetworkModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.workspace_model import WorkspaceAssetModel, WorkspaceModel
from strata.services.workspace_service import WorkspaceService, effective_assets, effective_providers


def _slotted_workspace() -> WorkspaceModel:
    return WorkspaceModel.model_validate(
        {
            "meta": {"name": "apim"},
            "spec": {
                "providers": ["azure-shared", {"slot": "azure", "provider": "azure-westeurope"}],
                "provisioners": [{"name": "tf", "tool": "terraform", "source": {"source_path": "infra"}}],
            },
        }
    )


def _deployment_binding(*bindings: dict) -> DeploymentModel:
    return DeploymentModel.model_validate(
        {"meta": {"name": "d"}, "spec": {"workspace": "apim", "environments": ["prd"], "providers": list(bindings)}}
    )


def test_effective_providers_without_deployment_is_the_workspace_default():
    assert effective_providers(_slotted_workspace(), None) == {
        "azure-shared": "azure-shared",
        "azure": "azure-westeurope",
    }


def test_effective_providers_overlays_deployment_binding():
    deployment = _deployment_binding({"slot": "azure", "provider": "azure-americas"})
    assert effective_providers(_slotted_workspace(), deployment) == {
        "azure-shared": "azure-shared",
        "azure": "azure-americas",
    }


def test_effective_providers_ignores_unknown_slot():
    """Reporting an unknown slot is validation's job; it never adds a provider here."""
    deployment = _deployment_binding({"slot": "ghost", "provider": "azure-americas"})
    assert "ghost" not in effective_providers(_slotted_workspace(), deployment)


def test_effective_assets_without_assets_is_empty():
    assert effective_assets(_slotted_workspace(), None) == {}


def test_effective_assets_overlays_deployment_binding():
    workspace = _slotted_workspace()
    workspace.spec.assets = [
        WorkspaceAssetModel.model_validate(x) for x in ("openapi", {"slot": "api", "asset": "api-x"})
    ]
    deployment = DeploymentModel.model_validate(
        {
            "meta": {"name": "d"},
            "spec": {
                "workspace": "apim",
                "environments": ["prd"],
                "assets": [{"slot": "api", "asset": "c0062-api"}, {"slot": "ghost", "asset": "x"}],
            },
        }
    )

    assert effective_assets(workspace, None) == {"openapi": "openapi", "api": "api-x"}
    # an unknown slot is ignored here; validation reports it
    assert effective_assets(workspace, deployment) == {"openapi": "openapi", "api": "c0062-api"}


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
    assert service.model.spec.providers[0].provider == "azure-main"


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
# validate_resource_subnets() — WorkspaceResourceSubnetModel.subnet against a
# real, loaded Network document's subnet names (docs/work/solution-loading-
# and-phase2-validation.md). `.network` (which Network *document*) is
# already checked in Phase 1 (`validate_resource_subnet_references`,
# `workspace_model.py`); this is only the Phase 2 half.
# ---------------------------------------------------------------------------


def _workspace_with_subnet(network: str = "main-network", subnet: str = "app") -> dict:
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
            "networks": [network],
            "resources": [
                {
                    "name": "app-vm",
                    "resource": "app-vm-class",
                    "subnet": {"network": network, "subnet": subnet},
                },
            ],
        },
    }


def _network_model(**overrides) -> NetworkModel:
    networks = overrides.pop(
        "networks",
        [
            {
                "name": "vnet-main",
                "address_space": ["10.0.0.0/16"],
                "subnets": [{"name": "app", "cidr": "10.0.1.0/24"}],
            }
        ],
    )
    return NetworkModel.model_validate({"meta": {"name": "main-network"}, "spec": {"networks": networks, **overrides}})


def test_validate_resource_subnets_accepts_a_real_subnet():
    service = WorkspaceService(data=_workspace_with_subnet())
    service.validate()

    result = service.validate_resource_subnets({"main-network": _network_model()})
    assert result.ok
    assert result.messages() == []


def test_validate_resource_subnets_rejects_unknown_subnet_name():
    service = WorkspaceService(data=_workspace_with_subnet(subnet="ghost-subnet"))
    service.validate()

    result = service.validate_resource_subnets({"main-network": _network_model()})
    assert not result.ok
    assert any("ghost-subnet" in m for m in result.messages())


def test_validate_resource_subnets_checks_every_internal_network_in_the_document():
    """A Network document can declare more than one independently-named
    network (docs/config/network.md) — `subnet.network` names the document,
    not which internal network, so a subnet declared under a *second*
    internal network in the same document must still resolve."""
    service = WorkspaceService(data=_workspace_with_subnet(subnet="db"))
    service.validate()

    network = _network_model(
        networks=[
            {
                "name": "vnet-main",
                "address_space": ["10.0.0.0/16"],
                "subnets": [{"name": "app", "cidr": "10.0.1.0/24"}],
            },
            {"name": "vnet-data", "address_space": ["10.1.0.0/16"], "subnets": [{"name": "db", "cidr": "10.1.1.0/24"}]},
        ]
    )
    result = service.validate_resource_subnets({"main-network": network})
    assert result.ok
    assert result.messages() == []


def test_validate_resource_subnets_skips_an_unresolved_network_reference():
    """A network name missing from the resolved dict (an unresolved
    reference) is silently skipped — `validate_references` already reports
    it; this method must not duplicate that finding or raise."""
    service = WorkspaceService(data=_workspace_with_subnet())
    service.validate()

    result = service.validate_resource_subnets({})
    assert result.ok
    assert result.messages() == []


def test_validate_resource_subnets_ignores_resources_with_no_subnet():
    data = _workspace_with_subnet()
    data["spec"]["resources"].append({"name": "other-vm", "resource": "other-vm-class"})
    service = WorkspaceService(data=data)
    service.validate()

    result = service.validate_resource_subnets({"main-network": _network_model()})
    assert result.ok


# ---------------------------------------------------------------------------
# validate_allowed_key_value_store() — docs/work/cross-run-step-output-
# delivery.md's Implementation Plan step 1 ('Governance'). Takes the
# already-resolved backend type directly (the controller's job to resolve
# output_store_integration -> its target document's spec.type), unlike
# validate_allowed_stores() which walks spec.variables/secrets/features.
# ---------------------------------------------------------------------------


def _minimal_workspace_service() -> WorkspaceService:
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
    service.validate()
    return service


def _security(**overrides) -> ConfigurationSecurityModel:
    return ConfigurationSecurityModel.model_validate(overrides)


def test_validate_allowed_key_value_store_accepts_a_listed_type():
    service = _minimal_workspace_service()

    result = service.validate_allowed_key_value_store(
        _security(allowed_key_value_stores=["azure-appconfig"]), "azure-appconfig"
    )
    assert result.ok
    assert result.messages() == []


def test_validate_allowed_key_value_store_rejects_an_unlisted_type():
    service = _minimal_workspace_service()

    result = service.validate_allowed_key_value_store(_security(allowed_key_value_stores=["azure-appconfig"]), "consul")
    assert not result.ok
    assert any("consul" in m and "allowed_key_value_stores" in m for m in result.messages())


def test_validate_allowed_key_value_store_unrestricted_when_none():
    """allowed_key_value_stores unset (None) means no restriction — matches
    every other allow-list in ConfigurationSecurityModel."""
    service = _minimal_workspace_service()

    result = service.validate_allowed_key_value_store(_security(), "anything-at-all")
    assert result.ok


def test_validate_allowed_key_value_store_skips_when_no_backend_configured():
    """backend_type=None (neither output_store nor output_store_integration
    set) means the feature is disabled for this workspace — nothing to
    check, even under a restrictive allow-list."""
    service = _minimal_workspace_service()

    result = service.validate_allowed_key_value_store(_security(allowed_key_value_stores=["azure-appconfig"]), None)
    assert result.ok
    assert result.messages() == []


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
        execution=[
            {"name": "apply", "provisioner": "terraform-main", "resources": ["vm"], "dns_zones": ["public-dns"]}
        ],
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
        execution=[{"name": "deploy", "provisioner": "terraform-main", "namespaces": ["apps"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"apps"}


def test_claimed_document_names_excludes_an_untargeted_document():
    data = _workspace_with_execution(
        dns_zones=["public-dns"],
        resources=[{"name": "vm", "resource": "vm-class", "role": "node"}],
        execution=[{"name": "apply", "provisioner": "terraform-main", "resources": ["vm"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    # "public-dns" is never targeted by any step, so it's excluded — and
    # "vm" (a resource) is never claimable at all regardless of targeting.
    assert service.claimed_document_names() == set()


# ---------------------------------------------------------------------------
# Resource/topology-derived claiming (docs/design/workspace-execution-model.md)
# ---------------------------------------------------------------------------


def test_claimed_document_names_includes_a_resources_firewall_without_direct_listing():
    """A resource's own firewalls/dns_zones/subnet are claimed via the
    resource reference alone — no need to also list the firewall/dns
    zone/network directly on the step."""
    data = _workspace_with_execution(
        firewalls=["web-fw"],
        dns_zones=["public-dns"],
        networks=["vnet-main"],
        resources=[
            {
                "name": "vm",
                "resource": "vm-class",
                "role": "node",
                "firewalls": ["web-fw"],
                "dns_zones": ["public-dns"],
                "subnet": {"network": "vnet-main", "subnet": "app"},
            }
        ],
        execution=[{"name": "apply", "provisioner": "terraform-main", "resources": ["vm"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"web-fw", "public-dns", "vnet-main"}


def test_claimed_document_names_includes_a_directly_listed_dns_zone_with_no_resource():
    """The escape hatch: a dns zone with no resource reference at all,
    listed directly via step.dns_zones, is still claimed."""
    data = _workspace_with_execution(
        dns_zones=["public-dns"],
        execution=[{"name": "apply", "provisioner": "terraform-main", "dns_zones": ["public-dns"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"public-dns"}


def test_claimed_document_names_includes_a_firewall_via_topology_expansion():
    """A topology's component resource's firewall is claimed via
    step.topologies alone — no direct step.resources entry needed."""
    data = _workspace_with_execution(
        firewalls=["web-fw"],
        resources=[{"name": "vm", "resource": "vm-class", "role": "node", "firewalls": ["web-fw"]}],
        topology=[{"name": "web-tier", "type": "kubernetes", "components": [{"resource": "vm"}]}],
        execution=[{"name": "apply", "provisioner": "terraform-main", "topologies": ["web-tier"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"web-fw"}


def test_claimed_document_names_includes_a_namespace_reached_only_via_topology():
    """Namespace claiming extends symmetrically (point 8's "Decided
    2026-10-10"): a namespace reached only through step.topologies — not
    listed directly under step.namespaces — is claimed too."""
    data = _workspace_with_execution(
        namespaces=["apps"],
        resources=[{"name": "vm", "resource": "vm-class", "role": "node"}],
        topology=[
            {
                "name": "web-tier",
                "type": "kubernetes",
                "components": [{"resource": "vm"}],
                "namespaces": [{"namespace": "apps"}],
            }
        ],
        execution=[{"name": "apply", "provisioner": "terraform-main", "topologies": ["web-tier"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == {"apps"}


def test_claimed_document_names_excludes_a_disabled_resources_firewall():
    """A resource with enabled=False behaves as if absent (decided
    2026-10-10) — its own firewalls/dns_zones are not claimed even though
    it's named in step.resources."""
    data = _workspace_with_execution(
        firewalls=["web-fw"],
        resources=[
            {
                "name": "vm",
                "resource": "vm-class",
                "role": "node",
                "enabled": False,
                "firewalls": ["web-fw"],
            }
        ],
        execution=[{"name": "apply", "provisioner": "terraform-main", "resources": ["vm"]}],
    )
    service = WorkspaceService(data=data)
    assert service.validate().ok
    assert service.claimed_document_names() == set()
