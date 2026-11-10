#!/usr/bin/env python3
"""The documents a deployment reaches: one answer for the token checks in `strata validate` and for the images
`effective_images()` lists (docs/work/artifact-gap-analysis.md, design 3).

Moved out of `semantic_checks.py`, which validates every token in these documents, so that
`effective_images()` can count the `${artifact:}` tokens in the same set.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.namespace_model import NamespaceModel
from strata.models.tenant_model import TenantModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.workspace_service import WorkspaceService, effective_providers


@dataclass(frozen=True)
class ReachedDocument:
    """One document a deployment reaches.

    `claimed` is whether some workspace execution step's `targets` claims it
    (`WorkspaceService.claimed_document_names()`, docs/design/gap_fit_v1.md gap #12): `unresolved_value_tokens()`
    accepts an `${output:...}` token only then. A module reached through a targeted namespace inherits that
    namespace's claim; every other document is never claimable today.
    """

    model: PlatformBaseModel
    kind: PlatformKind
    name: str
    source: Path
    claimed: bool = False


def documents_reachable_from_workspace(
    index: DocumentIndex,
    workspace_name: str | None,
    deployment: DeploymentModel | None = None,
    *,
    enabled_only: bool = False,
) -> list[ReachedDocument]:
    """Every document a workspace renders or configures: DNS/Network/Firewall/Module plus the workspace document
    itself and every Resource/Provider/Topology it references (config passthrough fields,
    docs/design/gap_fit_v1.md gap #10's Phase 7 finding).

    A bounded, one-then-two-hop walk, not a generic graph traversal: a workspace names Topology/Namespace documents
    directly, and those attach Modules. The workspace document is included whole, so
    `WorkspaceResourceModel.configuration` (the workspace's own per-resource override) is covered without a
    separate fetch.

    Args:
        index: The loaded solution's documents.
        workspace_name: The workspace to walk; nothing is returned when it is unset or not in the index.
        deployment: Selects the providers (`effective_providers()`); `None` takes the workspace's own.
        enabled_only: Leave out a module whose namespace reference has `enabled: false`, as `effective_images()`
            does for its services. Validation keeps it (`False`): a disabled module is still checked.
    """
    if not workspace_name:
        return []
    workspace_entry = index.get(PlatformKind.WORKSPACE, workspace_name)
    if workspace_entry is None:
        return []
    workspace = cast(WorkspaceModel, workspace_entry.model)
    spec = workspace.spec
    claimed_names = WorkspaceService.from_model(workspace).claimed_document_names()

    documents = [ReachedDocument(workspace, PlatformKind.WORKSPACE, workspace_name, workspace_entry.source)]

    for kind, names in (
        (PlatformKind.DNS, spec.dns_zones),
        (PlatformKind.NETWORK, spec.networks),
        (PlatformKind.FIREWALL, spec.firewalls),
        (PlatformKind.PROVIDER, list(dict.fromkeys(effective_providers(workspace, deployment).values()))),
    ):
        for name in names or []:
            found = index.get(kind, name)
            if found is not None:
                documents.append(ReachedDocument(found.model, kind, name, found.source, name in claimed_names))

    for workspace_resource in spec.resources or []:
        if workspace_resource.resource is None:
            continue
        resource_entry = index.get(PlatformKind.RESOURCE, workspace_resource.resource)
        if resource_entry is not None:
            documents.append(
                ReachedDocument(
                    resource_entry.model, PlatformKind.RESOURCE, workspace_resource.resource, resource_entry.source
                )
            )

    for topo in spec.topology or []:
        for component in topo.components:
            for module_ref in component.modules or []:
                module_entry = index.get(PlatformKind.MODULE, module_ref.module)
                if module_entry is not None:
                    documents.append(
                        ReachedDocument(module_entry.model, PlatformKind.MODULE, module_ref.module, module_entry.source)
                    )

    for name in spec.namespaces or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        namespace_claimed = name in claimed_names
        for module_ref in namespace.spec.modules or []:
            if enabled_only and not module_ref.enabled:
                continue
            module_entry = index.get(PlatformKind.MODULE, module_ref.module)
            if module_entry is not None:
                documents.append(
                    ReachedDocument(
                        module_entry.model,
                        PlatformKind.MODULE,
                        module_ref.module,
                        module_entry.source,
                        namespace_claimed,
                    )
                )

    return documents


def reachable_environment_names(index: DocumentIndex, deployment: DeploymentModel) -> list[str]:
    """Every Environment name this deployment resolves: its Tenant's own `spec.environments` first (tenant merges
    in before deployment), then the deployment's own."""
    names = list(deployment.spec.environments or [])
    if deployment.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
        if tenant_entry is not None:
            tenant = cast(TenantModel, tenant_entry.model)
            names = list(tenant.spec.environments or []) + names
    return names


def documents_reached_by_deployment(
    index: DocumentIndex, deployment: DeploymentModel, source: Path, *, enabled_only: bool = False
) -> list[ReachedDocument]:
    """The workspace's documents (`documents_reachable_from_workspace()`), plus the deployment itself, its tenant
    and its environments: every document whose strings `deploy run` may resolve a token in.

    Args:
        index: The loaded solution's documents.
        deployment: The resolved deployment (its `extends` chain merged), which is the one listed.
        source: The deployment document's file, for findings.
        enabled_only: As for `documents_reachable_from_workspace()`.
    """
    documents = documents_reachable_from_workspace(
        index, deployment.spec.workspace, deployment, enabled_only=enabled_only
    )
    documents.append(ReachedDocument(deployment, PlatformKind.DEPLOYMENT, deployment.meta.name, source))
    if deployment.spec.tenant:
        tenant_entry = index.get(PlatformKind.TENANT, deployment.spec.tenant)
        if tenant_entry is not None:
            documents.append(
                ReachedDocument(tenant_entry.model, PlatformKind.TENANT, deployment.spec.tenant, tenant_entry.source)
            )
    for name in reachable_environment_names(index, deployment):
        env_entry = index.get(PlatformKind.ENVIRONMENT, name)
        if env_entry is not None:
            documents.append(ReachedDocument(env_entry.model, PlatformKind.ENVIRONMENT, name, env_entry.source))
    return documents
