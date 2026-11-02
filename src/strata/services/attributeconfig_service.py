#!/usr/bin/env python3
"""Service for loading and validating attribute config (attribute schema) documents."""

from strata.models.attributeconfig_model import AttributeConfigModel
from strata.services.base_service import BaseService


class AttributeConfigService(BaseService[AttributeConfigModel]):
    """Service for handling attribute config (attribute schema) documents.

    No Phase 2 dynamic validation yet — Phase 2 (`_validate_dynamic()`)
    lands with the shared evaluator (`attributeconfig_evaluator.py`), which
    compiles `spec.schema` against the real JSON Schema meta-schema and
    reports an `attributeconfig_invalid` diagnostic if it doesn't. Applying
    the schema against other documents in the solution is a controller-level
    concern (`semantic_checks.py`), not this service's job.
    """

    def _get_model_class(self) -> type[AttributeConfigModel]:
        """Return the AttributeConfigModel class for validation."""
        return AttributeConfigModel
