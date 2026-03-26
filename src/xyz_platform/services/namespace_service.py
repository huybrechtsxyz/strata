#!/usr/bin/env python3
"""
===============================================================================
Script Name   : namespace_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Namespace service class for managing namespaces and their modules.
===============================================================================
"""

from typing import List, Optional, Tuple
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.namespace_model import NamespaceModel
from xyz_platform.services.base_service import BaseService


class NamespaceService(BaseService):
    """Service for handling namespace configurations and modules."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the NamespaceService."""
        super().__init__(path=path, data=data)
        self.model: Optional[NamespaceModel] = None

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
        repo_map = configuration_model.get_repo_map() if configuration_model else {}

        if self.model and self.model.spec.modules:
            for m in self.model.spec.modules:
                file_refs.append((f"Module '{m.name}'", m.file))

        errors = self._validate_file_refs(work_path, repo_map, file_refs)
        return len(errors) == 0, errors
