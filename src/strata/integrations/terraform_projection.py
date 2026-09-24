#!/usr/bin/env python3
"""The default Terraform output projection (ADR-0023 D1, Phase 1 + 2a + 2c
dns/networks).

One function per category, not a method on any model. Not a style
preference: ADR-0003's import-linter contract has `strata.integrations`
sitting *above* `strata.models`, so a `WorkspaceModel.to_tfvars_payload()`-
style method would mean the model layer reaching up into Terraform-specific
shaping (backwards). The `resources_by_category` merge also can't be one
model's method regardless of layering — it needs a `ResourceModel`'s own
fields *and* the workspace's `WorkspaceResourceModel` override merged on
top, two documents at once.

A sibling module to `terraform.py`, not a method on `TerraformIntegration`
— the projection only needs a `ResolvedWorkspaceGraph` (already-resolved
plain models), never `DocumentIndex`/`SolutionContext` access, which
`InfraIntegration` subclasses are not allowed to touch (ADR-0021 D2).

Deliberately excludes several things this module does not build (see
ADR-0023's own Phase 1/2/3 split, checked against real evidence, not
assumed):

- `modules` — checked all six real workspaces available for this ADR;
  zero use of `TopologyComponentModel.modules` in any of them (Compose/Helm
  modules go through the entirely separate `prepare_namespace()` pipeline,
  ADR-0022 D5-D7, never this projection). Skipped, not built.
- `tenant` (Phase 2c's remaining item — no `tenant` in any workspace
  checked, so no fixture to ground its shape against yet).
- `dns`/`networks` are built, but with a known gap: `DnsRecordModel.value`/
  `SubnetModel.cidr`/`NetworkDefinitionModel.address_space` may themselves
  contain `${var:}`/`${secret:}`/`${feature:}` tokens (ADR-0002) - written
  as-is here, unresolved; wiring Phase 3's `resolve_expr_tokens()` into
  these two categories too is not done yet (see ADR's Remaining Work).
- `required_variables`/`required_features`/`required_secrets` (deferred —
  no v2 model has a `references` field to walk for this; building it means
  regex-scanning resolved config for `${var:}`/`${secret:}`/`${feature:}`
  tokens, Phase 3's job).
- `custom` is never read for `resources_by_category` — only `configuration`
  is merged. Checked against both real workspaces available and found zero
  use of `custom` anywhere; `configuration`/`custom` are separate channels
  for a reason (`configuration` is merged verbatim into the deployer's own
  structures, `custom` is for scripts/extensions only) — if a value needs
  to reach Terraform's tfvars it goes in `configuration`, never `custom`.
- A `WorkspaceResourceModel` with `enabled=False` is skipped entirely, and
  one with `managed_by="provisioner"` (no backing `Resource` document —
  the real `spoke_resx`/`env_resx` case in `cfg-int-deployment`) has
  nothing to categorise and is skipped too.
"""


from typing import Any

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceResourceModel


def _build_workspace_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`WorkspaceMetaModel`/`WorkspaceSpecModel` -> name/labels/tags/annotations."""
    meta = graph.workspace.meta
    return {
        "name": meta.name,
        "labels": meta.labels or {},
        "tags": meta.tags or [],
        "annotations": meta.annotations or {},
    }


def _build_providers_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {type, region, display_name} per `ProviderPropertiesModel`."""
    payload: dict[str, Any] = {}
    for name, provider in graph.providers.items():
        properties = provider.spec.properties
        payload[name] = {
            "type": properties.type,
            "region": properties.region,
            "display_name": properties.display_name,
        }
    return payload


def _build_topologies_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {type, components: [...], volumes: [...]}.

    `workspace.spec.topology` is optional (a workspace may have no grouping
    concept at all) — an empty dict, not an error, when it is unset.
    """
    payload: dict[str, Any] = {}
    for name in graph.workspace.spec.topology or []:
        topology = graph.topologies[name]
        spec = topology.spec
        payload[name] = {
            "type": spec.type,
            "components": [c.model_dump(mode="json", exclude_none=True) for c in spec.components],
            "volumes": [v.model_dump(mode="json", exclude_none=True) for v in (spec.volumes or [])],
        }
    return payload


def _merge_resource_entry(workspace_resource: WorkspaceResourceModel, resource_spec: Any) -> dict[str, Any]:
    """One `resources_by_category` entry — `ResourceSpecModel`'s own fields
    with the matching `WorkspaceResourceModel` override fields merged on
    top ("workspace wins"). Only `configuration` is merged from the two
    documents (never `custom` — see module docstring); `labels`/`tags` on
    the entry are the *resource document's* `meta.labels`/`meta.tags`
    overridden by the workspace instance's own, since `ResourceSpecModel`
    itself has no plain `labels`/`tags` field (only `default_tags`/
    `custom_tags`, cloud tags, a distinct concept).
    """
    properties = resource_spec.properties
    return {
        "provider_type": properties.provider_type,
        "resource_type": properties.resource_type,
        "subcategory": properties.subcategory,
        "unit_cost": properties.unit_cost,
        "role": workspace_resource.role,
        "count": workspace_resource.count,
        "configuration": {
            **(resource_spec.configuration or {}),
            **(workspace_resource.configuration or {}),
        },
        "default_tags": {
            **(resource_spec.default_tags or {}),
            **(workspace_resource.default_tags or {}),
        },
        "custom_tags": {
            **(resource_spec.custom_tags or {}),
            **(workspace_resource.custom_tags or {}),
        },
        "firewalls": workspace_resource.firewalls or [],
        "subnet": (
            workspace_resource.subnet.model_dump(mode="json") if workspace_resource.subnet is not None else None
        ),
    }


def _build_resources_payload(graph: ResolvedWorkspaceGraph) -> dict[str, dict[str, dict[str, Any]]]:
    """Grouped by `ResourceSpecModel.properties.resource_type` (lowercased),
    each group wrapped as `{"resources": {name: entry}}` - matches v1's real
    `_build_resources_by_category()` exactly (confirmed by reading
    `terraform_builder.py` directly), not `category` and not a flat dict -
    an earlier draft grouped by `category` and skipped the wrapper, which
    also meant `planned_files()` wrote one combined file instead of v1's
    real one-file-per-resource-type split (`resx_<type>.auto.tfvars.json`).

    Skips a `WorkspaceResourceModel` with `enabled=False` entirely - that
    field's own docstring is explicit: "excludes it from the built platform
    artifact, and therefore from every provisioner that consumes it." Also
    skips `managed_by="provisioner"` entries (no backing `Resource`
    document to categorise - the real `spoke_resx`/`env_resx` case).
    """
    payload: dict[str, dict[str, dict[str, Any]]] = {}
    for workspace_resource in graph.workspace.spec.resources or []:
        if not workspace_resource.enabled:
            continue
        if workspace_resource.resource is None:
            continue
        resource = graph.resources[workspace_resource.resource]
        resource_type = (resource.spec.properties.resource_type or "uncategorized").lower()
        entries = payload.setdefault(resource_type, {}).setdefault("resources", {})
        entries[workspace_resource.name] = _merge_resource_entry(workspace_resource, resource.spec)
    return payload


def _build_namespaces_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, modules: [module names]} (Phase 2a).

    Matches v1's real `_build_namespace_vars()` shape (confirmed directly).
    `description` falls back to `""` when `meta.annotations` has none set,
    same as v1's own `namespace.annotations.get("description", "")`.
    """
    payload: dict[str, Any] = {}
    for name in graph.workspace.spec.namespaces or []:
        namespace = graph.namespaces[name]
        meta = namespace.meta
        payload[name] = {
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "modules": [str(m.module) for m in namespace.spec.modules or []],
        }
    return payload


def _build_firewalls_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, rules: {reset, defaults, deny, allow}}
    (Phase 2a). Matches v1's real `_build_firewall_vars()` shape (confirmed
    directly). `by_alias=True` on the rule dumps so `from_` serialises back
    to `from` (the schema's real field name, aliased for the `from`/Python
    keyword clash) - matches v1's own `model_dump(..., by_alias=True)`.
    """
    payload: dict[str, Any] = {}
    for name in graph.workspace.spec.firewalls or []:
        firewall = graph.firewalls[name]
        meta = firewall.meta
        spec = firewall.spec
        payload[name] = {
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "rules": {
                "reset": spec.reset or False,
                "defaults": [d.model_dump(mode="json", exclude_none=True) for d in spec.defaults or []],
                "deny": [r.model_dump(mode="json", exclude_none=True, by_alias=True) for r in spec.deny or []],
                "allow": [r.model_dump(mode="json", exclude_none=True, by_alias=True) for r in spec.allow or []],
            },
        }
    return payload


def _build_dns_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, provider, zones: {zone_name:
    {ttl, records: [...]}}} (Phase 2c).

    Adapted from v1's real `_build_dns_vars()`, not copied verbatim: v2's
    `DnsRecordModel` has a single `value` field that may itself contain
    `${var:}`/`${secret:}`/`${feature:}` tokens (ADR-0002), unlike v1's
    separate `value`/`var`/`secret`/`output_key` fields - v2 never ported
    `output_key` at all (ADR-0006, no shared runtime Context store yet), so
    there is no `dns_secret_records`/`dns_output_records` bucketing to
    reproduce. `record.value` is written as-is here, tokens and all -
    resolving those tokens is Phase 3's `resolve_expr_tokens()` job, not yet
    wired into this category (known gap, see ADR's Remaining Work).
    """
    payload: dict[str, Any] = {}
    for name in graph.workspace.spec.dns_zones or []:
        dns = graph.dns[name]
        meta = dns.meta
        spec = dns.spec
        payload[name] = {
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "provider": spec.provider,
            "zones": {
                zone.name: {
                    "ttl": zone.ttl,
                    "records": [
                        {
                            "name": record.name,
                            "type": record.type.value,
                            "value": record.value,
                            "ttl": record.ttl,
                            "priority": record.priority,
                        }
                        for record in zone.records or []
                    ],
                }
                for zone in spec.zones
            },
        }
    return payload


def _build_networks_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, networks: {network_name:
    {address_space, subnets, peerings}}} (Phase 2c).

    Adapted from v1's real `_build_network_vars()`. `address_space`/
    `subnet.cidr` may themselves contain `${var:}`/`${secret:}`/`${feature:}`
    tokens (ADR-0002) - written as-is, same known gap as `_build_dns_payload()`
    above pending Phase 3's token resolution.
    """
    payload: dict[str, Any] = {}
    for name in graph.workspace.spec.networks or []:
        network_doc = graph.networks[name]
        meta = network_doc.meta
        payload[name] = {
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "networks": {
                net.name: {
                    "address_space": list(net.address_space),
                    "subnets": {
                        subnet.name: {"cidr": subnet.cidr, "description": subnet.description}
                        for subnet in net.subnets
                    },
                    "peerings": {p.name: {"target": p.target} for p in net.peerings or []},
                }
                for net in network_doc.spec.networks
            },
        }
    return payload


def build_platform_projection(graph: ResolvedWorkspaceGraph, provisioner: ProvisionerModel) -> dict[str, Any]:
    """The default projection (D1: Phase 1's four structural categories,
    Phase 2a's namespaces/firewalls, Phase 2c's dns/networks).

    `provisioner` is currently unused by any category built so far -
    accepted now so a later phase's per-provisioner filtering (if any turns
    out to be needed) does not change this function's call sites again.
    """
    del provisioner
    return {
        "workspace": _build_workspace_payload(graph),
        "providers": _build_providers_payload(graph),
        "topologies": _build_topologies_payload(graph),
        "resources_by_category": _build_resources_payload(graph),
        "namespaces": _build_namespaces_payload(graph),
        "firewalls": _build_firewalls_payload(graph),
        "dns": _build_dns_payload(graph),
        "networks": _build_networks_payload(graph),
    }


def planned_files(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(filename, data) per non-empty category - Terraform's
    `*.auto.tfvars.json` auto-load convention (D1), no `-var-file` flag
    needed. Empty categories are skipped automatically, not a configuration
    knob (D2).

    `resources_by_category` unrolls into one file per resource type
    (`resx_<type>.auto.tfvars.json`) instead of a single combined file -
    matches v1's real `_planned_files()` (confirmed directly): each
    resource type is Terraform's own natural `for_each` unit, not the
    category grouping as a whole.
    """
    files: list[tuple[str, dict[str, Any]]] = []
    for category, data in payload.items():
        if not data:
            continue
        if category == "resources_by_category":
            for resource_type, type_payload in data.items():
                files.append((f"resx_{resource_type}.auto.tfvars.json", type_payload))
            continue
        files.append((f"{category}.auto.tfvars.json", data))
    return files
