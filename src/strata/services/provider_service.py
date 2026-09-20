#!/usr/bin/env python3
"""Service for loading and validating provider configurations."""

from strata.models.provider_model import ProviderModel
from strata.services.base_service import BaseService


class ProviderService(BaseService[ProviderModel]):
    """Service for handling provider configurations.

    Phase 2 dynamic validation (cross-checking `spec.properties.type`/`region`
    against a configuration registry, as v1's `ProviderService` does) is
    deferred until a `Configuration` kind/model exists in v2 — see ADR-0003.
    """

    def _get_model_class(self) -> type[ProviderModel]:
        """Return the ProviderModel class for validation."""
        return ProviderModel

    def get_provider_type(self) -> str:
        """Return the provider's cloud/infrastructure type."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.type

    def get_provider_region(self) -> str:
        """Return the provider's region."""
        self._ensure_validated()
        assert self.model is not None
        return self.model.spec.properties.region
