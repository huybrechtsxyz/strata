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
from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel
from strata.models.environment_model import EnvironmentModel
from strata.models.namespace_model import NamespaceModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
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
    index: DocumentIndex, resolved_deployments: dict[str, DeploymentModel] | None = None
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

    Returns:
        Every finding, from all seven checks combined.
    """
    resolved = resolved_deployments or {}
    diagnostics = Diagnostics()
    diagnostics.extend(_check_deployments(index, resolved))
    diagnostics.extend(_check_tenants(index))
    diagnostics.extend(_check_providers(index))
    diagnostics.extend(_check_resources(index))
    diagnostics.extend(_check_workspaces(index))
    diagnostics.extend(_check_deployment_value_tokens(index, resolved))
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
        entry.ref.name: cast(ProviderConfigModel, entry.model)
        for entry in index.all_of(PlatformKind.PROVIDERCONFIG)
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
# Deployment -> Environment/Tenant reachability: value tokens resolve
# ---------------------------------------------------------------------------


def _check_deployment_value_tokens(index: DocumentIndex, resolved: dict[str, DeploymentModel]) -> Diagnostics:
    """Every token in a document reachable from a deployment resolves.

    Scoped per deployment because that is the only place declared keys and
    token-bearing documents actually meet: an Environment declares keys, a
    Deployment names which Environments apply (merged with its Tenant's, per
    `TenantSpecModel.environments`'s own description — tenant merges in
    first), and *its* Workspace is what reaches the dns/network/firewall/
    module documents that might use them. Checking module tokens against
    an unrelated environment would be either a false positive (flags a
    token satisfied by whichever environment actually deploys it) or a
    false negative (passes against an environment that never applies) —
    scoping by the real reachability graph is the only version that can't be
    wrong in either direction.
    """
    diagnostics = Diagnostics()
    for entry in index.all_of(PlatformKind.DEPLOYMENT):
        deployment = resolved.get(entry.ref.name, cast(DeploymentModel, entry.model))
        declared = _merged_declared_keys(index, deployment)
        if declared is None:
            continue  # no resolvable environment — nothing to check tokens against

        owner = "+".join(deployment.spec.environments or []) or "(none)"
        for document in _documents_reachable_from_workspace(index, deployment.spec.workspace):
            diagnostics.extend(unresolved_value_tokens(document, declared, owner), source=str(entry.source))
    return diagnostics


def _merged_declared_keys(index: DocumentIndex, deployment: DeploymentModel) -> dict[str, set[str]] | None:
    """Union declared keys across every Environment this deployment resolves.

    Existence-checking only needs the union: a token is fine if *any*
    resolved environment declares it.
    """
    names = list(deployment.spec.environments or [])
    if deployment.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
        if tenant_entry is not None:
            tenant = cast(TenantModel, tenant_entry.model)
            names = list(tenant.spec.environments or []) + names

    merged: dict[str, set[str]] = {"var": set(), "secret": set(), "feature": set()}
    found_any = False
    for name in names:
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
) -> list[PlatformBaseModel]:
    """Every DNS/Network/Firewall/Module document a workspace can render.

    A bounded, one-then-two-hop walk — not a generic graph traversal — since
    the schema only has one indirection: a workspace names Topology/
    Namespace documents directly, and those in turn attach Modules.
    """
    if not workspace_name:
        return []
    workspace_entry = index.get(PlatformKind.WORKSPACE, workspace_name)
    if workspace_entry is None:
        return []
    workspace = cast(WorkspaceModel, workspace_entry.model)
    spec = workspace.spec

    documents: list[PlatformBaseModel] = []
    for kind, names in (
        (PlatformKind.DNS, spec.dns_zones),
        (PlatformKind.NETWORK, spec.networks),
        (PlatformKind.FIREWALL, spec.firewalls),
    ):
        for name in names or []:
            found = index.get(kind, name)
            if found is not None:
                documents.append(found.model)

    for name in spec.topology or []:
        topology_entry = index.get(PlatformKind.TOPOLOGY, name)
        if topology_entry is None:
            continue
        topology = cast(TopologyModel, topology_entry.model)
        for component in topology.spec.components:
            for module_ref in component.modules or []:
                module_entry = index.get(PlatformKind.MODULE, module_ref.module)
                if module_entry is not None:
                    documents.append(module_entry.model)

    for name in spec.namespaces or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        for module_ref in namespace.spec.modules or []:
            module_entry = index.get(PlatformKind.MODULE, module_ref.module)
            if module_entry is not None:
                documents.append(module_entry.model)

    return documents
