#!/usr/bin/env python3
"""Service for loading and validating the solution manifest."""

from strata.models.solution_model import SolutionModel
from strata.services.base_service import BaseService


class SolutionService(BaseService[SolutionModel]):
    """Service for handling the solution manifest (`strata.yaml`).

    No Phase 2 dynamic validation wired in yet. Two cross-document checks are
    known to be needed once a loading layer exists (ADR-0003; nothing in v2
    loads files yet):

    - `spec.remotes[].integration` must name a real Integration declaring the
      'sources' capability.
    - `spec.configuration` must resolve to at least one `kind: configuration`
      document.

    Both require documents this service does not load, so neither is
    implemented here — same deferral as `WorkspaceService`'s topology checks.
    """

    def _get_model_class(self) -> type[SolutionModel]:
        """Return the SolutionModel class for validation."""
        return SolutionModel
