#!/usr/bin/env python3
"""
===============================================================================
Script Name   : module_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Module service class for managing modules with source fetching.
===============================================================================
"""

from typing import List, Optional, Tuple
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.module_model import ModuleModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.logger import get_logger

logger = get_logger(__name__)


class ModuleService(BaseService):
    """Service for handling module configurations and source fetching."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the ModuleService."""
        super().__init__(path=path, data=data)
        self.model: Optional[ModuleModel] = None

    def _get_model_class(self):
        """Return the ModuleModel class for validation."""
        return ModuleModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Module validation is intentionally minimal since:
        - Source validation is complete (SourceModel validates paths, formats, security)
        - Lifecycle scripts validated by ScriptsModel (FilePath + extensions)
        - Modules are standalone definitions (no cross-references)
        - Referenced by workspace components (workspace validates FilePath references)
        - No variable/secret references (modules don't use dynamic values)

        All validation is handled by MODEL validators:
        - SourceModel: repository format, reference format, relative paths, security
        - ScriptsModel: lifecycle phase scripts (exists, valid extensions)
        - Required fields and types

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        # No cross-reference validation needed for module
        return True, []
