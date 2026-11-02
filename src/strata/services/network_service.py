#!/usr/bin/env python3
"""Service for loading and validating network topology configuration."""

from strata.models.network_model import NetworkModel
from strata.services.base_service import BaseService


class NetworkService(BaseService[NetworkModel]):
    """Service for handling network topology configuration.

    No Phase 2 dynamic validation: v1's `NetworkService._validate_dynamic()`
    was already a no-op ("Network has minimal cross-reference validation —
    self-contained"). Checking a `${var:KEY}`/`${secret:KEY}` token's key
    against a real Environment (ADR-0002) requires the `environment` kind,
    which doesn't exist in v2 yet.

    v1's `NetworkService` also supported merging multiple network documents
    (`merge_networks`/`merge_networkfiles`) — a workspace/environment
    composition feature, not core schema validation. Not ported: no v2
    workspace/environment layer exists yet to consume it (see ADR-0007).
    """

    def _get_model_class(self) -> type[NetworkModel]:
        """Return the NetworkModel class for validation."""
        return NetworkModel
