#!/usr/bin/env python3
"""Service for loading and validating firewall ruleset configuration."""

from strata.models.firewall_model import FirewallModel
from strata.services.base_service import BaseService


class FirewallService(BaseService[FirewallModel]):
    """Service for handling firewall ruleset configuration.

    No Phase 2 dynamic validation: checking a `${var:KEY}`/`${secret:KEY}`
    token's key against a real Environment (ADR-0002) requires the
    `environment` kind, which doesn't exist in v2 yet.

    v1's `FirewallService` also supported merging multiple firewall documents
    (`merge_firewalls`) — a workspace/environment composition feature, not
    core schema validation. Not ported: no v2 workspace/environment layer
    exists yet to consume it (same reasoning as `NetworkService`, ADR-0007).
    """

    def _get_model_class(self) -> type[FirewallModel]:
        """Return the FirewallModel class for validation."""
        return FirewallModel
