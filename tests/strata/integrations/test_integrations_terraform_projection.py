#!/usr/bin/env python3
"""Tests for `terraform_projection` (ADR-0023 Phase 1 + 2a) — the default
Terraform tfvars projection.

Fixture shaped like haven's real `stack/workspace.yaml`: one provider, one
topology, one resource, one namespace, one firewall.
"""

from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueReference
from strata.integrations.terraform_projection import (
    build_platform_projection,
    planned_files,
)
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.dns_model import DnsMetaModel, DnsModel, DnsRecordModel, DnsSpecModel, DnsZoneModel
from strata.models.firewall_model import (
    FirewallDefaultsModel,
    FirewallMetaModel,
    FirewallModel,
    FirewallRuleModel,
    FirewallSpecModel,
)
from strata.models.namespace_model import NamespaceMetaModel, NamespaceModel, NamespaceSpecModel
from strata.models.network_model import (
    NetworkDefinitionModel,
    NetworkMetaModel,
    NetworkModel,
    NetworkSpecModel,
    SubnetModel,
)
from strata.models.provider_model import (
    ProviderMetaModel,
    ProviderModel,
    ProviderPropertiesModel,
    ProviderSpecModel,
)
from strata.models.provisioning_model import ProvisionerModel
from strata.models.resource_model import (
    ResourceMetaModel,
    ResourceModel,
    ResourcePropertiesModel,
    ResourceSpecModel,
)
from strata.models.topology_model import (
    TopologyComponentModel,
    TopologyMetaModel,
    TopologyModel,
    TopologySpecModel,
)
from strata.models.workspace_model import (
    WorkspaceMetaModel,
    WorkspaceModel,
    WorkspaceResourceModel,
    WorkspaceSpecModel,
)


def _provider(name: str = "hetzner_dc_eu_de") -> ProviderModel:
    return ProviderModel(
        meta=ProviderMetaModel(name=name),
        spec=ProviderSpecModel(
            properties=ProviderPropertiesModel(type="hetzner", region="nbg1", display_name="Nuremberg")
        ),
    )


def _topology(name: str = "hetzner_hearth", resource: str = "haven_vm_hetzner_hearth") -> TopologyModel:
    return TopologyModel(
        meta=TopologyMetaModel(name=name),
        spec=TopologySpecModel(type="single_node", components=[TopologyComponentModel(resource=resource)]),
    )


def _resource(name: str = "haven_vm_hetzner_hearth", category: str = "compute") -> ResourceModel:
    return ResourceModel(
        meta=ResourceMetaModel(name=name),
        spec=ResourceSpecModel(
            properties=ResourcePropertiesModel(
                provider_type="hetzner", resource_type="virtualmachine", unit_cost=4.15, category=category
            ),
            configuration={"image": "ubuntu-24.04", "server_type": "cx23"},
            default_tags={"managed-by": "strata"},
        ),
    )


def _namespace(name: str = "hearth") -> NamespaceModel:
    return NamespaceModel(
        meta=NamespaceMetaModel(name=name, annotations={"description": "Hearth namespace"}, tags=["hearth"]),
        spec=NamespaceSpecModel(
            modules=[ModuleReferenceModel(name="vaultwarden_ref", module="vaultwarden")],
            default_labels={"environment": "production"},
        ),
    )


def _firewall(name: str = "haven_fw_hetzner_hearth") -> FirewallModel:
    return FirewallModel(
        meta=FirewallMetaModel(name=name, annotations={"description": "Hearth firewall"}),
        spec=FirewallSpecModel(
            defaults=[FirewallDefaultsModel(direction="in", permission="deny")],
            allow=[FirewallRuleModel(direction="in", proto="tcp", port=443, **{"from": "0.0.0.0/0"})],
            default_tags={"managed-by": "strata"},
        ),
    )


def _dns(name: str = "huybrechts_xyz") -> DnsModel:
    return DnsModel(
        meta=DnsMetaModel(name=name, annotations={"description": "Primary zone"}),
        spec=DnsSpecModel(
            provider="cloudflare",
            zones=[
                DnsZoneModel(
                    name="huybrechts.xyz",
                    default_tags={"managed-by": "strata"},
                    records=[DnsRecordModel(name="@", type="A", value="1.2.3.4")],
                )
            ],
        ),
    )


def _network(name: str = "product_estate") -> NetworkModel:
    return NetworkModel(
        meta=NetworkMetaModel(name=name, annotations={"description": "Product estate"}),
        spec=NetworkSpecModel(
            networks=[
                NetworkDefinitionModel(
                    name="vnet_main",
                    address_space=["10.0.0.0/16"],
                    subnets=[SubnetModel(name="aks", cidr="10.0.1.0/24")],
                    default_tags={"managed-by": "strata"},
                )
            ]
        ),
    )


def _workspace(
    *,
    provider_name: str = "hetzner_dc_eu_de",
    topology_name: str | None = "hetzner_hearth",
    resources: list[WorkspaceResourceModel] | None = None,
    namespace_names: list[str] | None = None,
    firewall_names: list[str] | None = None,
    dns_names: list[str] | None = None,
    network_names: list[str] | None = None,
) -> WorkspaceModel:
    if resources is None:
        resources = [WorkspaceResourceModel(name="haven_vm_hetzner_hearth", resource="haven_vm_hetzner_hearth")]
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="haven_platform", tags=["haven", "hetzner"]),
        spec=WorkspaceSpecModel(
            providers=[provider_name],
            provisioners=[
                ProvisionerModel(
                    name="haven_iac",
                    tool="terraform",
                    source=SourceModel(source_path="deploy/terraform"),
                )
            ],
            topology=[topology_name] if topology_name else None,
            resources=resources,
            namespaces=namespace_names,
            firewalls=firewall_names,
            dns_zones=dns_names,
            networks=network_names,
        ),
    )


def _graph(
    *,
    namespace_names=None,
    firewall_names=None,
    dns_names=None,
    network_names=None,
    variable_refs=None,
    feature_refs=None,
    properties=None,
    custom=None,
    **workspace_kwargs,
) -> ResolvedWorkspaceGraph:
    workspace = _workspace(
        namespace_names=namespace_names,
        firewall_names=firewall_names,
        dns_names=dns_names,
        network_names=network_names,
        **workspace_kwargs,
    )
    return ResolvedWorkspaceGraph(
        workspace=workspace,
        providers={"hetzner_dc_eu_de": _provider()},
        topologies={"hetzner_hearth": _topology()},
        resources={"haven_vm_hetzner_hearth": _resource()},
        namespaces={name: _namespace(name) for name in (namespace_names or [])},
        firewalls={name: _firewall(name) for name in (firewall_names or [])},
        dns={name: _dns(name) for name in (dns_names or [])},
        networks={name: _network(name) for name in (network_names or [])},
        variable_refs=variable_refs or [],
        feature_refs=feature_refs or [],
        properties=properties or {},
        custom=custom or {},
    )


def _provisioner() -> ProvisionerModel:
    return ProvisionerModel(name="haven_iac", tool="terraform", source=SourceModel(source_path="deploy/terraform"))


def test_workspace_category_present():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["workspace"] == {
        "name": "haven_platform",
        "labels": {},
        "tags": ["haven", "hetzner"],
        "annotations": {},
    }


def test_providers_category_present():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["providers"] == {
        "hetzner_dc_eu_de": {"type": "hetzner", "region": "nbg1", "display_name": "Nuremberg"}
    }


def test_topologies_category_present():
    payload = build_platform_projection(_graph(), _provisioner())
    topology = payload["topologies"]["hetzner_hearth"]
    assert topology["type"] == "single_node"
    assert topology["components"] == [{"resource": "haven_vm_hetzner_hearth"}]
    assert topology["volumes"] == []


def test_topologies_category_empty_when_workspace_has_no_topology():
    payload = build_platform_projection(_graph(topology_name=None), _provisioner())
    assert payload["topologies"] == {}


def test_namespaces_category_present():
    payload = build_platform_projection(_graph(namespace_names=["hearth"]), _provisioner())
    namespace = payload["namespaces"]["hearth"]
    assert namespace["description"] == "Hearth namespace"
    assert namespace["tags"] == ["hearth"]
    assert namespace["modules"] == ["vaultwarden"]


def test_namespaces_category_empty_when_workspace_has_no_namespaces():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["namespaces"] == {}


def test_firewalls_category_present():
    payload = build_platform_projection(_graph(firewall_names=["haven_fw_hetzner_hearth"]), _provisioner())
    firewall = payload["firewalls"]["haven_fw_hetzner_hearth"]
    assert firewall["description"] == "Hearth firewall"
    assert firewall["rules"]["reset"] is False
    assert firewall["rules"]["defaults"] == [{"direction": "in", "permission": "deny"}]
    assert firewall["rules"]["deny"] == []
    allow_rule = firewall["rules"]["allow"][0]
    assert allow_rule["direction"] == "in"
    assert allow_rule["port"] == 443
    assert allow_rule["from"] == "0.0.0.0/0"  # by_alias=True - not "from_"


def test_firewalls_category_empty_when_workspace_has_no_firewalls():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["firewalls"] == {}


def test_dns_category_present():
    payload = build_platform_projection(_graph(dns_names=["huybrechts_xyz"]), _provisioner())
    dns = payload["dns"]["huybrechts_xyz"]
    assert dns["description"] == "Primary zone"
    assert dns["provider"] == "cloudflare"
    zone = dns["zones"]["huybrechts.xyz"]
    assert zone["ttl"] == 3600
    assert zone["records"] == [{"name": "@", "type": "A", "value": "1.2.3.4", "ttl": None, "priority": None}]


def test_dns_category_empty_when_workspace_has_no_dns_zones():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["dns"] == {}


def test_networks_category_present():
    payload = build_platform_projection(_graph(network_names=["product_estate"]), _provisioner())
    attachment = payload["networks"]["product_estate"]
    assert attachment["description"] == "Product estate"
    network = attachment["networks"]["vnet_main"]
    assert network["address_space"] == ["10.0.0.0/16"]
    assert network["subnets"] == {"aks": {"cidr": "10.0.1.0/24", "description": None}}
    assert network["peerings"] == {}


def test_networks_category_empty_when_workspace_has_no_networks():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["networks"] == {}


def test_resources_by_category_merges_workspace_override_on_top():
    workspace_resource = WorkspaceResourceModel(
        name="haven_vm_hetzner_hearth",
        resource="haven_vm_hetzner_hearth",
        configuration={"server_type": "cx33"},  # overrides resource's own cx23
        role="node",
        count=1,
    )
    payload = build_platform_projection(_graph(resources=[workspace_resource]), _provisioner())
    # grouped by resource_type (lowercased), not category - matches v1's real
    # _build_resources_by_category(), wrapped under "resources" per type.
    entry = payload["resources_by_category"]["virtualmachine"]["resources"]["haven_vm_hetzner_hearth"]
    assert entry["provider_type"] == "hetzner"
    assert entry["resource_type"] == "virtualmachine"
    assert entry["unit_cost"] == 4.15
    assert entry["role"] == "node"
    assert entry["count"] == 1
    # workspace wins: server_type overridden, image kept from the resource document
    assert entry["configuration"] == {"image": "ubuntu-24.04", "server_type": "cx33"}
    assert entry["default_tags"] == {"managed-by": "strata"}


def test_resources_by_category_excludes_disabled_resource():
    workspace_resource = WorkspaceResourceModel(
        name="haven_vm_hetzner_hearth", resource="haven_vm_hetzner_hearth", enabled=False
    )
    payload = build_platform_projection(_graph(resources=[workspace_resource]), _provisioner())
    assert payload["resources_by_category"] == {}


def test_resources_by_category_excludes_managed_by_provisioner():
    workspace_resource = WorkspaceResourceModel(name="opaque_resx", managed_by="provisioner")
    payload = build_platform_projection(_graph(resources=[workspace_resource]), _provisioner())
    assert payload["resources_by_category"] == {}


def test_planned_files_skips_empty_categories():
    payload = build_platform_projection(_graph(topology_name=None, resources=[]), _provisioner())
    files = dict(planned_files(payload))
    assert "topologies.auto.tfvars.json" not in files
    assert "resx_virtualmachine.auto.tfvars.json" not in files


# ---------------------------------------------------------------------------
# flags / variables / properties / custom (docs/design/build-time-value-categories.md)
# ---------------------------------------------------------------------------


def test_flags_category_only_includes_resolved_entries():
    refs = [
        ValueReference(key="NEW_UI", store="constant", value=True),
        ValueReference(key="BETA", store="vault", value=None),  # integration-backed - excluded
    ]
    payload = build_platform_projection(_graph(feature_refs=refs), _provisioner())
    assert payload["flags"] == {"NEW_UI": True}


def test_variables_category_only_includes_resolved_entries():
    refs = [
        ValueReference(key="REGION", store="constant", value="westeurope"),
        ValueReference(key="DB_HOST", store="vault", value=None),
    ]
    payload = build_platform_projection(_graph(variable_refs=refs), _provisioner())
    assert payload["variables"] == {"REGION": "westeurope"}


def test_properties_and_custom_categories_pass_through_graph_dicts_directly():
    payload = build_platform_projection(
        _graph(properties={"tier": "premium"}, custom={"team": "platform"}), _provisioner()
    )
    assert payload["properties"] == {"tier": "premium"}
    assert payload["custom"] == {"team": "platform"}


def test_flags_writes_to_flags_auto_tfvars_json_not_features():
    """Matches v1's real filename (`_build_feature_flags_vars()`) - the
    payload key is `flags`, not `features`/`feature_refs`."""
    payload = build_platform_projection(
        _graph(feature_refs=[ValueReference(key="NEW_UI", store="constant", value=True)]), _provisioner()
    )
    files = dict(planned_files(payload))
    assert "flags.auto.tfvars.json" in files
    assert "features.auto.tfvars.json" not in files


def test_flags_and_variables_categories_empty_when_nothing_resolved():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["flags"] == {}
    assert payload["variables"] == {}
    files = dict(planned_files(payload))
    assert "flags.auto.tfvars.json" not in files
    assert "variables.auto.tfvars.json" not in files
    assert "properties.auto.tfvars.json" not in files
    assert "custom.auto.tfvars.json" not in files
    assert "namespaces.auto.tfvars.json" not in files
    assert "firewalls.auto.tfvars.json" not in files
    assert "dns.auto.tfvars.json" not in files
    assert "networks.auto.tfvars.json" not in files
    assert "workspace.auto.tfvars.json" in files
    assert "providers.auto.tfvars.json" in files


def test_planned_files_naming_matches_auto_tfvars_convention():
    payload = build_platform_projection(
        _graph(
            namespace_names=["hearth"],
            firewall_names=["haven_fw_hetzner_hearth"],
            dns_names=["huybrechts_xyz"],
            network_names=["product_estate"],
        ),
        _provisioner(),
    )
    filenames = {filename for filename, _ in planned_files(payload)}
    # resources_by_category unrolls into one resx_<type> file per resource
    # type, matching v1's real one-file-per-type split - never a single
    # combined resources_by_category.auto.tfvars.json file.
    assert filenames == {
        "workspace.auto.tfvars.json",
        "providers.auto.tfvars.json",
        "topologies.auto.tfvars.json",
        "resx_virtualmachine.auto.tfvars.json",
        "namespaces.auto.tfvars.json",
        "firewalls.auto.tfvars.json",
        "dns.auto.tfvars.json",
        "networks.auto.tfvars.json",
    }


def test_planned_files_resx_content_is_wrapped_under_resources_key():
    payload = build_platform_projection(_graph(), _provisioner())
    files = dict(planned_files(payload))
    resx = files["resx_virtualmachine.auto.tfvars.json"]
    assert set(resx) == {"resources"}
    assert "haven_vm_hetzner_hearth" in resx["resources"]
