#!/usr/bin/env python3
"""Service for loading and validating workspace configuration."""

from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService


class WorkspaceService(BaseService[WorkspaceModel]):
    """Service for handling workspace configuration.

    No Phase 2 dynamic validation yet: cross-checking `Topology`'s internal
    references (`components[].resource`, `namespaces[].namespace`) against
    this workspace's own `resources`/`namespaces`, and cross-checking each
    `WorkspaceResourceModel.subnet.subnet` against the real subnet names
    inside the referenced Network document's `NetworkDefinitionModel.subnets[]`,
    both require actually loading the referenced file (ADR-0011) — deferred,
    same reasoning as `NamespaceService`/`TopologyService`'s own deferred
    Phase 2 checks.
    """

    def _get_model_class(self) -> type[WorkspaceModel]:
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel
