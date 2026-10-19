#!/usr/bin/env python3
"""The default Terraform output projection (ADR-0035, docs/design/
unified-terraform-projection.md) — `build_strata_payload()` builds one
nested `strata` object (every top-level key always present, even empty)
from a `ResolvedWorkspaceGraph`; `planned_strata_file()` wraps it as the
single `strata.auto.tfvars.json` `build run` writes; `rewrite_strata_
payload()`/`find_secret_paths()` re-resolve it at deploy time, sentineling
any secret-shaped leaf rather than ever writing a real secret value to
disk; `build_strata_locals()` generates the `strata_locals.tf` a consumer
can use to merge a step's `TF_VAR_strata_secrets` back in.

One function per category, not a method on any model. Not a style
preference: ADR-0003's import-linter contract has `strata.integrations`
sitting *above* `strata.models`, so a `WorkspaceModel.to_tfvars_payload()`-
style method would mean the model layer reaching up into Terraform-specific
shaping (backwards).

A sibling module to `terraform.py`, not a method on `TerraformIntegration`
— the projection only needs a `ResolvedWorkspaceGraph` (already-resolved
plain models), never `DocumentIndex`/`SolutionContext` access, which
`InfraIntegration` subclasses are not allowed to touch (ADR-0021 D2).

Deliberately excludes `modules` attached to a topology (not a namespace) —
checked all six real workspaces available for this ADR; zero use of
`TopologyComponentModel.modules` in any of them (Compose/Helm modules go
through the entirely separate `prepare_namespace()` pipeline, ADR-0022
D5-D7, never this projection).
"""

import json
from typing import Any

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.utils.value_tokens import (
    escape_path_segment,
    extract_value_tokens,
    resolve_value_tokens_tracking_secrets,
    split_path,
)


def _strata_workspace(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.workspace` — v1-parity flat names (`workspace_name`/
    `metadata.*`) reshaped as plain nested fields, and `default_tags`
    hoisted out to its own top-level `strata.default_tags` category
    (`_strata_default_tags()` below) rather than nested here — see this
    design doc's own "The output surface" section for why.
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
        "name": meta.name,
        "version": workspace_version,
        "description": (meta.annotations or {}).get("description", ""),
        "tags": meta.tags or [],
        "deployment": deployment_name,
        "deployment_version": deployment_version,
        "deployment_description": deployment_description,
        "deployment_tags": deployment_tags,
        "environment": environment,
        "platform_version": platform_version,
        "labels": workspace_labels,
        "configuration": spec.configuration or {},
        "custom": spec.custom or {},
    }


def _strata_default_tags(graph: ResolvedWorkspaceGraph) -> dict[str, str]:
    """`strata.default_tags` — the solution-wide tag baseline, hoisted out
    of `workspace` (see `_strata_workspace()` above)."""
    return graph.workspace.spec.default_tags or {}


def _strata_tenant(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.tenant` — the deployment's own `spec.tenant` reference,
    resolved once by `value_controller.resolve_tenant()`. Two gap fixes
    folded in from day one: `custom` was a real `TenantSpecModel` field
    never read at all; `geographies` is the real field name (`spec.
    geographies`), not v1's `zones` rename — this redesign abandons v1
    output parity on purpose, so the field keeps its real name instead of
    being translated back to what v1 called it.
    """
    if graph.tenant is None:
        return {}
    spec = graph.tenant.spec
    return {
        "code": graph.tenant.meta.name,
        "name": spec.display_name,
        "geographies": spec.geographies,
        "onboarded": spec.onboarded.isoformat() if spec.onboarded else None,
        "configuration": spec.configuration or {},
        "custom": spec.custom or {},
    }


def _strata_providers(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.providers` — slot -> {provider, type, region, display_name,
    description, labels, tags, configuration, custom, default_tags} per
    `ProviderSpecModel`/`ProviderMetaModel`. Keyed by workspace slot, so a
    deployment rebinding a slot (docs/design/provider-slots.md) leaves IaC
    code unchanged; `provider` names the document actually bound."""
    payload: dict[str, Any] = {}
    for name, provider in graph.providers.items():
        spec = provider.spec
        meta = provider.meta
        payload[name] = {
            "provider": meta.name,
            "type": spec.type,
            "region": spec.region,
            "display_name": spec.display_name,
            "description": (meta.annotations or {}).get("description", ""),
            "labels": meta.labels or {},
            "tags": meta.tags or [],
            "configuration": spec.configuration or {},
            "custom": spec.custom or {},
            "default_tags": spec.default_tags or {},
        }
    return payload


def _strata_resources(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.resources` — a **flat map keyed by resource name**, not
    nested by resource type (v1's old per-type `resx_<type>` grouping) — see this design doc's "Resources are one
    flat map" section: the only real consumer checked (`haven`) re-merges
    the type split itself, and the split is a confirmed silent-data-loss
    bug when multiple `resx_<type>.auto.tfvars.json` files declare the
    same `resources` Terraform variable (last-file-wins, no warning).

    Two gap fixes folded in from day one, not patched into the old
    function first: `storage` (`ResourceSpecModel.storage` — disks/
    volumes/parameters, validated and round-trip tested but never
    projected anywhere until now) and `custom` (same "validated, never
    read" gap, merged workspace-wins like `configuration`/`default_tags`/
    `custom_tags` already are, for consistency with every other
    `WorkspaceResourceModel` override field here).
    """
    payload: dict[str, Any] = {}
    for workspace_resource in graph.workspace.spec.resources or []:
        if not workspace_resource.enabled:
            continue
        if workspace_resource.resource is None:
            continue
        resource = graph.resources[workspace_resource.resource]
        resource_spec = resource.spec
        payload[workspace_resource.name] = {
            "provider_type": resource_spec.provider_type,
            "resource_type": resource_spec.resource_type,
            "subcategory": resource_spec.subcategory,
            "unit_cost": resource_spec.unit_cost,
            "role": workspace_resource.role,
            "count": workspace_resource.count,
            "configuration": {
                **(resource_spec.configuration or {}),
                **(workspace_resource.configuration or {}),
            },
            "custom": {
                **(resource_spec.custom or {}),
                **(workspace_resource.custom or {}),
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
            "dns_zones": workspace_resource.dns_zones or [],
            "subnet": (
                workspace_resource.subnet.model_dump(mode="json") if workspace_resource.subnet is not None else None
            ),
            "storage": (
                resource_spec.storage.model_dump(mode="json", exclude_none=True)
                if resource_spec.storage is not None
                else None
            ),
        }
    return payload


def _strata_namespaces(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.namespaces` — name -> {description, labels, tags,
    modules: [module names], configuration, custom, default_labels,
    custom_labels}."""
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


def _strata_modules(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.modules` — new in this redesign (`graph.modules`, Phase 1).
    Previously skipped entirely (zero real use of `TopologyComponentModel
    .modules` found, and adding a per-category file/variable/rename-table
    entry for unproven demand was real cost) — in a single-object shape,
    adding one more nested key is free, and omitting it would itself have
    become the special case this whole redesign exists to delete.

    `mounts`/`checks`/`endpoints` are flattened directly onto `ModuleSpecModel`
    (ADR-0037), not nested under a `properties` sub-model — the output
    payload below still groups them under a `"properties"` key, since that's
    a separate, Terraform-facing shape decision, not the source model's own
    field layout. `services` is deliberately excluded (Helm chart/workload-
    builder internals, not something a Terraform root plausibly needs) —
    this is a read-only projection, it does not change the Helm/Compose
    workload pipeline at all.
    """
    payload: dict[str, Any] = {}
    for name, module in graph.modules.items():
        spec = module.spec
        payload[name] = {
            "type": spec.type,
            "source": spec.source.model_dump(mode="json", exclude_none=True),
            "release_name": spec.release_name,
            "kubernetes_namespace": spec.kubernetes_namespace,
            "properties": {
                "mounts": [m.model_dump(mode="json", exclude_none=True) for m in (spec.mounts or [])],
                "checks": [c.model_dump(mode="json", exclude_none=True) for c in (spec.checks or [])],
                "endpoints": [e.model_dump(mode="json", exclude_none=True) for e in (spec.endpoints or [])],
            },
            "configuration": spec.configuration or {},
            "custom": spec.custom or {},
            "default_labels": spec.default_labels or {},
            "custom_labels": spec.custom_labels or {},
        }
    return payload


def _strata_dns(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.dns` — name -> {description, labels, tags, provider,
    zones: {zone_name: {ttl, records: [...]}}}. Tokens stay literal
    (build-time only, ADR-0022 D4)."""
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


def _strata_networks(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.networks` — name -> {description, labels, tags, networks:
    {network_name: {address_space, subnets, peerings, configuration,
    custom, default_tags, custom_tags}}}. Tokens stay literal (build-time
    only, ADR-0022 D4)."""
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


def _strata_firewalls(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.firewalls` — name -> {description, labels, tags,
    rules: {reset, defaults, deny, allow}, configuration, custom,
    default_tags, custom_tags}. `by_alias=True` on the rule dumps so
    `from_` serialises back to `from` (the schema's real field name)."""
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


def _strata_topologies(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.topologies` — name -> {type, components, namespaces,
    volumes}, including `TopologySpecModel.namespaces` (a gap fix — that
    field was validated but never read at all before this redesign)."""
    payload: dict[str, Any] = {}
    for topology in graph.workspace.spec.topology or []:
        payload[topology.name] = {
            "type": topology.type,
            "components": [c.model_dump(mode="json", exclude_none=True) for c in topology.components],
            "namespaces": [n.namespace for n in (topology.namespaces or [])],
            "volumes": [v.model_dump(mode="json", exclude_none=True) for v in (topology.volumes or [])],
        }
    return payload


def _strata_flags(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.flags` — `{key: bool}`, resolved `constant`/`environment`-
    store entries only (unresolved integration-backed stores are filtered
    out, never emitted) — no longer its own flat Terraform variable, now
    `local.strata.flags.<key>`."""
    return {ref.key: ref.value for ref in graph.feature_refs if ref.value is not None}


def _strata_variables(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.variables` — `{key: value}`, same `constant`/`environment`-
    only filter as `_strata_flags()` above."""
    return {ref.key: ref.value for ref in graph.variable_refs if ref.value is not None}


def _strata_properties(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.properties` — the already-merged workspace -> environment(s)
    -> deployment dict, computed once by `build_controller.py`."""
    return graph.properties


def _strata_custom(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.custom` — same merge as `_strata_properties()` above,
    `custom` source instead of `properties`."""
    return graph.custom


def _strata_assets(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.assets` (docs/design/asset-management.md) — name -> {path,
    files}, resolved once by `build_controller.py` before the step loop
    (`ResolvedAssetInfo`). Unlike every other category, keyed only by
    assets at least one execution step actually references this build —
    see this function's own "Deliberate deviation" note in that doc."""
    return {name: {"path": info.path, "files": info.files} for name, info in graph.resolved_assets.items()}


def build_strata_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """The unified projection (ADR-0035, docs/design/
    unified-terraform-projection.md) — one nested object.
    `{"strata": build_strata_payload(graph)}` is the file content
    `default_output()` writes to `strata.auto.tfvars.json` — this function
    returns the *inner* dict, not the wrapped file content.

    **Every one of the sixteen keys below is always present**, empty
    (`{}`) when unused rather than omitted — resolves the Type design
    section's own open sub-question ("should the projection always emit
    every top-level key, which would let the generated `strata_variables.tf`
    type stay strict with no top-level `optional()`") in favor of always
    emitting: a workspace with no `dns_zones` gets `strata.dns == {}`, not
    a missing key.
    """
    return {
        "workspace": _strata_workspace(graph),
        "tenant": _strata_tenant(graph),
        "providers": _strata_providers(graph),
        "resources": _strata_resources(graph),
        "namespaces": _strata_namespaces(graph),
        "modules": _strata_modules(graph),
        "dns": _strata_dns(graph),
        "networks": _strata_networks(graph),
        "firewalls": _strata_firewalls(graph),
        "topologies": _strata_topologies(graph),
        "properties": _strata_properties(graph),
        "custom": _strata_custom(graph),
        "flags": _strata_flags(graph),
        "variables": _strata_variables(graph),
        "default_tags": _strata_default_tags(graph),
        "assets": _strata_assets(graph),
    }


def planned_strata_file(graph: ResolvedWorkspaceGraph) -> tuple[str, dict[str, Any]]:
    """Phase 4 (ADR-0035) — exactly one `(filename, data)` pair, every
    time, never a list. One `strata` key wraps `build_strata_payload(graph)`'s
    whole inner object — the file's real Terraform variable name is
    `strata`, matching the `variable "strata" {}` declaration
    `strata_variables.tf` (Phase 5) generates.
    """
    return "strata.auto.tfvars.json", {"strata": build_strata_payload(graph)}


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Navigate a dotted path (`resolve_value_tokens_tracking_secrets()`'s
    own convention — dict keys joined by `.`, each key escaped by
    `escape_path_segment()`, a list index its plain stringified integer)
    into `data` and overwrite the leaf it names. Always use `split_path()`
    to reverse the join, never a plain `path.split(".")` — a real key can
    itself contain a literal `.` (e.g. a DNS zone name like `example.com`),
    which a naive split would wrongly treat as two separate path segments.

    `data` must already contain every segment of `path` (true by
    construction for every caller here — the path was discovered by
    walking this exact structure in the first place), so no segment is
    ever created, only indexed into.
    """
    parts = split_path(path)
    node: Any = data
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def find_secret_paths(payload: dict[str, Any]) -> list[str]:
    """Phase 3 (ADR-0035) — every dotted path in a *build-time*
    (token-literal, unresolved) `strata` payload whose leaf is
    `${secret:...}`-shaped, in the exact path convention
    `resolve_value_tokens_tracking_secrets()` already uses (dict keys
    joined by `.`, a list index its plain stringified integer) — needed by
    Phase 6's `strata_locals.tf` generator, which must know every
    secret-bearing path at build time, before any real secret value
    exists to resolve.

    Deliberately its own standalone walk, not a call into
    `resolve_value_tokens_tracking_secrets(payload, {})` with an empty
    `values` dict — that function's non-secret branch calls
    `resolve_value_tokens()`, which raises `ValueError` the moment it
    meets a `${var:...}`/`${feature:...}` token with no matching key in
    `values`. A build-time payload is expected to carry exactly such
    still-unresolved tokens (ADR-0022 D4) — this function only ever
    checks a leaf's *shape*, never attempts to resolve anything, so it
    never needs real values to run.
    """
    paths: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            if any(kind == "secret" for kind, _ in extract_value_tokens(node)):
                paths.append(path)
            return
        if isinstance(node, dict):
            for key, value in node.items():
                escaped_key = escape_path_segment(key)
                _walk(value, f"{path}.{escaped_key}" if path else escaped_key)
            return
        if isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, f"{path}.{index}" if path else str(index))

    for key, value in payload.items():
        escaped_key = escape_path_segment(key)
        _walk(value, escaped_key)
    return paths


def rewrite_strata_payload(
    payload: dict[str, Any], tokens: dict[str, str], *, sensitive_output_keys: set[str] | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Phase 3 (ADR-0035) — the `deploy run`-time counterpart to
    `build_strata_payload()`: no category branching of any kind — one
    shape, one walk, because there is only one object now.

    Resolves every token in `payload` via
    `resolve_value_tokens_tracking_secrets()` exactly as today — every
    non-secret-shaped leaf resolves to its real value in place; every
    secret-shaped leaf is left as its original, still-unresolved literal
    by that function and reported separately in the returned `secrets`
    dict instead. This function's own addition on top of that: every path
    in `secrets` is then overwritten *in the resolved payload* with the
    sentinel string `f"__strata_secret__:{path}"` — never the literal
    unresolved token (which would leak the fact a value is missing, not
    what it is), never the real value (which must never touch disk,
    ADR-0031), and never omitted (an omitted key breaks every attribute
    access expecting it to exist — see this design's own "Secret
    delivery" section for why a stable shape matters more than it first
    appears).

    `sensitive_output_keys` passes straight through to `resolve_value_
    tokens_tracking_secrets()` — the caller's own collected-output
    sensitivity flags, keyed `"step.key"` exactly like an `${output:...}`
    token's own key half, so a `${output:}` reference to a real Terraform
    `sensitive = true` output is treated as secret-shaped too, not just a
    literal `${secret:}` token.

    Returns:
        `(sentinel_payload, secrets)` — `sentinel_payload` is written to
        `strata.auto.tfvars.json`; `secrets` is the flat `{path: value}`
        map delivered as `TF_VAR_strata_secrets` (never written to disk).
    """
    resolved, secrets = resolve_value_tokens_tracking_secrets(
        payload, tokens, sensitive_output_keys=sensitive_output_keys
    )
    for path in secrets:
        _set_path(resolved, path, f"__strata_secret__:{path}")
    return resolved, secrets


def _group_by_first_segment(paths: list[str]) -> dict[str, list[str]]:
    """Split every dotted path on its first segment — `{head: [rest, ...]}`,
    `rest` empty when `head` is itself the whole (leaf) path. Uses
    `split_path()` (not a naive `path.partition(".")`) so a real key
    containing a literal `.` (e.g. a DNS zone name like `example.com`)
    splits correctly; `rest` is re-escaped via `escape_path_segment()`
    before being rejoined, so it remains a valid path string for a
    recursive call back into this same function. Used by
    `_render_strata_merge()` to decide, per key, whether to emit a
    `lookup()` leaf or recurse into a nested `merge()`.
    """
    groups: dict[str, list[str]] = {}
    for path in paths:
        parts = split_path(path)
        head = parts[0]
        rest = ".".join(escape_path_segment(part) for part in parts[1:])
        groups.setdefault(head, []).append(rest)
    return groups


def _is_list_level(groups: dict[str, list[str]]) -> bool:
    """True when every key `_group_by_first_segment()` found at this
    nesting level is a list index (all-digit), not a dict key — found via
    a real-terraform regression (a secret inside a DNS record's `value`,
    `records` being a list): `merge()` rejects a list/tuple argument
    outright (`arguments must be maps or objects, got "tuple"`), so a list
    level needs `_render_strata_list_override()`'s `for`-expression
    rebuild instead of `_render_strata_merge()`'s `merge()` call.
    """
    return bool(groups) and all(key.isdigit() for key in groups)


def _render_strata_override(var_expr: str, full_path: str, paths: list[str], indent: str) -> str:
    """Dispatch to `_render_strata_merge()` (dict level) or
    `_render_strata_list_override()` (list level) — the two are mutually
    exclusive shapes at any given nesting level (a dict can't have a
    digit-only key, and a list has nothing else), so inspecting
    `_group_by_first_segment(paths)` once up front is enough to pick.
    """
    if _is_list_level(_group_by_first_segment(paths)):
        return _render_strata_list_override(var_expr, full_path, paths, indent)
    return _render_strata_merge(var_expr, full_path, paths, indent)


def _quote_full_path(full_path: str) -> str:
    """Quote a dotted path for embedding as an HCL string literal naming
    the `var.strata_secrets` lookup key. Doubles each literal backslash
    (our own `escape_path_segment()` convention for a literal `.` inside
    a real key) so HCL's own string parser decodes it back to exactly one
    backslash at runtime, matching the real key Terraform populates
    `var.strata_secrets` with — **not** `json.dumps()` on top of that
    doubling, which would escape the already-doubled backslashes a
    second time and produce a lookup key that can never match the real
    one (found via a real-terraform test once `strata_locals.tf` was
    actually wired into a real `build run`, not caught by any unit test
    since those only ever compared generated *text*, never decoded it the
    way HCL itself would).
    """
    return '"' + full_path.replace("\\", "\\\\") + '"'


def _render_strata_list_override(var_expr: str, full_path: str, paths: list[str], indent: str) -> str:
    """Render a `[for idx, item in <var_expr> : idx == N ? ... : item]`
    rebuild of a list, replacing exactly the indices that lead to a
    secret-bearing leaf — `merge()`'s list/map sibling to
    `_render_strata_merge()`, needed because `merge()` itself cannot
    partially override a list/tuple at all (Terraform rejects it
    outright), unlike a map.
    """
    branches = []
    for key, children in _group_by_first_segment(paths).items():
        index = int(key)
        escaped_key = escape_path_segment(key)
        child_full_path = f"{full_path}.{escaped_key}" if full_path else escaped_key
        child_var_expr = f"{var_expr}[{index}]"
        if children == [""]:
            replacement = f"lookup(var.strata_secrets, {_quote_full_path(child_full_path)}, {child_var_expr})"
        else:
            replacement = _render_strata_override(child_var_expr, child_full_path, children, indent + "  ")
        branches.append(f"idx == {index} ? {replacement} :")
    return f"[for idx, item in {var_expr} : {' '.join(branches)} item]"


def _render_strata_merge(var_expr: str, full_path: str, paths: list[str], indent: str) -> str:
    """Render one `merge(<var_expr>, {...})` call overriding exactly the
    keys that lead to a secret-bearing leaf — recursing per nesting level,
    never touching a sibling key that has no secret beneath it.

    `var_expr` is the real `var.strata.foo.bar`-style access expression for
    this level (the fallback — "sentinel, safe, key always exists" per the
    design doc's own comment); `full_path` is the dotted path consumed so
    far, used both as the next level's `var_expr` suffix and as the literal
    key into `var.strata_secrets`.

    Every key is emitted via `json.dumps()` — both as an object
    constructor's quoted key (`"key" = value`, valid HCL for ANY string,
    unlike a bare identifier) and as a bracket-indexed attribute access
    (`var_expr["key"]`, unlike dot access, which splits on a literal `.`
    the same way our own path convention would without escaping) — a real
    key can contain characters a bare HCL identifier can't (a DNS zone
    name like `example.com`, a hyphenated name like `public-dns`), and
    JSON's string-escaping rules are a safe enough subset of HCL's own for
    every real key this generates against.

    Recurses into `_render_strata_override()`, not directly back into
    itself — a key's own children can turn out to be a list level one
    step further down (e.g. a DNS zone's `records`), which needs
    `_render_strata_list_override()` instead.
    """
    inner = indent + "  "
    lines = [f"merge({var_expr}, {{"]
    for key, children in _group_by_first_segment(paths).items():
        quoted_key = json.dumps(key)
        escaped_key = escape_path_segment(key)
        child_full_path = f"{full_path}.{escaped_key}" if full_path else escaped_key
        child_var_expr = f"{var_expr}[{quoted_key}]"
        if children == [""]:
            # HCL double-quoted strings only recognize a small fixed set of
            # `\x` escapes — `\.` (our own path-escaping convention for a
            # literal `.` inside a real key) is not one of them, so
            # `_quote_full_path()` doubles the literal backslash itself
            # before embedding, letting HCL's own string parser decode it
            # back to the literal 2-character `\.` our path convention
            # expects.
            lines.append(
                f"{inner}{quoted_key} = lookup(var.strata_secrets, {_quote_full_path(child_full_path)}, "
                f"{child_var_expr})"
            )
        else:
            nested = _render_strata_override(child_var_expr, child_full_path, children, inner)
            lines.append(f"{inner}{quoted_key} = {nested}")
    lines.append(f"{indent}}})")
    return "\n".join(lines)


def build_strata_locals(secret_paths: list[str]) -> str:
    """Phase 6 (ADR-0035) — generates `strata_locals.tf`'s full text: a
    `local.strata` that is `var.strata` with every secret-bearing leaf
    replaced by its real value from `var.strata_secrets`, so a consumer
    reads `local.strata.*` identically whether or not a given value is a
    secret (this design's own "Secret delivery" section).

    `secret_paths` is `find_secret_paths()`'s own output (dotted, same
    path convention `resolve_value_tokens_tracking_secrets()` uses) — a
    build-time-only input: every secret-bearing path is already known from
    the literal `${secret:...}` tokens themselves, long before any real
    value exists to resolve.

    No third-party HCL-writing library — a plain template/string build,
    matching this module's existing "functions returning strings/dicts"
    convention; nothing here needs HCL AST manipulation.
    """
    header = (
        "# GENERATED BY STRATA - DO NOT EDIT\n"
        "# local.strata is var.strata with every secret-bearing leaf replaced by\n"
        "# its resolved value from var.strata_secrets. Read local.strata.* -\n"
        "# never var.strata.* - and secrets are simply never a special case.\n"
    )
    if not secret_paths:
        return header + "\nlocals {\n  strata = var.strata\n}\n"
    merge_expr = _render_strata_override("var.strata", "", secret_paths, "  ")
    return header + "\nlocals {\n  strata = " + merge_expr + "\n}\n"
