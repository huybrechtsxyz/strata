#!/usr/bin/env python3
"""Service for loading and validating integration configuration."""

from strata.models.integration_model import IntegrationModel
from strata.services.base_service import BaseService


class IntegrationService(BaseService[IntegrationModel]):
    """Service for handling integration configuration.

    No Phase 2 dynamic validation yet: there is no runtime `IntegrationFactory`-
    equivalent in v2 to check `spec.type` against a real registered-types set
    (v1's actual existence check happens entirely at runtime, not via a
    Configuration registry — unlike Provider/Topology, there is nothing for
    Phase 2 to cross-check against even in principle, not just "not yet
    loaded").
    """

    def _get_model_class(self) -> type[IntegrationModel]:
        """Return the IntegrationModel class for validation."""
        return IntegrationModel
