#!/usr/bin/env python3
"""Service for loading and validating topology config (topology type registry) documents."""

from strata.models.topology_config_model import TopologyConfigModel
from strata.services.base_service import BaseService


class TopologyConfigService(BaseService[TopologyConfigModel]):
    """Service for handling topology config (topology type registry) documents.

    No Phase 2 dynamic validation — a topology type registry entry is
    entirely self-contained.
    """

    def _get_model_class(self) -> type[TopologyConfigModel]:
        """Return the TopologyConfigModel class for validation."""
        return TopologyConfigModel
