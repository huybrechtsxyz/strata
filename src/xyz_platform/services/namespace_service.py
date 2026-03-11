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

    def __init__(self, path: str = None, data: dict = None):
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

        Namespace validation is intentionally minimal since:
        - Module file references validated by FilePath (ensures files exist)
        - Lifecycle scripts validated by ScriptsModel (FilePath + extensions)
        - Namespaces don't have variables/secrets (not in model)
        - Referenced by workspace (workspace validates FilePath references)
        - Workspace validates namespace as service (variables/secrets if added)

        All validation is handled by MODEL validators in NamespaceSpecModel:
        - At least lifecycle or modules required (no empty namespaces)
        - Unique module names within namespace
        - Module files exist (FilePath validation)
        - Lifecycle scripts exist and have valid extensions

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        # No cross-reference validation needed for namespace
        return True, []
