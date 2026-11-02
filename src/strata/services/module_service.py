#!/usr/bin/env python3
"""Service for loading and validating module configuration."""

from strata.models.module_model import ModuleModel
from strata.services.base_service import BaseService


class ModuleService(BaseService[ModuleModel]):
    """Service for handling module configuration.

    No Phase 2 dynamic validation: v1's `ModuleService._validate_dynamic()`
    was already a no-op ("Currently no module-level cross-service checks are
    needed"). Checking a `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`
    token's key against a real Environment (ADR-0002) requires the
    `environment` kind, which doesn't exist in v2 yet.

    v1's service-layer "Phase 1.5" self-consistency check (`_validate_self()`
    — `services[].depends_on` validation) is folded into
    `ModuleSpecModel.validate_depends_on()` (a Pydantic model validator)
    instead of a separate service-layer step, since it only needs the
    document itself — no external context required.
    """

    def _get_model_class(self) -> type[ModuleModel]:
        """Return the ModuleModel class for validation."""
        return ModuleModel
