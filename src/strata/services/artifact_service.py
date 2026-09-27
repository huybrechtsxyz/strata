#!/usr/bin/env python3
"""Service for loading and validating artifact configuration."""

from strata.models.artifact_model import ArtifactModel
from strata.services.base_service import BaseService


class ArtifactService(BaseService[ArtifactModel]):
    """Service for handling artifact configuration.

    No Phase 2 cross-checks yet — `spec.integration` referencing a real
    Integration with the `sources` capability is deferred until real usage
    exists (docs/design/artifact-references.md), same discipline as
    `TenantService.spec.environments`.
    """

    def _get_model_class(self) -> type[ArtifactModel]:
        """Return the ArtifactModel class for validation."""
        return ArtifactModel
