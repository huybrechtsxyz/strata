#!/usr/bin/env python3
"""Service for loading and validating provider config (provider type registry) documents."""

from strata.models.provider_config_model import ProviderConfigModel
from strata.services.base_service import BaseService


class ProviderConfigService(BaseService[ProviderConfigModel]):
    """Service for handling provider config (provider type registry) documents.

    No Phase 2 dynamic validation — a provider type registry entry is
    entirely self-contained.
    """

    def _get_model_class(self) -> type[ProviderConfigModel]:
        """Return the ProviderConfigModel class for validation."""
        return ProviderConfigModel
