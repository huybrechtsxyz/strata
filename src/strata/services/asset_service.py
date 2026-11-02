#!/usr/bin/env python3
"""Service for loading and validating asset configuration."""

from strata.models.asset_model import AssetModel
from strata.services.base_service import BaseService


class AssetService(BaseService[AssetModel]):
    """Service for handling asset configuration.

    No Phase 2 cross-checks yet — `spec.remote` referencing a real solution
    manifest remote is already caught generically via `RemoteReference()`
    (docs/design/asset-management.md Phase 1 is schema-only).
    """

    def _get_model_class(self) -> type[AssetModel]:
        """Return the AssetModel class for validation."""
        return AssetModel
