#!/usr/bin/env python3
"""Service for loading and validating solution-wide configuration."""

from strata.models.configuration_model import ConfigurationModel
from strata.services.base_service import BaseService


class ConfigurationService(BaseService[ConfigurationModel]):
    """Service for handling the configuration registry.

    No Phase 2 dynamic validation — the configuration file is the source of
    truth other services cross-check against, not itself cross-checked.
    """

    def _get_model_class(self) -> type[ConfigurationModel]:
        """Return the ConfigurationModel class for validation."""
        return ConfigurationModel
