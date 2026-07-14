#!/usr/bin/env python3
"""Service for loading and validating version-lock files."""

from typing import List, Optional, Tuple

from strata.models.version_lock_model import VersionLockModel
from strata.services.base_service import BaseService


class VersionLockService(BaseService["VersionLockModel"]):
    """Service for handling version-lock files (kind: version-lock)."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the VersionLockService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the VersionLockModel class."""
        return VersionLockModel

    def _validate_dynamic(
        self,
        configuration_model=None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Version-lock files are self-contained — no cross-reference validation."""
        return True, []
