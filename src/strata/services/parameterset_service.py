#!/usr/bin/env python3
"""Service for loading and validating parameter sets."""

from strata.models.parameterset_model import ParameterSetModel
from strata.services.base_service import BaseService


class ParameterSetService(BaseService[ParameterSetModel]):
    """Service for handling parameter sets.

    No Phase 2 cross-checks: a parameter set references nothing, and its values are plain (the model rejects
    any `${...}` token).
    """

    def _get_model_class(self) -> type[ParameterSetModel]:
        """Return the ParameterSetModel class for validation."""
        return ParameterSetModel
