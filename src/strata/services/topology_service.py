#!/usr/bin/env python3
"""Service for loading and validating topology configuration."""

from strata.models.topology_model import TopologyModel
from strata.services.base_service import BaseService


class TopologyService(BaseService[TopologyModel]):
    """Service for handling topology configuration.

    No Phase 2 dynamic validation yet: checking `components[].resource`/
    `namespaces[].namespace` against real `Resource`/`Namespace` documents
    requires the solution-wide resource-loading machinery Workspace itself
    doesn't have yet either (deferred, same as `NamespaceService`). Checking
    `type` against a registered topology-type schema requires extending
    `ConfigurationModel` with `topologies`/`ConfigurationTopologyModel`
    (not yet ported — see ADR-0011 Remaining Work).
    """

    def _get_model_class(self) -> type[TopologyModel]:
        """Return the TopologyModel class for validation."""
        return TopologyModel
