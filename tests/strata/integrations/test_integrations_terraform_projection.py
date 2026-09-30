#!/usr/bin/env python3
"""Tests for `terraform_projection` (ADR-0023 Phase 1 + 2a) — the default
Terraform tfvars projection.

Fixture shaped like haven's real `stack/workspace.yaml`: one provider, one
topology, one resource, one namespace, one firewall.
"""

from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueReference
from strata.integrations.terraform_projection import (
    build_configuration_payloads,
    build_platform_projection,
    planned_files,
)
from strata.models.common_models import ModuleReferenceModel, SourceModel
from strata.models.deployment_model import DeploymentMetaModel, DeploymentModel, DeploymentSpecModel
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
from strata.models.tenant_model import TenantMetaModel, TenantModel, TenantSpecModel
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
    tenant=None,
    deployment=None,
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
        tenant=tenant,
        deployment=deployment,
    )


def _provisioner() -> ProvisionerModel:
    return ProvisionerModel(name="haven_iac", tool="terraform", source=SourceModel(source_path="deploy/terraform"))


def test_workspace_category_present():
    """Matches v1's real `_build_workspace_vars()` shape exactly
    (docs/design/terraform-tfvars-parity.md) — flat, six independent
    variables, with `graph.deployment` unset falling back to v1's own
    documented defaults (`environment="production"`,
    `workspace_version="1.0.0"`, `deployment_name`=the workspace's own
    name)."""
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["workspace"] == {
        "workspace_name": "haven_platform",
        "workspace_version": "1.0.0",
        "deployment_name": "haven_platform",
        "environment": "production",
        "platform_version": "",
        "labels": {},
        "metadata": {
            "deployment_version": "1.0.0",
            "workspace_description": "",
            "deployment_description": "",
            "workspace_tags": ["haven", "hetzner"],
            "deployment_tags": [],
        },
    }


def test_workspace_category_uses_deployment_labels_when_present():
    """`environment`/`metadata.deployment_version`/`deployment_name`/
    `platform_version` all come from the resolved Deployment document, not
    the workspace — confirmed against v1's real `_build_workspace_vars()`."""
    deployment = DeploymentModel(
        meta=DeploymentMetaModel(
            name="deploy-hub-z00-spoke-s01",
            labels={"version": "2.3.0", "environment": "production"},
            annotations={"description": "Spoke deployment"},
            tags=["spoke"],
        ),
        spec=DeploymentSpecModel(workspace="haven_platform", environments=["spoke-env"]),
    )
    graph = _graph(deployment=deployment)
    payload = build_platform_projection(graph, _provisioner())
    assert payload["workspace"]["deployment_name"] == "deploy-hub-z00-spoke-s01"
    assert payload["workspace"]["environment"] == "production"
    assert payload["workspace"]["platform_version"] == "strata.huybrechts.xyz/v2"
    assert payload["workspace"]["metadata"]["deployment_version"] == "2.3.0"
    assert payload["workspace"]["metadata"]["deployment_description"] == "Spoke deployment"
    assert payload["workspace"]["metadata"]["deployment_tags"] == ["spoke"]


def test_providers_category_present():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["providers"] == {
        "hetzner_dc_eu_de": {
            "type": "hetzner",
            "region": "nbg1",
            "display_name": "Nuremberg",
            "description": "",
            "labels": {},
            "tags": [],
            "configuration": {},
            "custom": {},
        }
    }


def test_providers_category_includes_description_labels_and_tags():
    """docs/design/terraform-tfvars-parity.md — v1's real
    `_build_provider_vars()` emits these three from the provider's own
    `description`/`labels`/`tags`; v2 previously dropped them entirely."""
    graph = _graph()
    graph.providers["hetzner_dc_eu_de"] = ProviderModel(
        meta=ProviderMetaModel(
            name="hetzner_dc_eu_de",
            annotations={"description": "Nuremberg datacenter"},
            labels={"version": "1.0.0"},
            tags=["hetzner", "nbg1"],
        ),
        spec=ProviderSpecModel(
            properties=ProviderPropertiesModel(type="hetzner", region="nbg1", display_name="Nuremberg")
        ),
    )
    payload = build_platform_projection(graph, _provisioner())
    provider = payload["providers"]["hetzner_dc_eu_de"]
    assert provider["description"] == "Nuremberg datacenter"
    assert provider["labels"] == {"version": "1.0.0"}
    assert provider["tags"] == ["hetzner", "nbg1"]


def test_providers_category_includes_configuration_and_custom_when_set():
    """docs/_gap_v1.md gap #17: `ProviderSpecModel.configuration`/`.custom`
    used to be silently dropped entirely — now projected like every other
    category's own passthrough fields."""
    graph = _graph()
    graph.providers["hetzner_dc_eu_de"] = ProviderModel(
        meta=ProviderMetaModel(name="hetzner_dc_eu_de"),
        spec=ProviderSpecModel(
            properties=ProviderPropertiesModel(type="hetzner", region="nbg1", display_name="Nuremberg"),
            configuration={"skip_provider_registration": True},
            custom={"cost_center": "platform"},
        ),
    )
    payload = build_platform_projection(graph, _provisioner())
    assert payload["providers"]["hetzner_dc_eu_de"]["configuration"] == {"skip_provider_registration": True}
    assert payload["providers"]["hetzner_dc_eu_de"]["custom"] == {"cost_center": "platform"}


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


def test_planned_files_wraps_categories_under_their_real_v1_variable_name():
    """docs/design/terraform-tfvars-parity.md — confirmed directly against
    v1's real `_planned_files()`: the file's content is nested one level
    under the real Terraform variable name, which is not always the same
    word as the category/filename (`providers` -> `platform_providers`,
    `dns` -> `dns_zones`, `tenant` -> `strata_tenant`) but is for the rest
    (`topologies`/`namespaces`/`firewalls`/`networks` wrap under their own
    name unchanged).
    """
    payload = build_platform_projection(
        _graph(
            namespace_names=["hearth"],
            firewall_names=["haven_fw_hetzner_hearth"],
            dns_names=["huybrechts_xyz"],
            network_names=["product_estate"],
            tenant=_tenant(),
        ),
        _provisioner(),
    )
    files = dict(planned_files(payload))
    assert set(files["providers.auto.tfvars.json"]) == {"platform_providers"}
    assert set(files["topologies.auto.tfvars.json"]) == {"topologies"}
    assert set(files["namespaces.auto.tfvars.json"]) == {"namespaces"}
    assert set(files["firewalls.auto.tfvars.json"]) == {"firewalls"}
    assert set(files["dns.auto.tfvars.json"]) == {"dns_zones"}
    assert set(files["networks.auto.tfvars.json"]) == {"networks"}
    assert set(files["tenant.auto.tfvars.json"]) == {"strata_tenant"}
    assert files["providers.auto.tfvars.json"]["platform_providers"] == payload["providers"]
    assert files["tenant.auto.tfvars.json"]["strata_tenant"] == payload["tenant"]
    # workspace/flags/variables/properties/custom are v1's flat,
    # multi-variable categories — never wrapped.
    assert files["workspace.auto.tfvars.json"] == payload["workspace"]


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


# ---------------------------------------------------------------------------
# tenant (docs/design/build-command.md's `tenant` Terraform category)
# ---------------------------------------------------------------------------


def _tenant(name: str = "acme") -> TenantModel:
    return TenantModel(
        meta=TenantMetaModel(name=name),
        spec=TenantSpecModel(
            display_name="Acme",
            geographies=["europe"],
            configuration={"tier": "sandbox"},
        ),
    )


def test_tenant_category_present_when_deployment_references_one():
    payload = build_platform_projection(_graph(tenant=_tenant()), _provisioner())
    assert payload["tenant"] == {
        "code": "acme",
        "name": "Acme",
        "zones": ["europe"],
        "onboarded": None,
        "configuration": {"tier": "sandbox"},
    }


def test_tenant_category_empty_when_no_tenant_referenced():
    payload = build_platform_projection(_graph(), _provisioner())
    assert payload["tenant"] == {}
    files = dict(planned_files(payload))
    assert "tenant.auto.tfvars.json" not in files


def test_tenant_writes_to_tenant_auto_tfvars_json():
    payload = build_platform_projection(_graph(tenant=_tenant()), _provisioner())
    files = dict(planned_files(payload))
    assert files["tenant.auto.tfvars.json"] == {"strata_tenant": payload["tenant"]}


# ---------------------------------------------------------------------------
# build_configuration_payloads() — docs/_gap_v1.md gap #8's Terraform-side
# refinement + gap #17's universal-resolution-reach extension,
# docs/design/value-token-resolution.md's "Full Solution" Phase 6 and
# "Decision (2026-09-29)". Broadcast-only (no per-name ownership, unlike
# build_dns_networks_firewalls_payloads()) delivery of workspace/providers/
# resx_<type>/topologies/namespaces/flags/variables/properties/custom/tenant.
# `workspace`/`providers` are unconditionally present in every `_graph()`
# fixture below (a workspace always has a name; the fixture always seeds one
# provider) — confirmed real, not an artifact of a specific test's setup.
# ---------------------------------------------------------------------------


def test_configuration_payloads_includes_resources_and_topologies_by_default():
    """The default `_graph()` fixture always has one resource, one
    topology, one workspace, and one provider - all four present, keyed to
    match `planned_files()`'s own per-category naming exactly."""
    payloads = build_configuration_payloads(_graph())
    assert set(payloads) == {"resx_virtualmachine", "topologies", "workspace", "providers"}


def test_configuration_payloads_resx_key_matches_the_resource_type():
    payloads = build_configuration_payloads(_graph())
    assert payloads["resx_virtualmachine"] == {
        "resources": {
            "haven_vm_hetzner_hearth": {
                "provider_type": "hetzner",
                "resource_type": "virtualmachine",
                "subcategory": None,
                "unit_cost": 4.15,
                "role": None,
                "count": 1,
                "configuration": {"image": "ubuntu-24.04", "server_type": "cx23"},
                "default_tags": {"managed-by": "strata"},
                "custom_tags": {},
                "firewalls": [],
                "subnet": None,
            }
        }
    }


def test_configuration_payloads_matches_build_platform_projection_exactly():
    """Not a second, divergent implementation - same underlying
    `_build_resources_payload()`/`_build_topologies_payload()` calls
    `build_platform_projection()` itself uses."""
    graph = _graph(tenant=_tenant(), properties={"region": "eu"}, custom={"team": "platform"})
    platform_payload = build_platform_projection(graph, _provisioner())
    configuration_payloads = build_configuration_payloads(graph)

    assert configuration_payloads["resx_virtualmachine"] == platform_payload["resources_by_category"]["virtualmachine"]
    assert configuration_payloads["topologies"] == platform_payload["topologies"]
    assert configuration_payloads["properties"] == platform_payload["properties"]
    assert configuration_payloads["custom"] == platform_payload["custom"]
    assert configuration_payloads["tenant"] == platform_payload["tenant"]
    assert configuration_payloads["workspace"] == platform_payload["workspace"]
    assert configuration_payloads["providers"] == platform_payload["providers"]


def test_configuration_payloads_excludes_resource_and_topology_categories_when_absent():
    """No resources, no topology - only `workspace`/`providers` remain
    (unconditionally present, see module-level comment above), matching
    `planned_files()`'s own "skip empty categories" convention for
    everything else."""
    payloads = build_configuration_payloads(_graph(resources=[], topology_name=None))
    assert set(payloads) == {"workspace", "providers"}


def test_configuration_payloads_includes_properties_and_custom_when_set():
    payloads = build_configuration_payloads(
        _graph(resources=[], topology_name=None, properties={"region": "eu"}, custom={"team": "platform"})
    )
    assert payloads["properties"] == {"region": "eu"}
    assert payloads["custom"] == {"team": "platform"}
    assert set(payloads) == {"workspace", "providers", "properties", "custom"}


def test_configuration_payloads_includes_tenant_when_set():
    payloads = build_configuration_payloads(_graph(resources=[], topology_name=None, tenant=_tenant()))
    assert payloads["tenant"] == {
        "code": "acme",
        "name": "Acme",
        "zones": ["europe"],
        "onboarded": None,
        "configuration": {"tier": "sandbox"},
    }


def test_configuration_payloads_excludes_disabled_resource():
    disabled = WorkspaceResourceModel(name="haven_vm_hetzner_hearth", resource="haven_vm_hetzner_hearth", enabled=False)
    payloads = build_configuration_payloads(_graph(resources=[disabled], topology_name=None))
    assert "resx_virtualmachine" not in payloads
