#!/usr/bin/env python3
"""Tests for `terraform_projection` (ADR-0035) — the unified Terraform
tfvars projection.

Fixture shaped like haven's real `stack/workspace.yaml`: one provider, one
topology, one resource, one namespace, one firewall.
"""

import json

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.terraform_projection import (
    build_strata_locals,
    build_strata_payload,
    find_secret_paths,
    rewrite_strata_payload,
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
from strata.models.module_model import ModuleMetaModel, ModuleModel, ModulePropertiesModel, ModuleSpecModel
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
    ResourceDiskModel,
    ResourceMetaModel,
    ResourceModel,
    ResourcePropertiesModel,
    ResourceSpecModel,
    ResourceStorageModel,
)
from strata.models.tenant_model import TenantMetaModel, TenantModel, TenantSpecModel
from strata.models.workspace_model import (
    TopologyComponentModel,
    TopologyNamespaceReferenceModel,
    TopologySpecModel,
    WorkspaceMetaModel,
    WorkspaceModel,
    WorkspaceResourceModel,
    WorkspaceSpecModel,
)
from strata.utils.value_tokens import escape_path_segment


def _provider(name: str = "hetzner_dc_eu_de") -> ProviderModel:
    return ProviderModel(
        meta=ProviderMetaModel(name=name),
        spec=ProviderSpecModel(
            properties=ProviderPropertiesModel(type="hetzner", region="nbg1", display_name="Nuremberg")
        ),
    )


def _topology(name: str = "hetzner_hearth", resource: str = "haven_vm_hetzner_hearth") -> TopologySpecModel:
    return TopologySpecModel(name=name, type="single_node", components=[TopologyComponentModel(resource=resource)])


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
                    configuration={"foo": "bar"},
                    custom={"team": "platform"},
                    default_tags={"managed-by": "strata"},
                    custom_tags={"cost-center": "eng"},
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
    topology = None
    if topology_name:
        # Inline now (ADR-0028) — reference whichever resource is actually in
        # scope, so a test overriding `resources` with a differently-named
        # entry doesn't trip the new Phase 1 component-existence validator.
        component_resource = resources[0].name if resources else "haven_vm_hetzner_hearth"
        topology = [_topology(name=topology_name, resource=component_resource)]
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
            topology=topology,
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


def _tenant(name: str = "acme") -> TenantModel:
    return TenantModel(
        meta=TenantMetaModel(name=name),
        spec=TenantSpecModel(
            display_name="Acme",
            geographies=["europe"],
            configuration={"tier": "sandbox"},
        ),
    )


# ---------------------------------------------------------------------------
# build_strata_payload() (ADR-0035, docs/design/unified-terraform-projection.md
# Phase 2) — the unified projection, one nested object per `ResolvedWorkspaceGraph`.
# ---------------------------------------------------------------------------


def _module(name: str = "authentik") -> ModuleModel:
    return ModuleModel(
        meta=ModuleMetaModel(name=name),
        spec=ModuleSpecModel(
            source=SourceModel(source_path=f"modules/{name}"),
            type="helm",
            release_name=name,
            kubernetes_namespace="hearth",
            properties=ModulePropertiesModel(),
        ),
    )


def test_build_strata_payload_workspace_shape_renames_v1_parity_fields():
    """`strata.workspace` reshapes `_build_workspace_payload()`'s flat
    v1-parity names (`workspace_name`/`metadata.*`) into plain nested
    fields, and drops `default_tags` (hoisted to its own top-level
    category — see the next test)."""
    payload = build_strata_payload(_graph())
    assert payload["workspace"] == {
        "name": "haven_platform",
        "version": "1.0.0",
        "description": "",
        "tags": ["haven", "hetzner"],
        "deployment": "haven_platform",
        "deployment_version": "1.0.0",
        "deployment_description": "",
        "deployment_tags": [],
        "environment": "production",
        "platform_version": "",
        "labels": {},
        "configuration": {},
        "custom": {},
    }


def test_build_strata_payload_default_tags_hoisted_to_top_level():
    workspace = _workspace(resources=[], topology_name=None)
    workspace = workspace.model_copy(
        update={"spec": workspace.spec.model_copy(update={"default_tags": {"managed-by": "strata"}})}
    )
    graph = ResolvedWorkspaceGraph(workspace=workspace, providers={"hetzner_dc_eu_de": _provider()})
    payload = build_strata_payload(graph)
    assert payload["default_tags"] == {"managed-by": "strata"}
    assert "default_tags" not in payload["workspace"]


def test_build_strata_payload_tenant_includes_geographies_and_custom():
    """`geographies` is the real `TenantSpecModel` field name (this
    redesign abandons v1's `zones` rename); `custom` is a real field the
    old `_build_tenant_payload()` never read at all."""
    tenant = _tenant()
    tenant = tenant.model_copy(update={"spec": tenant.spec.model_copy(update={"custom": {"cost_center": "1234"}})})
    payload = build_strata_payload(_graph(tenant=tenant))
    assert payload["tenant"] == {
        "code": "acme",
        "name": "Acme",
        "geographies": ["europe"],
        "onboarded": None,
        "configuration": {"tier": "sandbox"},
        "custom": {"cost_center": "1234"},
    }


def test_build_strata_payload_resources_is_flat_map_not_nested_by_type():
    """`strata.resources` is keyed directly by resource name
    (`haven_vm_hetzner_hearth`), not wrapped under a resource-type/
    `resources` double layer the way `resx_<type>` categories are —
    see "Resources are one flat map"."""
    payload = build_strata_payload(_graph())
    assert set(payload["resources"]) == {"haven_vm_hetzner_hearth"}
    assert payload["resources"]["haven_vm_hetzner_hearth"]["resource_type"] == "virtualmachine"


def test_build_strata_payload_resources_includes_storage_and_custom():
    """Two gap fixes folded in: `storage` and `custom` were validated
    `ResourceSpecModel` fields the old `_merge_resource_entry()` never
    read at all."""
    resource = ResourceModel(
        meta=ResourceMetaModel(name="haven_vm_hetzner_hearth"),
        spec=ResourceSpecModel(
            properties=ResourcePropertiesModel(provider_type="hetzner", resource_type="virtualmachine"),
            storage=ResourceStorageModel(
                install_path="/opt/app", disks=[ResourceDiskModel(name="os", size=64, label="os-disk", mount="/data")]
            ),
            custom={"team": "platform"},
        ),
    )
    workspace_resource = WorkspaceResourceModel(name="haven_vm_hetzner_hearth", resource="haven_vm_hetzner_hearth")
    workspace = _workspace(resources=[workspace_resource], topology_name=None)
    graph = ResolvedWorkspaceGraph(
        workspace=workspace,
        providers={"hetzner_dc_eu_de": _provider()},
        resources={"haven_vm_hetzner_hearth": resource},
    )
    payload = build_strata_payload(graph)
    entry = payload["resources"]["haven_vm_hetzner_hearth"]
    assert entry["custom"] == {"team": "platform"}
    assert entry["storage"] == {
        "install_path": "/opt/app",
        "disks": [{"name": "os", "size": 64, "label": "os-disk", "mount": "/data"}],
    }


def test_build_strata_payload_topologies_includes_namespaces():
    """`TopologySpecModel.namespaces` was validated but never read by the
    old `_build_topologies_payload()` at all."""
    workspace = _workspace(namespace_names=["hearth"])
    topology = workspace.spec.topology[0].model_copy(
        update={"namespaces": [TopologyNamespaceReferenceModel(namespace="hearth")]}
    )
    workspace = workspace.model_copy(update={"spec": workspace.spec.model_copy(update={"topology": [topology]})})
    graph = ResolvedWorkspaceGraph(
        workspace=workspace,
        providers={"hetzner_dc_eu_de": _provider()},
        resources={"haven_vm_hetzner_hearth": _resource()},
        namespaces={"hearth": _namespace("hearth")},
    )
    payload = build_strata_payload(graph)
    assert payload["topologies"]["hetzner_hearth"]["namespaces"] == ["hearth"]


def test_build_strata_payload_modules_present():
    """New in this redesign — previously skipped entirely (zero real use
    found). `mounts`/`checks`/`endpoints` nest under `properties`, matching
    `ModuleSpecModel`'s real shape, not as flat siblings of `type`/`source`."""
    graph = ResolvedWorkspaceGraph(
        workspace=_workspace(resources=[], topology_name=None),
        providers={"hetzner_dc_eu_de": _provider()},
        modules={"authentik": _module()},
    )
    payload = build_strata_payload(graph)
    assert payload["modules"]["authentik"] == {
        "type": "helm",
        "source": {"source_path": "modules/authentik"},
        "release_name": "authentik",
        "kubernetes_namespace": "hearth",
        "properties": {"mounts": [], "checks": [], "endpoints": []},
        "configuration": {},
        "custom": {},
        "default_labels": {},
        "custom_labels": {},
    }


def test_build_strata_payload_always_emits_every_category_even_when_empty():
    """Unlike `build_configuration_payloads()`/`planned_files()`'s old
    'skip empty categories' convention (which existed to avoid writing an
    empty *file* per category — no longer relevant with one shared file),
    every one of the fifteen keys is always present, empty when unused —
    resolves the Type design section's own open question in favor of a
    strict, no-top-level-`optional()` generated type."""
    payload = build_strata_payload(_graph(topology_name=None))
    assert set(payload) == {
        "workspace",
        "tenant",
        "providers",
        "resources",
        "namespaces",
        "modules",
        "dns",
        "networks",
        "firewalls",
        "topologies",
        "properties",
        "custom",
        "flags",
        "variables",
        "default_tags",
    }
    for empty_category in (
        "tenant",
        "namespaces",
        "modules",
        "dns",
        "networks",
        "firewalls",
        "topologies",
        "flags",
        "variables",
        "default_tags",
    ):
        assert payload[empty_category] == {}


# ---------------------------------------------------------------------------
# find_secret_paths() / rewrite_strata_payload() (ADR-0035, docs/work/
# unified-terraform-projection.md Phase 3) — the single-object replacement
# for resolve_deploy_time_files(), tested standalone against synthetic
# payloads rather than a real build_strata_payload() output, since neither
# function cares what produced the dict it's given.
# ---------------------------------------------------------------------------


def test_find_secret_paths_finds_secrets_at_multiple_depths():
    payload = {
        "variables": {"db_password": "${secret:DB_PASSWORD}"},
        "providers": {"kamatera": {"configuration": {"api_token": "${secret:API_TOKEN}"}}},
        "workspace": {"name": "ws"},  # non-secret sibling, must not appear
    }
    assert find_secret_paths(payload) == ["variables.db_password", "providers.kamatera.configuration.api_token"]


def test_find_secret_paths_ignores_non_secret_tokens_without_resolving_them():
    """A build-time payload legitimately carries unresolved `${var:}`/
    `${feature:}` tokens too (ADR-0022 D4) — this function must not choke
    on them the way reusing `resolve_value_tokens_tracking_secrets(payload, {})`
    would (it would raise ValueError on a var token with no matching key)."""
    payload = {"workspace": {"environment": "${var:ENVIRONMENT}"}}
    assert find_secret_paths(payload) == []


def test_find_secret_paths_empty_when_no_secrets():
    assert find_secret_paths({"workspace": {"name": "ws"}}) == []


def test_find_secret_paths_walks_list_indices():
    payload = {"modules": [{"configuration": {"token": "${secret:T}"}}]}
    assert find_secret_paths(payload) == ["modules.0.configuration.token"]


def test_rewrite_strata_payload_sentinel_replaces_secret_leaf():
    payload = {"variables": {"db_password": "${secret:DB_PASSWORD}", "db_host": "${var:DB_HOST}"}}
    tokens = {"DB_PASSWORD": "hunter2", "DB_HOST": "db.internal"}

    resolved, secrets = rewrite_strata_payload(payload, tokens)

    assert resolved["variables"]["db_password"] == "__strata_secret__:variables.db_password"
    assert resolved["variables"]["db_host"] == "db.internal"  # non-secret sibling resolved normally
    assert secrets == {"variables.db_password": "hunter2"}


def test_rewrite_strata_payload_real_value_never_appears_in_the_payload():
    payload = {"providers": {"kamatera": {"configuration": {"api_token": "${secret:API_TOKEN}"}}}}
    resolved, secrets = rewrite_strata_payload(payload, {"API_TOKEN": "super-secret-value"})

    assert "super-secret-value" not in json.dumps(resolved)
    assert secrets["providers.kamatera.configuration.api_token"] == "super-secret-value"


def test_rewrite_strata_payload_sentinel_at_a_nested_depth():
    payload = {"providers": {"kamatera": {"configuration": {"api_token": "${secret:API_TOKEN}"}}}}
    resolved, _ = rewrite_strata_payload(payload, {"API_TOKEN": "x"})
    assert (
        resolved["providers"]["kamatera"]["configuration"]["api_token"]
        == "__strata_secret__:providers.kamatera.configuration.api_token"
    )


def test_rewrite_strata_payload_no_secrets_returns_fully_resolved_payload_unchanged_shape():
    payload = {"workspace": {"environment": "${var:ENVIRONMENT}"}}
    resolved, secrets = rewrite_strata_payload(payload, {"ENVIRONMENT": "prd"})
    assert resolved == {"workspace": {"environment": "prd"}}
    assert secrets == {}


# ---------------------------------------------------------------------------
# build_strata_locals() (ADR-0035, docs/design/unified-terraform-projection.md
# Phase 6) — generates strata_locals.tf's full text.
# ---------------------------------------------------------------------------


def test_build_strata_locals_no_secrets_is_the_one_line_fallback():
    assert "locals {\n  strata = var.strata\n}\n" in build_strata_locals([])
    assert "merge(" not in build_strata_locals([])


def test_build_strata_locals_single_path_emits_one_lookup():
    content = build_strata_locals(["variables.db_password"])
    assert 'lookup(var.strata_secrets, "variables.db_password", var.strata["variables"]["db_password"])' in content


def test_build_strata_locals_shared_parent_merges_into_one_nested_call_not_two():
    """Two secrets sharing `providers.kamatera.configuration` must produce
    exactly one `merge(var.strata["providers"]["kamatera"]["configuration"], {...})`
    call with both overrides inside it, not two independent top-level
    merges that would clobber each other (each `merge()` call replaces the
    whole key it's assigned to)."""
    content = build_strata_locals(
        ["providers.kamatera.configuration.api_token", "providers.kamatera.configuration.other_secret"]
    )
    assert content.count('merge(var.strata["providers"]["kamatera"]["configuration"],') == 1
    assert content.count('merge(var.strata["providers"],') == 1  # one path down to "providers", not two
    assert '"api_token" = lookup(var.strata_secrets, "providers.kamatera.configuration.api_token"' in content
    assert '"other_secret" = lookup(var.strata_secrets, "providers.kamatera.configuration.other_secret"' in content


def test_build_strata_locals_two_different_top_level_categories_both_present():
    content = build_strata_locals(["variables.db_password", "providers.kamatera.configuration.api_token"])
    assert '"variables" = merge(var.strata["variables"],' in content
    assert '"providers" = merge(var.strata["providers"],' in content


def test_build_strata_locals_secret_inside_a_list_uses_a_for_expression_not_merge():
    """`merge()` rejects a list/tuple argument outright — a secret reached
    through a list index (e.g. a DNS zone's `records.0.value`) must
    rebuild the list via `[for idx, item in ... : ...]` instead, never
    `merge(var.strata[...]["records"], {...})`."""
    content = build_strata_locals(["dns.public-dns.zones." + escape_path_segment("example.com") + ".records.0.value"])
    assert '"records" = [for idx, item in var.strata["dns"]["public-dns"]["zones"]["example.com"]["records"]' in content
    # merge() on the *list itself* (no index) is the invalid pattern this
    # guards against; merge() on an *indexed element* (a dict) is fine.
    assert 'merge(var.strata["dns"]["public-dns"]["zones"]["example.com"]["records"], ' not in content
    assert "idx == 0 ?" in content
    assert (
        'lookup(var.strata_secrets, "dns.public-dns.zones.example\\\\.com.records.0.value", '
        'var.strata["dns"]["public-dns"]["zones"]["example.com"]["records"][0]["value"])' in content
    )


def test_build_strata_locals_list_with_two_secret_indices_both_present():
    content = build_strata_locals(["services.0.env.DB_PASSWORD", "services.2.env.API_KEY"])
    assert content.count("for idx, item in") == 1
    assert "idx == 0 ?" in content
    assert "idx == 2 ?" in content
