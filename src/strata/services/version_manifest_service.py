#!/usr/bin/env python3
"""Service for loading and validating version-manifest files."""

from typing import List, Optional, Tuple

from strata.models.version_manifest_model import VersionManifestModel
from strata.services.base_service import BaseService


class VersionManifestService(BaseService["VersionManifestModel"]):
    """Service for handling version-manifest files (kind: version-manifest)."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the VersionManifestService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the VersionManifestModel class."""
        return VersionManifestModel

    def _validate_dynamic(
        self,
        configuration_model=None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Version-manifest files are self-contained — no cross-reference validation."""
        return True, []
