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

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.value_references import resolve_document_value_references
from strata.models.common_models import PlatformBaseModel, PlatformKind, SourceModel
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.models.environment_model import EnvironmentModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.solution_model import RemoteType, SolutionModel
from strata.models.tenant_model import TenantModel
from strata.models.topology_config_model import TopologyConfigModel
from strata.models.topology_model import TopologyModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.deployment_service import DeploymentService
from strata.services.environment_service import EnvironmentService, unresolved_value_tokens
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.tenant_service import TenantService
from strata.services.workspace_service import WorkspaceService
from strata.utils.diagnostics import Diagnostics


def run_semantic_checks(
    index: DocumentIndex,
    resolved_deployments: dict[str, DeploymentModel] | None = None,
    solution: SolutionModel | None = None,
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

    Returns:
        Every finding, from all nine checks combined.
    """
    resolved = resolved_deployments or {}
    diagnostics = Diagnostics()
    diagnostics.extend(_check_deployments(index, resolved))
    diagnostics.extend(_check_tenants(index))
    diagnostics.extend(_check_providers(index))
    diagnostics.extend(_check_resources(index))
    diagnostics.extend(_check_workspaces(index))
    diagnostics.extend(_check_environments(index))
    diagnostics.extend(_check_deployment_value_tokens(index, resolved))
    diagnostics.extend(_check_remotes(index, solution))
    diagnostics.extend(_check_value_references(index))
    return diagnostics


# ---------------------------------------------------------------------------
# Deployment -> Workspace: stages name real execution steps
# ---------------------------------------------------------------------------


def _check_deployments(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = resolved.get(entry.ref.name, cast(DeploymentModel, entry.model))
        if not deployment.spec.workspace:
            continue
        workspace_entry = index.get(PlatformKind.WORKSPACE, deployment.spec.workspace)
        if workspace_entry is None:
            continue  # already reported by validate_references

        service = DeploymentService.from_model(deployment)
        workspace = cast(WorkspaceModel, workspace_entry.model)
        diagnostics.extend(service.validate_stages_against_workspace(workspace), source=str(entry.source))
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
        topology_models: dict[str, TopologyModel] = {}
        for name in workspace.spec.topology or []:
            found = index.get(PlatformKind.TOPOLOGY, name)
            if found is not None:
                topology_models[name] = cast(TopologyModel, found.model)
        if not topology_models:
            continue

        service = WorkspaceService.from_model(workspace)
        diagnostics.extend(service.validate_topology_references(topology_models), source=str(entry.source))
        diagnostics.extend(
            _check_workspace_topology_components(index, workspace, topology_models), source=str(entry.source)
        )

    return diagnostics


def _check_workspace_topology_components(
    index: DocumentIndex, workspace: WorkspaceModel, topology_models: dict[str, TopologyModel]
) -> Diagnostics:
    """The registry-backed half of the workspace/topology check.

    Needs `spec.topologies` from the loaded Configuration registry —
    `validate_topology_components`'s signature requires it (no Optional
    fallback), so this is skipped entirely when no Configuration document
    exists, rather than guessing a policy that was never declared.
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
    return service.validate_topology_components(configuration, topology_config_models, topology_models)


# ---------------------------------------------------------------------------
# Environment -> Artifact: store: artifact variables reference a real artifact
# ---------------------------------------------------------------------------


def _check_environments(index: DocumentIndex) -> Diagnostics:
    """Every `store: artifact` variable's `value` names a real `ArtifactModel`
    (docs/design/artifact-references.md's full-review finding, 2026-09-27):
    `VariableStoreModel.value: Any` is only conditionally an artifact
    reference, so `references.py`'s generic `References()` walker never
    checks it — this is that missing check, following the exact
    `WorkspaceService.validate_topology_references()` precedent for a
    conditionally-meaningful field.
    """
    diagnostics = Diagnostics()
    artifact_names = set(index.names_of(PlatformKind.ARTIFACT))
    for entry in index.all_of(PlatformKind.ENVIRONMENT):
        environment = cast(EnvironmentModel, entry.model)
        service = EnvironmentService.from_model(environment)
        diagnostics.extend(service.validate_artifact_references(artifact_names), source=str(entry.source))
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
    provider/topology documents themselves** — docs/_gap_v1.md gap #10's
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
    fields: docs/_gap_v1.md gap #10's Phase 7 finding — these were never
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

    for name in spec.topology or []:
        topology_entry = index.get(PlatformKind.TOPOLOGY, name)
        if topology_entry is None:
            continue
        documents.append((topology_entry.model, False))
        topology = cast(TopologyModel, topology_entry.model)
        for component in topology.spec.components:
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
# needs it (docs/_gap_v1.md gap #3).
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
    still-unbuilt gap (see docs/_gap_v1.md), not this check's concern.
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
