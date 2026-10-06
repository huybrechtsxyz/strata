#!/usr/bin/env python3
"""Cross-document semantic checks — the seven cross-checks that existed but
were never called.

Reference *existence* (`strata.controllers.references`) answers "does the
name point at something real?". This module answers the harder question for
each pair of documents that already resolve to each other: is the *content*
consistent — right region for that provider, right component roles for that
topology type, tokens that actually resolve, stages that name real steps?

Each check already existed as a tested method on its service — built while
adding the corresponding kind, since a document naming another one invites
the question immediately. None of them were ever called from anywhere: each
needs the *other* document already loaded to check against, which is
precisely the resolution pass this module is.

Every check here uses `BaseService.from_model()` to wrap an already-validated
model without re-running Phase 1 — the model came out of the index, which
means it already passed schema validation. `IndexEntry.model` is typed as the
base `PlatformBaseModel` (the index holds every kind), so each helper below
casts to the concrete type its own kind guarantees — the index's own identity
(`DocumentRef.kind`) is what backs that guarantee.

Every lookup uses `index.get()`, so a reference this checks is one reference
existence already vetted; an unresolved reference produces no duplicate
finding here — `validate_references` already reported it.
"""

from pathlib import Path
from typing import cast

from strata.controllers.solution_controller import DocumentIndex, IndexEntry
from strata.controllers.value_references import resolve_document_value_references
from strata.models.common_models import PlatformBaseModel, PlatformKind, SourceModel
from strata.models.configuration_model import ConfigurationModel, ConfigurationSecurityModel, PathConventionModel
from strata.models.deployment_model import DeploymentModel
from strata.models.environment_model import EnvironmentModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.solution_model import RemoteType, SolutionModel
from strata.models.tenant_model import TenantModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.version_model import VersionModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.deployment_service import DeploymentService
from strata.services.environment_service import EnvironmentService, unresolved_value_tokens
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.tenant_service import TenantService
from strata.services.workspace_service import WorkspaceService
from strata.utils.diagnostics import Diagnostics
from strata.utils.path_conventions import in_scope, match_directory, match_filename, matches_segment_pattern


def run_semantic_checks(
    index: DocumentIndex,
    resolved_deployments: dict[str, DeploymentModel] | None = None,
    solution: SolutionModel | None = None,
    root: Path | None = None,
) -> Diagnostics:
    """Run every cross-document semantic check over an already-loaded index.

    Args:
        index: The loaded `DocumentIndex`.
        resolved_deployments: Deployments with their `extends` chain already
            folded in (`deployment_resolution.resolve_deployment_chains`).
            When a deployment's name is present here, its resolved (complete)
            spec is checked instead of the raw indexed one — otherwise a
            deployment that gets `workspace`/`environments` only through
            `extends` would silently skip these checks, since the raw model
            never has them.
        solution: The manifest, for `spec.remotes` (`_check_remotes()`, gap
            #3). Its own kind is never indexed (`SolutionController._load_manifest()`'s
            own docstring), so this is the only cross-document check here
            that cannot get its second document from `index` alone — same
            reason `check_version_pins()` also takes `solution` directly.
        root: The solution root, for `_check_paths()`/the `DeploymentLayersModel`
            cross-check (docs/design/path-conventions.md) — needed to turn
            each `IndexEntry.source` (absolute) into a path relative to the
            solution, which is what a `Configuration.spec.paths` convention's
            `pattern` is written against. `None` (the default) skips both
            checks entirely — same "nothing to check against" rule every
            other Configuration-backed check here already uses.

    Returns:
        Every finding, from all eleven checks combined.
    """
    resolved = resolved_deployments or {}
    diagnostics = Diagnostics()
    diagnostics.extend(_check_deployments(index, resolved, root))
    diagnostics.extend(_check_tenants(index))
    diagnostics.extend(_check_providers(index))
    diagnostics.extend(_check_resources(index))
    diagnostics.extend(_check_workspaces(index))
    diagnostics.extend(_check_environments(index))
    diagnostics.extend(_check_deployment_value_tokens(index, resolved))
    diagnostics.extend(_check_remotes(index, solution))
    diagnostics.extend(_check_value_references(index))
    diagnostics.extend(_check_paths(index, root))
    diagnostics.extend(_check_version_workspace(index, resolved))
    return diagnostics


# ---------------------------------------------------------------------------
# Deployment -> Workspace: stages name real execution steps
# ---------------------------------------------------------------------------


def _check_deployments(
    index: DocumentIndex, resolved: dict[str, DeploymentModel], root: Path | None = None
) -> Diagnostics:
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = resolved.get(entry.ref.name, cast(DeploymentModel, entry.model))
        if deployment.spec.workspace:
            workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
            if workspace_entry is not None:
                service = DeploymentService.from_model(deployment)
                workspace = cast(WorkspaceModel, workspace_entry.model)
                diagnostics.extend(service.validate_stages_against_workspace(workspace), source=str(entry.source))
        diagnostics.extend(_check_deployment_layers(index, entry, deployment, root), source=str(entry.source))
        diagnostics.extend(_check_deployment_tenant_geography(index, deployment), source=str(entry.source))
    return diagnostics


def _single_configuration(index: DocumentIndex) -> ConfigurationModel | None:
    """The one `Configuration` document, or `None` when absent or ambiguous.

    Shared by `_check_paths()` and `_check_deployment_layers()` — both need
    "the one declared source of truth, or nothing to check against at all"
    (ADR-0003's already-known "ambiguous merging not implemented" case for
    more than one), the same rule `_check_workspace_topology_components()`
    already applies inline for `spec.topologies`.
    """
    entries = index.all_of(PlatformKind.CONFIGURATION)
    if len(entries) != 1:
        return None
    return cast(ConfigurationModel, entries[0].model)


def _check_deployment_layers(
    index: DocumentIndex, entry: IndexEntry, deployment: DeploymentModel, root: Path | None
) -> Diagnostics:
    """Cross-check `Deployment.spec.layers` against the deployment document's
    own real file path (docs/design/path-conventions.md Phase 3) — the check
    that finally makes `DeploymentLayersModel` (previously "inert in v2")
    mean something. Validation only, never mutates `segments` — auto-
    derivation when absent is deliberately out of scope (no consumer of
    `layers` exists yet to feed).
    """
    diagnostics = Diagnostics()
    layers = deployment.spec.layers
    if layers is None or layers.follows is None or root is None:
        return diagnostics

    configuration = _single_configuration(index)
    if configuration is None:
        return diagnostics  # nothing declared, nothing to check against (Open Question #2, resolved)

    conventions = {c.name: c for c in (configuration.spec.paths or [])}
    convention = conventions.get(layers.follows)
    if convention is None:
        diagnostics.error(
            f"Deployment '{deployment.meta.name}': layers.follows names unknown path convention "
            f"'{layers.follows}'. Available: {sorted(conventions)}",
            code="unknown_path_convention",
        )
        return diagnostics

    if not layers.segments:
        return diagnostics  # nothing declared yet to cross-check

    relative = entry.source.resolve().relative_to(root.resolve()).as_posix()
    match = match_directory(relative, convention.pattern)
    if match is None:
        return diagnostics  # _check_paths() already reports this structural mismatch itself

    captures = match.groupdict()
    severity = diagnostics.error if convention.enforcement == "deny" else diagnostics.warning
    for name, declared_value in layers.segments.items():
        captured_value = captures.get(name)
        if captured_value is not None and captured_value != declared_value:
            severity(
                f"Deployment '{deployment.meta.name}': layers.segments['{name}'] = '{declared_value}', but "
                f"its real file path resolves '{name}' = '{captured_value}'.",
                code="path_convention_layers_drift",
            )
    return diagnostics


def _check_deployment_tenant_geography(index: DocumentIndex, deployment: DeploymentModel) -> Diagnostics:
    """Cross-check a deployment's provider region(s) against its tenant's allowed
    geographies (docs/design/tenant-zone-policy.md's "static tier" — the
    cheap, config-time equivalent of v1's real, plan-time `tenant_zone` policy).

    Fully automatic — no config field anywhere turns this on or off; it
    activates purely from data that already exists (`deployment.spec.tenant`/
    `.workspace`, each provider's resolved region), and skips silently
    wherever any link in that chain is genuinely absent (see the design doc's
    scenario matrix) rather than treating an absence as a violation. A
    provider whose region isn't found in its `ProviderConfig` at all, or whose
    matching region entry has no `geography` tag, is skipped the same way —
    both are either already reported elsewhere (`_check_providers()`) or
    structurally unknowable, never duplicated or guessed at here.

    Every provider on the workspace is checked independently — never
    short-circuits on the first violation, matching `_check_deployment_layers()`'s
    own "report every mismatch" behavior. Severity is a fixed `Severity.ERROR`
    (no `warn` option): there is no field anywhere to hold that choice, and
    data residency is a governance boundary that should fail closed, matching
    the one real production config's own `enforcement: deny` choice for this
    exact policy.
    """
    diagnostics = Diagnostics()
    if deployment.spec.tenant is None:
        return diagnostics
    tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
    if tenant_entry is None:
        return diagnostics  # reference existence already reports this
    tenant = cast(TenantModel, tenant_entry.model)
    if not tenant.spec.geographies:
        return diagnostics

    if deployment.spec.workspace is None:
        return diagnostics
    workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
    if workspace_entry is None:
        return diagnostics
    workspace = cast(WorkspaceModel, workspace_entry.model)
    if not workspace.spec.providers:
        return diagnostics

    allowed_geographies = set(tenant.spec.geographies)

    for provider_name in workspace.spec.providers:
        provider_entry = index.get(PlatformKind.PROVIDER, provider_name)
        if provider_entry is None:
            continue  # reference existence already reports this
        provider = cast(ProviderModel, provider_entry.model)

        config_entry = index.get(PlatformKind.PROVIDERCONFIG, provider.spec.properties.type)
        if config_entry is None:
            continue  # unregistered type — _check_providers()'s own existing skip
        provider_config = cast(ProviderConfigModel, config_entry.model)

        region = next(
            (r for r in (provider_config.spec.regions or []) if r.name == provider.spec.properties.region), None
        )
        if region is None or region.geography is None:
            continue  # invalid region (already reported by _check_providers()) or no geography tag declared

        if region.geography not in allowed_geographies:
            diagnostics.error(
                f"Deployment '{deployment.meta.name}': provider '{provider_name}' is in region "
                f"'{provider.spec.properties.region}' (geography '{region.geography}'), which is not in "
                f"tenant '{tenant.meta.name}''s allowed geographies: {sorted(allowed_geographies)}",
                code="tenant_geography_mismatch",
            )
    return diagnostics


# ---------------------------------------------------------------------------
# Deployment <-> Version: workspace exclusivity (docs/work/version-lifecycle.md
# Phase 1, docs/work/promotion.md's blast-radius anchor)
# ---------------------------------------------------------------------------


def _check_version_workspace(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """Every `Deployment` referencing a `Version` document (via `spec.version`)
    must agree on which `Workspace` that document applies to.

    `Version.spec.workspace` is a *conditionally* meaningful cross-document
    constraint, not a plain existence reference — like `VariableStoreModel.
    value` and `DeploymentLayersModel.segments` before it, it needs a
    dedicated check rather than a `References()` annotation (ADR-0015's own
    "conditional reference" carve-out).

    A declared `spec.workspace` on the `Version` document is authoritative:
    any referencing deployment naming a different workspace is an error.
    When `spec.workspace` is unset, the first referencing deployment (in
    index order) establishes the expectation for every other one — this
    never mutates the `Version` document itself, it is validation only, the
    same restraint `_check_deployment_layers()` applies to `segments`.

    A `Version` document with no referrers, or referrers with no
    `spec.workspace` of their own, produces no finding — nothing to check
    against, same "skip rather than guess" rule every other
    conditionally-meaningful check here already follows.
    """
    diagnostics = Diagnostics()
    referrers_by_version: dict[str, list[IndexEntry]] = {}
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = resolved.get(entry.ref.name, cast(DeploymentModel, entry.model))
        if deployment.spec.version:
            referrers_by_version.setdefault(deployment.spec.version, []).append(entry)

    for entry in index.all_of(PlatformKind.VERSION):
        version = cast(VersionModel, entry.model)
        expected = version.spec.workspace
        for referrer_entry in referrers_by_version.get(entry.ref.name, []):
            deployment = resolved.get(referrer_entry.ref.name, cast(DeploymentModel, referrer_entry.model))
            if deployment.spec.workspace is None:
                continue
            if expected is None:
                expected = deployment.spec.workspace
                continue
            if deployment.spec.workspace != expected:
                diagnostics.error(
                    f"Deployment '{deployment.meta.name}' references Version '{entry.ref.name}' with "
                    f"workspace '{deployment.spec.workspace}', but workspace '{expected}' was already "
                    "established for this Version document (either declared on it directly, or by "
                    "another referencing deployment).",
                    source=str(referrer_entry.source),
                    location="spec.workspace",
                    code="version_workspace_mismatch",
                )
    return diagnostics


# ---------------------------------------------------------------------------
# Tenant -> ProviderConfig: declared geographies are real
# ---------------------------------------------------------------------------


def _check_tenants(index: DocumentIndex) -> Diagnostics:
    diagnostics = Diagnostics()
    provider_configs: dict[str, ProviderConfigModel] = {
        entry.ref.name: cast(ProviderConfigModel, entry.model) for entry in index.all_of(PlatformKind.PROVIDERCONFIG)
    }
    for entry in index.all_of(PlatformKind.TENANT):
        tenant = cast(TenantModel, entry.model)
        service = TenantService.from_model(tenant)
        diagnostics.extend(
            service.validate_geographies_against_provider_configs(provider_configs), source=str(entry.source)
        )
    return diagnostics


# ---------------------------------------------------------------------------
# Provider / Resource -> ProviderConfig: region and resource-type/schema fit
# ---------------------------------------------------------------------------


def _check_providers(index: DocumentIndex) -> Diagnostics:
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.PROVIDER):
        provider = cast(ProviderModel, entry.model)
        config_entry = index.get(PlatformKind.PROVIDERCONFIG, provider.spec.properties.type)
        if config_entry is None:
            continue  # unregistered type is a schema-registry policy question, not existence
        service = ProviderService.from_model(provider)
        config = cast(ProviderConfigModel, config_entry.model)
        diagnostics.extend(service.validate_against_provider_config(config), source=str(entry.source))
    return diagnostics


def _check_resources(index: DocumentIndex) -> Diagnostics:
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.RESOURCE):
        resource = cast(ResourceModel, entry.model)
        config_entry = index.get(PlatformKind.PROVIDERCONFIG, resource.spec.properties.provider_type)
        if config_entry is None:
            continue
        service = ResourceService.from_model(resource)
        config = cast(ProviderConfigModel, config_entry.model)
        diagnostics.extend(service.validate_against_provider_config(config), source=str(entry.source))
    return diagnostics


# ---------------------------------------------------------------------------
# Workspace -> Topology (+ TopologyConfig): internal refs and component roles
# ---------------------------------------------------------------------------


def _check_workspaces(index: DocumentIndex) -> Diagnostics:
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.WORKSPACE):
        workspace = cast(WorkspaceModel, entry.model)
        if workspace.spec.topology:
            diagnostics.extend(_check_workspace_topology_components(index, workspace), source=str(entry.source))

        diagnostics.extend(_check_workspace_subnets(index, workspace), source=str(entry.source))

    return diagnostics


def _check_workspace_topology_components(index: DocumentIndex, workspace: WorkspaceModel) -> Diagnostics:
    """The registry-backed half of the workspace/topology check.

    Needs `spec.topologies` from the loaded Configuration registry —
    `validate_topology_components`'s signature requires it (no Optional
    fallback), so this is skipped entirely when no Configuration document
    exists, rather than guessing a policy that was never declared. Topology
    itself is inline on `workspace.spec.topology` now (ADR-0028) — its own
    internal reference checks already ran at Phase 1 (model validators on
    `WorkspaceSpecModel`), so this is only the registry cross-check.
    """
    configuration_entries = index.all_of(PlatformKind.CONFIGURATION)
    if len(configuration_entries) != 1:
        return Diagnostics()  # none, or ambiguous merging not implemented (ADR-0003)
    configuration = cast(ConfigurationModel, configuration_entries[0].model)

    topology_config_models: dict[str, TopologyConfigModel] = {}
    for name in configuration.spec.topologies or []:
        found = index.get(PlatformKind.TOPOLOGYCONFIG, name)
        if found is not None:
            config = cast(TopologyConfigModel, found.model)
            topology_config_models[config.meta.name] = config

    service = WorkspaceService.from_model(workspace)
    return service.validate_topology_components(configuration, topology_config_models)


# ---------------------------------------------------------------------------
# Workspace -> Network: resource subnet references name a real subnet
# ---------------------------------------------------------------------------


def _check_workspace_subnets(index: DocumentIndex, workspace: WorkspaceModel) -> Diagnostics:
    """The registry-backed half of the workspace/subnet check
    (docs/work/solution-loading-and-phase2-validation.md).

    `WorkspaceResourceSubnetModel.network` (which Network *document*) is
    already checked in Phase 1 (`validate_resource_subnet_references`)
    against this workspace's own `spec.networks`. This is only the Phase 2
    half: once that document is actually loaded, does its real
    `NetworkDefinitionModel.subnets[]` contain the named subnet? Resolves
    every network name this workspace's resources reference, so a resource
    with no `subnet` set (the common case) costs one empty-dict lookup,
    never a loaded-but-unused document.

    An unresolved `.network` name produces no duplicate finding here —
    `validate_references` already reported it (module docstring).
    """
    network_names = {r.subnet.network for r in (workspace.spec.resources or []) if r.subnet is not None}
    if not network_names:
        return Diagnostics()

    network_models: dict[str, NetworkModel] = {}
    for name in network_names:
        found = index.get(PlatformKind.NETWORK, name)
        if found is not None:
            network_models[name] = cast(NetworkModel, found.model)

    service = WorkspaceService.from_model(workspace)
    return service.validate_resource_subnets(network_models)


# ---------------------------------------------------------------------------
# Environment -> Artifact: store: artifact variables reference a real artifact
# ---------------------------------------------------------------------------


def _check_environments(index: DocumentIndex) -> Diagnostics:
    """Every `store: artifact` variable's `value` names a real `ArtifactModel`
    (docs/work/artifact-references.md's full-review finding, 2026-09-27):
    `VariableStoreModel.value: Any` is only conditionally an artifact
    reference, so `references.py`'s generic `References()` walker never
    checks it — this is that missing check, following the exact
    `WorkspaceService.validate_topology_references()` precedent for a
    conditionally-meaningful field.

    Also enforces `Configuration.spec.security`'s store allow-lists
    (docs/design/gap_fit_v1.md gap #7), when exactly one `Configuration` document
    exists and declares it — same "skip rather than guess a policy that was
    never declared" rule `_check_workspace_topology_components()` already
    uses for `spec.topologies`.
    """
    diagnostics = Diagnostics()
    artifact_names = set(index.names_of(PlatformKind.ARTIFACT))

    security: ConfigurationSecurityModel | None = None
    configuration_entries = index.all_of(PlatformKind.CONFIGURATION)
    if len(configuration_entries) == 1:
        configuration = cast(ConfigurationModel, configuration_entries[0].model)
        security = configuration.spec.security

    for entry in index.all_of(PlatformKind.ENVIRONMENT):
        environment = cast(EnvironmentModel, entry.model)
        service = EnvironmentService.from_model(environment)
        diagnostics.extend(service.validate_artifact_references(artifact_names), source=str(entry.source))
        if security is not None:
            diagnostics.extend(service.validate_allowed_stores(security), source=str(entry.source))
    return diagnostics


# ---------------------------------------------------------------------------
# Deployment -> Environment/Tenant reachability: value tokens resolve
# ---------------------------------------------------------------------------


def _check_deployment_value_tokens(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """Every token in a document reachable from a deployment resolves.

    Scoped per deployment because that is the only place declared keys and
    token-bearing documents actually meet: an Environment declares keys, a
    Deployment names which Environments apply (merged with its Tenant's, per
    `TenantSpecModel.environments`'s own description — tenant merges in
    first), and *its* Workspace is what reaches the dns/network/firewall/
    module/resource/provider/topology documents that might use them.
    Checking module tokens against an unrelated environment would be either
    a false positive (flags a token satisfied by whichever environment
    actually deploys it) or a false negative (passes against an environment
    that never applies) — scoping by the real reachability graph is the
    only version that can't be wrong in either direction.

    **Also checks the deployment/tenant/environment/workspace/resource/
    provider/topology documents themselves** — docs/design/gap_fit_v1.md gap #10's
    Phase 7 finding: this function used to only check DNS/network/
    firewall/module (`_documents_reachable_from_workspace()`'s original,
    narrower scope), so a `${var:}`/`${secret:}` token in, say,
    `ResourceSpecModel.configuration` or `WorkspaceSpecModel.custom` was
    never checked at all — not a "resolves at deploy time or not" question
    (gap #9/Phase 6's own concern), a "does `strata validate` even look at
    this field" one. Verified real: gap #9 Phase 6 delivers exactly these
    fields at deploy time now, but nothing validated their tokens before
    that ever ran.
    """
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = resolved.get(entry.ref.name, cast(DeploymentModel, entry.model))
        declared = _merged_declared_keys(index, deployment)
        if declared is None:
            continue  # no resolvable environment — nothing to check tokens against

        owner = "+".join(deployment.spec.environments or []) or "(none)"
        documents: list[tuple[PlatformBaseModel, bool]] = _documents_reachable_from_workspace(
            index, deployment.spec.workspace
        )
        documents.append((deployment, False))
        if deployment.spec.tenant:
            tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
            if tenant_entry is not None:
                documents.append((tenant_entry.model, False))
        for name in _reachable_environment_names(index, deployment):
            env_entry = index.get(PlatformKind.ENVIRONMENT, name)
            if env_entry is not None:
                documents.append((env_entry.model, False))

        for document, claimed in documents:
            diagnostics.extend(
                unresolved_value_tokens(document, declared, owner, output_claimed=claimed), source=str(entry.source)
            )
    return diagnostics


def _reachable_environment_names(index: DocumentIndex, deployment: DeploymentModel) -> list[str]:
    """Every Environment name this deployment resolves — its Tenant's own
    `spec.environments` first (tenant merges in before deployment), then
    the deployment's own. Shared by `_merged_declared_keys()` (which only
    needs the union of *keys*) and `_check_deployment_value_tokens()`
    (which also needs each environment's own *document*, gap #10's Phase 7
    finding, to check tokens written directly in an Environment's own
    `properties`/`custom`).
    """
    names = list(deployment.spec.environments or [])
    if deployment.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
        if tenant_entry is not None:
            tenant = cast(TenantModel, tenant_entry.model)
            names = list(tenant.spec.environments or []) + names
    return names


def _merged_declared_keys(index: DocumentIndex, deployment: DeploymentModel) -> dict[str, set[str]] | None:
    """Union declared keys across every Environment this deployment resolves.

    Existence-checking only needs the union: a token is fine if *any*
    resolved environment declares it.
    """
    merged: dict[str, set[str]] = {"var": set(), "secret": set(), "feature": set()}
    found_any = False
    for name in _reachable_environment_names(index, deployment):
        env_entry = index.get(PlatformKind.ENVIRONMENT, name)
        if env_entry is None:
            continue
        found_any = True
        environment = cast(EnvironmentModel, env_entry.model)
        declared = EnvironmentService.from_model(environment).declared_keys()
        for kind, keys in declared.items():
            merged[kind] |= keys

    return merged if found_any else None


def _documents_reachable_from_workspace(
    index: DocumentIndex, workspace_name: str | None
) -> list[tuple[PlatformBaseModel, bool]]:
    """Every document a workspace renders or configures — DNS/Network/
    Firewall/Module (rendered artifacts) plus the workspace document itself
    and every Resource/Provider/Topology it references (config passthrough
    fields: docs/design/gap_fit_v1.md gap #10's Phase 7 finding — these were never
    checked at all before, a validate-time coverage gap entirely separate
    from gap #9/Phase 6's deploy-time delivery fix).

    Returns `(document, claimed)` pairs — `claimed` is whether some
    workspace execution step's `targets` claims this document
    (`WorkspaceService.claimed_document_names()`, gap #12 generalized to
    namespaces, docs/design/deploy-command.md's "Cross-invocation output
    access" section) — `unresolved_value_tokens()` accepts an
    `${output:...}` token only when `claimed` is True. A module reached
    through a targeted namespace inherits that namespace's claim; every
    other document here (workspace/resource/provider/topology, and a
    topology-attached module) is never claimable today, so `claimed` is
    unconditionally False for those.

    A bounded, one-then-two-hop walk — not a generic graph traversal —
    since the schema only has one indirection beyond the workspace's own
    direct references: a workspace names Topology/Namespace documents
    directly, and those in turn attach Modules. The workspace document
    itself is included whole (not just its `configuration`/`custom`/
    `properties` fields individually) — `unresolved_value_tokens()` already
    walks a document's entire serialized form generically, so this covers
    `WorkspaceResourceModel.configuration` (the workspace's own per-resource
    override) for free, without a separate fetch.
    """
    if not workspace_name:
        return []
    workspace_entry = index.get(PlatformKind.WORKSPACE, workspace_name)
    if workspace_entry is None:
        return []
    workspace = cast(WorkspaceModel, workspace_entry.model)
    spec = workspace.spec
    claimed_names = WorkspaceService.from_model(workspace).claimed_document_names()

    documents: list[tuple[PlatformBaseModel, bool]] = [(workspace, False)]

    for kind, names in (
        (PlatformKind.DNS, spec.dns_zones),
        (PlatformKind.NETWORK, spec.networks),
        (PlatformKind.FIREWALL, spec.firewalls),
        (PlatformKind.PROVIDER, spec.providers),
    ):
        for name in names or []:
            found = index.get(kind, name)
            if found is not None:
                documents.append((found.model, name in claimed_names))

    for workspace_resource in spec.resources or []:
        if workspace_resource.resource is None:
            continue
        resource_entry = index.get(PlatformKind.RESOURCE, workspace_resource.resource)
        if resource_entry is not None:
            documents.append((resource_entry.model, False))

    for topo in spec.topology or []:
        for component in topo.components:
            for module_ref in component.modules or []:
                module_entry = index.get(PlatformKind.MODULE, module_ref.module)
                if module_entry is not None:
                    documents.append((module_entry.model, False))

    for name in spec.namespaces or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        namespace_claimed = name in claimed_names
        for module_ref in namespace.spec.modules or []:
            module_entry = index.get(PlatformKind.MODULE, module_ref.module)
            if module_entry is not None:
                documents.append((module_entry.model, namespace_claimed))

    return documents


# ---------------------------------------------------------------------------
# SolutionRemoteModel -> Module/Provisioner SourceModel usages: an OCI
# remote's `reference` is required only when a real (non-chart) consumer
# needs it (docs/design/gap_fit_v1.md gap #3).
# ---------------------------------------------------------------------------


def _check_remotes(index: DocumentIndex, solution: SolutionModel | None) -> Diagnostics:
    """Every `type: oci` remote with no `reference` must have no non-chart consumer.

    `SolutionRemoteModel.validate_reference_for_type()` (Phase 1) can only
    enforce this half of the rule once it knows how the remote is actually
    used, which needs the loaded solution — so Phase 1 leaves `reference`
    merely optional for every OCI remote, and this is the check that makes
    "optional unless a real consumer needs it" actually true. A chart-based
    consumer (`SourceModel.chart_name` set) never needs it — a chart index
    legitimately serves many versions, and the real pin is
    `SourceModel.chart_version` instead. A git-based consumer
    (`SourceModel.source_path` set) does — an OCI remote serving a real
    artifact IS one immutable tree, same as a git remote, and `reference`
    is how that tree is pinned.

    An OCI remote with `reference` already set, or with zero consumers at
    all, produces no finding either way — an unused remote is a separate,
    still-unbuilt gap (see docs/design/gap_fit_v1.md), not this check's concern.
    """
    diagnostics = Diagnostics()
    if solution is None:
        return diagnostics

    remotes_needing_check = {
        remote.name: remote
        for remote in (solution.spec.remotes or [])
        if remote.type is RemoteType.OCI and remote.reference is None
    }
    if not remotes_needing_check:
        return diagnostics

    sources: list[tuple[SourceModel, str]] = []
    for entry in index.all_of(PlatformKind.MODULE):
        module = cast(ModuleModel, entry.model)
        sources.append((module.spec.source, str(entry.source)))
    for entry in index.all_of(PlatformKind.WORKSPACE):
        workspace = cast(WorkspaceModel, entry.model)
        for provisioner in workspace.spec.provisioners:
            if provisioner.source is not None:
                sources.append((provisioner.source, str(entry.source)))

    for source, doc_source in sources:
        if source.remote is None or source.chart_name is not None:
            continue  # not naming a remote, or a chart-based consumer (no reference needed)
        remote = remotes_needing_check.get(source.remote)
        if remote is None:
            continue  # not one of the OCI-and-missing-reference remotes
        diagnostics.error(
            f"Remote '{remote.name}' (type oci) has no 'reference', but is used by a non-chart "
            f"source (source_path '{source.source_path}') — a real artifact needs an immutable "
            "ref/digest pinned on the remote, the same way a git remote does (chart-based consumers "
            "are the only case that can omit it, since their pin is SourceModel.chart_version instead).",
            source=doc_source,
            location="spec.source.remote",
            code="oci_remote_missing_reference",
        )
    return diagnostics


# ---------------------------------------------------------------------------
# ${value:kind.name.path} cross-document references (docs/design/
# cross-document-value-references.md's Phase 5).
# ---------------------------------------------------------------------------


def _check_value_references(index: DocumentIndex) -> Diagnostics:
    """Every `${value:kind.name.path}` token anywhere in the solution
    resolves to a real, literal, scalar field.

    Solution-wide, like `_check_remotes()` above — not deployment-scoped
    like most other checks in this module, since a `${value:...}` token
    names its own target directly via `(kind, name)` document identity
    (ADR-0015), not through an Environment a deployment happens to reach.

    Deliberately just discards `resolve_document_value_references()`'s
    resolved values and returns only its `Diagnostics` — `strata validate`
    has no use for the actual resolved strings, only whether every
    reference in the solution *would* resolve. Reusing that function
    directly (rather than a second, parallel implementation) is the whole
    point of this check: `strata validate` and real deploy-time resolution
    (`value_controller.py`'s `resolve_values()`, Phase 4) share one
    implementation, so they can never silently disagree about whether a
    given token is valid.
    """
    _values, diagnostics = resolve_document_value_references(index)
    return diagnostics


# ---------------------------------------------------------------------------
# Configuration.spec.paths -> every document's real file location
# (docs/design/path-conventions.md, docs/design/gap_fit_v1.md gap #7)
# ---------------------------------------------------------------------------


def _check_paths(index: DocumentIndex, root: Path | None) -> Diagnostics:
    """Every document's real, on-disk location matches whichever declared
    `PathConventionModel` covers it (structural shape only — the generic
    `validate:` sub-block v1 also had is deliberately not ported, see the
    design doc's "What's already solved differently").

    Skips entirely without a single `Configuration` document declaring
    `spec.paths`, or without `root` (needed to compute each document's path
    relative to the solution) — same "nothing declared, nothing to check"
    rule every other Configuration-backed check here already uses.
    """
    diagnostics = Diagnostics()
    if root is None:
        return diagnostics
    configuration = _single_configuration(index)
    if configuration is None or not configuration.spec.paths:
        return diagnostics

    resolved_root = root.resolve()
    for entry in index.all():
        relative = entry.source.resolve().relative_to(resolved_root).as_posix()
        convention = next((c for c in configuration.spec.paths if in_scope(relative, c.scope)), None)
        if convention is None:
            continue
        diagnostics.extend(
            _check_document_against_path_convention(entry, relative, convention), source=str(entry.source)
        )
    return diagnostics


def _check_document_against_path_convention(
    entry: IndexEntry, relative: str, convention: PathConventionModel
) -> Diagnostics:
    """The per-document half of `_check_paths()` — one document, one already
    in-scope convention. Split out so each of the three findings this can
    produce (structural mismatch, tenant code mismatch, segment pattern
    mismatch) stays a single, readable branch.

    `pattern` (directory) and `filename_pattern` (filename, optional) are
    two independent matches against different substrings of `relative` —
    both must match (a set `filename_pattern` that doesn't is the same
    `path_convention_mismatch` as a directory mismatch); captures from
    both merge into one dict for the `resolves` checks below.
    """
    diagnostics = Diagnostics()
    severity = diagnostics.error if convention.enforcement == "deny" else diagnostics.warning
    directory_match = match_directory(relative, convention.pattern)
    filename_match = match_filename(relative, convention.filename_pattern) if convention.filename_pattern else None
    if directory_match is None or (convention.filename_pattern and filename_match is None):
        severity(
            f"'{relative}' does not match path convention '{convention.name}' (pattern "
            f"'{convention.pattern}'"
            + (f", filename_pattern '{convention.filename_pattern}'" if convention.filename_pattern else "")
            + ").",
            code="path_convention_mismatch",
        )
        return diagnostics

    captures = {**directory_match.groupdict(), **(filename_match.groupdict() if filename_match else {})}
    if convention.resolves == "tenant" and entry.ref.kind is PlatformKind.TENANT and len(captures) == 1:
        tenant = cast(TenantModel, entry.model)
        (code_value,) = captures.values()
        if tenant.meta.name != code_value:
            severity(
                f"Tenant filed at '{relative}' has meta.name '{tenant.meta.name}', but path convention "
                f"'{convention.name}' expects '{code_value}'.",
                code="path_convention_tenant_mismatch",
            )
    elif convention.resolves == "layers":
        for segment in convention.segments or []:
            captured_value = captures.get(segment.name)
            if captured_value is not None and not matches_segment_pattern(segment.pattern, captured_value):
                severity(
                    f"'{relative}': segment '{segment.name}' value '{captured_value}' does not match path "
                    f"convention '{convention.name}''s declared pattern '{segment.pattern}'.",
                    code="path_convention_segment_mismatch",
                )
    return diagnostics
