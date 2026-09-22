#!/usr/bin/env python3
"""Service for loading and validating namespace configuration."""

from strata.models.namespace_model import NamespaceModel
from strata.services.base_service import BaseService


class NamespaceService(BaseService[NamespaceModel]):
    """Service for handling namespace configuration.

    No Phase 2 dynamic validation: v1's `NamespaceService._validate_dynamic()`
    resolved `modules[].file` references against a work path + cross-repo
    `repo_map` (built from `ConfigurationModel`/registered repositories).
    v2's `modules[].module` names a Module document instead of a path, so the
    check becomes "does this name resolve in the discovery index" — which
    needs the solution loading layer that doesn't exist yet. Add when it does.
    """

    def _get_model_class(self) -> type[NamespaceModel]:
        """Return the NamespaceModel class for validation."""
        return NamespaceModel
