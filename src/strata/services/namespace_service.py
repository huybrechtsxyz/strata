#!/usr/bin/env python3
"""Service for loading and validating namespace configurations."""

from typing import List, Optional, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.namespace_model import NamespaceModel
from strata.services.base_service import BaseService


class NamespaceService(BaseService["NamespaceModel"]):
    """Service for handling namespace configurations and modules."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the NamespaceService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return the NamespaceModel class for validation."""
        return NamespaceModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Validates that all module file: references resolve to existing files on disk.
        Skipped when work_path is not provided (e.g. schema-only validation).

        Args:
            configuration_model: Optional ConfigurationModel for building repo_map
            work_path: Optional working path for file resolution

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not work_path:
            return True, []

        file_refs = []
        config_repo_map = configuration_model.get_remote_map() if configuration_model else {}
        repo_map = {**config_repo_map, **(self._repo_map or {})}

        if self.model and self.model.spec.modules:
            for m in self.model.spec.modules:
                file_refs.append((f"Module '{m.name}'", m.file))

        errors = self._validate_file_refs(work_path, repo_map, file_refs)
        return len(errors) == 0, errors
