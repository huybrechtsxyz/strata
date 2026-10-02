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
- `dns`/`networks` are built with `DnsRecordModel.value`/`SubnetModel.cidr`/
  `NetworkDefinitionModel.address_space` written as-is, tokens (ADR-0002)
  unresolved — this build-time projection deliberately never resolves them
  (ADR-0022 D4); `deploy_run()` re-resolves both categories per step via
  `build_dns_networks_firewalls_payloads()` below, delivered as
  `TF_VAR_dns`/`TF_VAR_networks` (docs/design/value-token-resolution.md's
  "Full Solution" Phase 2, implemented — not a gap anymore).
- Until docs/design/gap_fit_v1.md gap #17, `_build_providers_payload()` silently
  dropped `ProviderSpecModel.configuration`/`.custom` entirely — fixed;
  see that function's own docstring.
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
  the real `spoke_resx`/`env_resx` case in `config-deploy`) has
  nothing to categorise and is skipped too.
"""

from typing import Any

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceResourceModel


def _build_workspace_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`workspace_name`/`workspace_version`/`deployment_name`/`environment`/
    `platform_version`/`labels`/`metadata` — six independent, flat
    Terraform variables (docs/design/terraform-tfvars-parity.md), matching
    v1's real `_build_workspace_vars()` exactly (confirmed directly), plus
    `configuration`/`custom`/`default_tags` — `WorkspaceSpecModel`'s own
    passthrough fields, previously validated but never read anywhere in this
    module (same silent-data-loss gap as `_build_dns_payload()`'s zone fields,
    found and fixed alongside it).

    - `workspace_version` — `workspace.meta.labels["version"]`, default
      `"1.0.0"` (v1's own default, not a v2 invention).
    - `deployment_name`/`platform_version` — the deployment document's own
      `meta.name`/`apiVersion` (v1's `platform.meta.name`/`platform.apiVersion`
      — v1 calls the resolved deployment artifact `platform`, the same
      document this module's own `graph.deployment` now carries).
    - `environment` — `deployment.meta.labels["environment"]`, default
      `"production"` (v1's own default). **Not** `DeploymentSpecModel.
      environments` — confirmed against v1's real source this is a plain
      label, not the deployment's environment-reference list.
    - `metadata.deployment_version` — `deployment.meta.labels["version"]`,
      default to `workspace_version` (v1's own fallback chain).
    - `metadata.workspace_description`/`deployment_description` —
      `workspace.meta.annotations`/`deployment.meta.annotations`'s
      `"description"` key, same convention every other category here
      already uses for its own `description` field.
    - `metadata.workspace_tags`/`deployment_tags` — `workspace.meta.tags`/
      `deployment.meta.tags`.

    `graph.deployment` is `None` for a caller with no deployment in scope
    (none exist today) — falls back to v1's own documented defaults for
    every deployment-derived field rather than raising, so `build run`
    against a hypothetical deployment-less graph still gets a valid,
    if minimal, `workspace.auto.tfvars.json`.
    """
    meta = graph.workspace.meta
    workspace_labels = meta.labels or {}
    workspace_version = workspace_labels.get("version", "1.0.0")
    deployment = graph.deployment

    if deployment is not None:
        deployment_labels = deployment.meta.labels or {}
        deployment_name = deployment.meta.name
        environment = deployment_labels.get("environment", "production")
        platform_version = (
            deployment.apiVersion.value if hasattr(deployment.apiVersion, "value") else str(deployment.apiVersion)
        )
        deployment_version = deployment_labels.get("version", workspace_version)
        deployment_description = (deployment.meta.annotations or {}).get("description", "")
        deployment_tags = deployment.meta.tags or []
    else:
        deployment_name = meta.name
        environment = "production"
        platform_version = ""
        deployment_version = workspace_version
        deployment_description = ""
        deployment_tags = []

    spec = graph.workspace.spec
    return {
        "workspace_name": meta.name,
        "workspace_version": workspace_version,
        "deployment_name": deployment_name,
        "environment": environment,
        "platform_version": platform_version,
        "labels": workspace_labels,
        "configuration": spec.configuration or {},
        "custom": spec.custom or {},
        "default_tags": spec.default_tags or {},
        "metadata": {
            "deployment_version": deployment_version,
            "workspace_description": (meta.annotations or {}).get("description", ""),
            "deployment_description": deployment_description,
            "workspace_tags": meta.tags or [],
            "deployment_tags": deployment_tags,
        },
    }


def _build_providers_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {type, region, display_name, description, labels, tags,
    configuration, custom, default_tags} per `ProviderPropertiesModel`/
    `ProviderSpecModel`/`ProviderMetaModel`.

    `default_tags` added alongside the DNS zone passthrough fix — same gap:
    `ProviderSpecModel.default_tags` was validated but never read anywhere
    in the codebase; gap #17 only fixed `configuration`/`custom` here, not
    this sibling field.

    `configuration`/`custom` added docs/design/value-token-resolution.md's
    "Decision (2026-09-29)" fix (docs/design/gap_fit_v1.md gap #17) — previously
    silently dropped from every Terraform artifact despite passing `strata
    validate` cleanly (`ProviderSpecModel.configuration`/`.custom` are
    checked by `unresolved_value_tokens()` via `_documents_reachable_from_
    workspace()`'s whole-document walk, but were never read anywhere in
    this module before this fix — confirmed by grep, unlike every other
    `configuration`/`custom`-bearing category here).

    `description`/`labels`/`tags` added docs/design/terraform-tfvars-parity.md
    — v1's real `_build_provider_vars()` (confirmed directly) emits these
    three from the provider component's own `description`/`labels`/`tags`,
    v2's equivalents being `meta.annotations["description"]`/`meta.labels`/
    `meta.tags` (the same convention every other category here already
    uses). v1 also emits a `version` field (`provider.properties.version`)
    with no v2 model equivalent at all — deliberately not restored here;
    that doc's own Open Questions track it as a separate, unresolved
    question (no confirmed real usage evidence for it, unlike these three).
    """
    payload: dict[str, Any] = {}
    for name, provider in graph.providers.items():
        properties = provider.spec.properties
        meta = provider.meta
        payload[name] = {
            "type": properties.type,
            "region": properties.region,
            "display_name": properties.display_name,
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "configuration": provider.spec.configuration or {},
            "custom": provider.spec.custom or {},
            "default_tags": provider.spec.default_tags or {},
        }
    return payload


def _build_topologies_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {type, components: [...], volumes: [...]}.

    `workspace.spec.topology` is optional (a workspace may have no grouping
    concept at all) — an empty dict, not an error, when it is unset. Entries
    are inline `TopologySpecModel` blocks (ADR-0028), read directly off the
    workspace — no separate document lookup.
    """
    payload: dict[str, Any] = {}
    for topology in graph.workspace.spec.topology or []:
        payload[topology.name] = {
            "type": topology.type,
            "components": [c.model_dump(mode="json", exclude_none=True) for c in topology.components],
            "volumes": [v.model_dump(mode="json", exclude_none=True) for v in (topology.volumes or [])],
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
    """name -> {description, labels, tags, modules: [module names],
    configuration, custom, default_labels, custom_labels} (Phase 2a).

    Matches v1's real `_build_namespace_vars()` shape (confirmed directly).
    `description` falls back to `""` when `meta.annotations` has none set,
    same as v1's own `namespace.annotations.get("description", "")`.

    `configuration`/`custom`/`default_labels`/`custom_labels` added alongside
    the DNS zone passthrough fix — confirmed by grep that `NamespaceSpecModel`'s
    own fields of the same names were validated but never read anywhere,
    including by `helm.py`/`compose.py`'s `prepare_namespace()` (the only other
    candidate consumer).
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
            "configuration": namespace.spec.configuration or {},
            "custom": namespace.spec.custom or {},
            "default_labels": namespace.spec.default_labels or {},
            "custom_labels": namespace.spec.custom_labels or {},
        }
    return payload


def _build_firewalls_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, rules: {reset, defaults, deny, allow},
    configuration, custom, default_tags, custom_tags} (Phase 2a). Matches v1's
    real `_build_firewall_vars()` shape (confirmed directly). `by_alias=True`
    on the rule dumps so `from_` serialises back to `from` (the schema's real
    field name, aliased for the `from`/Python keyword clash) - matches v1's
    own `model_dump(..., by_alias=True)`.

    `configuration`/`custom`/`default_tags`/`custom_tags` added alongside the
    DNS zone passthrough fix — `FirewallSpecModel`'s own fields of the same
    names were validated but never read anywhere in this module at all
    (unlike DNS/providers, not even partially fixed by gap #17).

    `FirewallRuleModel.name`/`.priority`/`.custom` need no change here at
    all — already flow through automatically via the existing per-rule
    `model_dump(..., exclude_none=True)` calls below, unlike every other
    passthrough fix in this module (those all needed an explicit new dict
    key since their payload entries are built field-by-field, not dumped).
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
            "configuration": spec.configuration or {},
            "custom": spec.custom or {},
            "default_tags": spec.default_tags or {},
            "custom_tags": spec.custom_tags or {},
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
    reproduce. `record.value` is written as-is here, tokens and all - this
    build-time projection deliberately never resolves them (ADR-0022 D4:
    "build run renders; it does not execute"); `deploy_run()` re-resolves
    this whole category per step via `build_dns_networks_firewalls_payloads()`
    (docs/design/value-token-resolution.md's "Full Solution" Phase 2,
    `deploy_controller.py`), delivered as `TF_VAR_dns`, never rewriting the
    file this function produced.

    Per-record `custom` added separately — `DnsRecordModel` had no passthrough
    field at all until then (flagged, not acted on, during the Subnet/
    FirewallRuleModel escape-hatch audit: Azure alias records, Cloudflare's
    `proxied` flag, Route53 weighted/health-check routing all need a
    record-level extension point with no cross-provider equivalent).
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
                            "custom": record.custom or {},
                        }
                        for record in zone.records or []
                    ],
                    "configuration": zone.configuration or {},
                    "custom": zone.custom or {},
                    "default_tags": zone.default_tags or {},
                    "custom_tags": zone.custom_tags or {},
                }
                for zone in spec.zones
            },
        }
    return payload


def _build_networks_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """name -> {description, labels, tags, networks: {network_name:
    {address_space, subnets, peerings, configuration, custom, default_tags,
    custom_tags}}} (Phase 2c).

    `configuration`/`custom`/`default_tags`/`custom_tags` added alongside the
    DNS zone passthrough fix — `NetworkDefinitionModel`'s own fields of the
    same names were validated but never read anywhere in this module.

    Per-subnet `configuration`/`custom` added separately — `SubnetModel` had
    no passthrough fields at all until then (delegations, service endpoints,
    NSG association, etc. had no escape hatch, unlike every other per-item
    model in this category).

    Adapted from v1's real `_build_network_vars()`. `address_space`/
    `subnet.cidr` may themselves contain `${var:}`/`${secret:}`/`${feature:}`
    tokens (ADR-0002) - written as-is here, same build-time-never-resolves
    reasoning as `_build_dns_payload()` above; `deploy_run()` re-resolves
    this category per step as `TF_VAR_networks` (Phase 2, same function).
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
                        subnet.name: {
                            "cidr": subnet.cidr,
                            "description": subnet.description,
                            "configuration": subnet.configuration or {},
                            "custom": subnet.custom or {},
                        }
                        for subnet in net.subnets
                    },
                    "peerings": {p.name: {"target": p.target} for p in net.peerings or []},
                    "configuration": net.configuration or {},
                    "custom": net.custom or {},
                    "default_tags": net.default_tags or {},
                    "custom_tags": net.custom_tags or {},
                }
                for net in network_doc.spec.networks
            },
        }
    return payload


def _build_flags_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`{key: bool}` for `flags.auto.tfvars.json` (docs/design/
    build-time-value-categories.md, Q1/Q2) - matches v1's real filename
    (`_build_feature_flags_vars()`), not the `feature_refs` field name.

    Only entries `build_value_references()` already resolved (`constant`/
    `environment` stores) carry a `value` - everything else (integration-
    backed stores) is filtered out here, never emitted.
    """
    return {ref.key: ref.value for ref in graph.feature_refs if ref.value is not None}


def _build_variables_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`{key: value}` for `variables.auto.tfvars.json`, native-typed where
    `build_value_references()` could tell (docs/design/
    build-time-value-categories.md, Q1/Q2). Same `constant`/`environment`-
    only filter as `_build_flags_payload()`.
    """
    return {ref.key: ref.value for ref in graph.variable_refs if ref.value is not None}


def _build_properties_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`properties.auto.tfvars.json` - already the final merged dict
    (workspace -> environment(s) -> deployment's own), computed once by
    `build_controller.py` (docs/design/build-time-value-categories.md, Q3).
    """
    return graph.properties


def _build_custom_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`custom.auto.tfvars.json` - same merge as `_build_properties_payload()`,
    `custom` source instead of `properties` (Q3)."""
    return graph.custom


def _build_tenant_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`tenant.auto.tfvars.json` - the deployment's own `spec.tenant`
    reference (docs/design/build-command.md's `tenant` category), resolved
    once by `value_controller.resolve_tenant()`.

    Flat top-level keys here (the *value* this function returns), matching
    every other category's own builder function — `planned_files()` wraps
    this under `strata_tenant` before writing the file
    (docs/design/terraform-tfvars-parity.md's `_REAL_VARIABLE_NAME`),
    confirmed directly against v1's real `_build_tenant_vars()` (which
    returns `{"strata_tenant": {code, name, zones, onboarded,
    configuration}}`) *and* independently against `tenant_zone_policy.md`'s
    own real `plan_data["variables"]["strata_tenant"]` read — the field
    names below (`code`/`name`/`zones`/`onboarded`/`configuration`) already
    matched v1 exactly before this fix; only the wrapper key was missing.

    Empty dict when the deployment references no tenant - `planned_files()`
    already skips empty categories, so no `tenant.auto.tfvars.json` is
    written at all in that case, same treatment as an unset `dns_zones`.
    """
    if graph.tenant is None:
        return {}
    spec = graph.tenant.spec
    return {
        "code": graph.tenant.meta.name,
        "name": spec.display_name,
        "zones": spec.geographies,
        "onboarded": spec.onboarded.isoformat() if spec.onboarded else None,
        "configuration": spec.configuration or {},
    }


def build_dns_networks_firewalls_payloads(graph: ResolvedWorkspaceGraph) -> dict[str, dict[str, Any]]:
    """The three Terraform-delivered, Value-token-bearing categories with
    per-name step ownership (docs/design/gap_fit_v1.md gap #12) — `dns`/`networks`/
    `firewalls` — grouped for deploy-time token resolution
    (docs/design/value-token-resolution.md's "Full Solution" Phase 2).

    **Not the only token-bearing categories** — `build_configuration_payloads()`
    (below) delivers ten more (`workspace`/`providers`/`resx_<type>`/
    `topologies`/`namespaces`/`flags`/`variables`/`properties`/`custom`/
    `tenant`, Phase 6 + gap #17's universal-reach fix) via a deliberately
    *separate* function: those ten have no per-name ownership concept at
    all (`${output:}` is rejected outright for them), a genuinely different
    claiming semantics than this function's own dns/networks/firewalls,
    which do support it (gap #12).
    """
    return {
        "dns": _build_dns_payload(graph),
        "networks": _build_networks_payload(graph),
        "firewalls": _build_firewalls_payload(graph),
    }


def build_configuration_payloads(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """Ten more Value-token-bearing categories, broadcast (no per-name step
    ownership, unlike `build_dns_networks_firewalls_payloads()` above) —
    every one of `build_platform_projection()`'s 13 categories except
    dns/networks/firewalls: `workspace`/`providers`/`resx_<type>`/
    `topologies`/`namespaces`/`flags`/`variables`/`properties`/`custom`/
    `tenant`.

    Originally five (`resx_<type>`/`topologies`/`properties`/`custom`/
    `tenant`, docs/design/value-token-resolution.md's "Full Solution"
    Phase 6, docs/design/gap_fit_v1.md gap #8's Terraform-side refinement) — extended
    to all ten by gap #17 (docs/design/value-token-resolution.md's
    "Decision (2026-09-29)": resolution reach must match validation reach,
    no curated subset). The confirmed real gap that motivated the
    extension: `ProviderSpecModel.configuration`/`.custom` passed `strata
    validate` cleanly (`_documents_reachable_from_workspace()` already
    walks the whole Provider document) yet was never projected into any
    Terraform artifact at all — silently dropped, not even delivered as an
    unresolved literal. `workspace`/`namespaces`/`flags`/`variables` came
    along for free from the same generalization (none of the four
    realistically carry a token-bearing field, so adding them is a no-op
    in practice — see this module's own tests) but are included on
    principle: a curated allowlist is exactly the shape of bug gap #17
    fixes, so this function must not reintroduce one.

    Each category carries a real `configuration`/`custom`-shaped free-form
    passthrough (`ProviderSpecModel.configuration`/`.custom`,
    `ResourceSpecModel.configuration`/`WorkspaceResourceModel.configuration`,
    `TopologyVolumeModel.configuration`, the merged `properties`/`custom`
    dicts, `TenantSpecModel.configuration`) that can contain a
    `${var:}`/`${secret:}`/`${feature:}` token — projected into a
    `*.auto.tfvars.json` file at build time (`build_platform_projection()`)
    but, until resolved here, never re-resolved at deploy time the way
    dns/networks/firewalls already are.

    Keyed to match `planned_files()`'s own per-category filenames exactly
    (`resx_<type>`, not a combined `resources_by_category` key — a real
    root module declares `variable "resx_compute" {}` per resource type,
    matching the one-file-per-type convention `planned_files()` already
    uses at build time) — so `deploy_controller.py` can deliver each as
    `TF_VAR_<key>` with zero translation.

    `${output:...}` is never valid in any of these ten — deliberately not
    checked here (that is `deploy_controller.py`'s pre-flight job, same
    split `build_dns_networks_firewalls_payloads()`'s own caller already
    has for its three categories) — this function only builds the raw,
    still-token-bearing payloads.

    Returns:
        Only non-empty categories — matches `planned_files()`'s own
        "skip empty categories" convention, so an unset `tenant`/no
        `resources`/no `topologies` contributes nothing to deliver.
        `workspace` is a practical exception: a workspace document always
        has a non-empty `name`, so it is unconditionally present whenever
        any category is (harmless — no realistic token there, and the
        value already matches what `build_platform_projection()` wrote to
        disk at build time).
    """
    payloads: dict[str, Any] = {
        "workspace": _build_workspace_payload(graph),
        "providers": _build_providers_payload(graph),
        "properties": _build_properties_payload(graph),
        "custom": _build_custom_payload(graph),
        "tenant": _build_tenant_payload(graph),
        "topologies": _build_topologies_payload(graph),
        "namespaces": _build_namespaces_payload(graph),
        "flags": _build_flags_payload(graph),
        "variables": _build_variables_payload(graph),
    }
    for resource_type, type_payload in _build_resources_payload(graph).items():
        payloads[f"resx_{resource_type}"] = type_payload
    return {name: payload for name, payload in payloads.items() if payload}


def build_platform_projection(graph: ResolvedWorkspaceGraph, provisioner: ProvisionerModel) -> dict[str, Any]:
    """The default projection (D1: Phase 1's four structural categories,
    Phase 2a's namespaces/firewalls, Phase 2c's dns/networks, and
    docs/design/build-time-value-categories.md's `flags`/`variables`/
    `properties`/`custom`).

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
        "flags": _build_flags_payload(graph),
        "variables": _build_variables_payload(graph),
        "properties": _build_properties_payload(graph),
        "custom": _build_custom_payload(graph),
        "tenant": _build_tenant_payload(graph),
    }


# Real v1 Terraform variable name per category, where it differs from the
# file's own category name (docs/design/terraform-tfvars-parity.md) —
# confirmed directly against v1's real `_build_*_vars()` return values.
# `planned_files()` wraps each category's payload under this key so the
# file's *content* matches the real variable a Terraform root declares
# (`variable "platform_providers" {}`, not `variable "providers" {}`),
# while the *filename* stays `<category>.auto.tfvars.json` (v1's own real,
# confirmed filenames — the two are independent conventions in v1, not one).
# A category absent here (`topologies`/`namespaces`/`firewalls`/`networks`)
# still gets wrapped — just under the *same* word as its own category name
# (v1's real `_build_topology_vars()`/etc. all return `{"<name>": {...}}`,
# one level of nesting, even where the wrapper key equals the file's own
# category name) — `.get(category, category)` below covers that case.
_REAL_VARIABLE_NAME: dict[str, str] = {
    "providers": "platform_providers",
    "dns": "dns_zones",
    "tenant": "strata_tenant",
}

# Categories v1 treats as flat, multi-variable bags — no wrapper key at
# all, each top-level key is its own independent Terraform variable
# (docs/design/terraform-tfvars-parity.md's three-shape convention).
# Public — `deploy_controller.py`'s `TF_VAR_<name>` delivery needs the same
# set to know which categories are several independent variables (one per
# top-level key) rather than one variable per category.
FLAT_CATEGORIES = {"workspace", "flags", "variables", "properties", "custom"}


def real_variable_name(category: str) -> str:
    """The real v1 Terraform variable name for a `planned_files()`/
    `build_configuration_payloads()`/`build_dns_networks_firewalls_payloads()`
    category — `_REAL_VARIABLE_NAME`'s lookup, identity for everything
    else (docs/design/terraform-tfvars-parity.md).

    Identity is the correct answer, not a fallback-of-convenience, for two
    different reasons depending on the category: `topologies`/`namespaces`/
    `firewalls`/`networks` already use the same word for both their file
    category and their real variable name (nothing to translate);
    `FLAT_CATEGORIES` (`workspace`/`flags`/`variables`/`properties`/`custom`)
    and `resx_<type>` have **no single real variable name at all** to
    rename to — each is either several independent variables (`workspace`'s
    six top-level keys, one per real variable) or, for `resx_<type>`,
    actually named `resources` in every real file regardless of type
    (confirmed against v1's real `_build_resources_by_category()`), which
    cannot be delivered as a single env var without colliding across
    resource types. `deploy_controller.py`'s `TF_VAR_<name>` delivery
    handles both of those cases itself (`FLAT_CATEGORIES`'s per-key
    delivery, `resx_<type>`'s merge-into-one-"resources"-variable) rather
    than through this function, since neither reduces to a single rename.

    Exposed for `deploy_controller.py`'s `TF_VAR_<name>` env var delivery
    — the same rename `planned_files()` already applies to the on-disk
    file's content key must also apply to the env var's own *name*, not
    just its value, for a real Terraform root's `TF_VAR_<declared_variable_name>`
    override mechanism to actually see it.
    """
    return _REAL_VARIABLE_NAME.get(category, category)


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

    Every category except `FLAT_CATEGORIES` is wrapped under its real v1
    Terraform variable name (`_REAL_VARIABLE_NAME`, defaulting to the
    category's own name) before being written — e.g.
    `providers.auto.tfvars.json`'s real content is
    `{"platform_providers": {...}}`, and `topologies.auto.tfvars.json`'s is
    `{"topologies": {...}}` — not the bare `{name: {...}}` map each
    `_build_*_payload()` function itself returns. The wrap happens here,
    not inside each builder function, so `build_configuration_payloads()`'s
    deploy-time re-resolution (`deploy_controller.py`'s `TF_VAR_<category>`
    delivery) keeps working against the same flat, unwrapped payloads —
    only the on-disk *file* shape needs the real variable-name wrapper.
    `resources_by_category` needs no wrapping here either — it is already
    produced pre-wrapped per type (`{"resources": {...}}`) by
    `_build_resources_payload()`.
    """
    files: list[tuple[str, dict[str, Any]]] = []
    for category, data in payload.items():
        if not data:
            continue
        if category == "resources_by_category":
            for resource_type, type_payload in data.items():
                files.append((f"resx_{resource_type}.auto.tfvars.json", type_payload))
            continue
        if category in FLAT_CATEGORIES:
            files.append((f"{category}.auto.tfvars.json", data))
            continue
        real_name = _REAL_VARIABLE_NAME.get(category, category)
        files.append((f"{category}.auto.tfvars.json", {real_name: data}))
    return files
