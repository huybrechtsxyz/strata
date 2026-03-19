#!/usr/bin/env python3
"""
===============================================================================
Script Name   : unknown_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Unknown service class to handle unsupported kinds.
===============================================================================
"""

from typing import Optional

from xyz_platform.models.unknown_model import UnknownModel
from xyz_platform.models.common_models import PlatformKind
from xyz_platform.services.base_service import BaseService


class UnknownService(BaseService):
    """Service class for unknown or unsupported kinds."""

    def __init__(self, path: str = None, data: dict = None):
        """Initialize the UnknownService."""
        super().__init__(path=path, data=data)
        self.model: Optional[UnknownModel] = None

    def _get_model_class(self):
        """Return a generic model class for unknown kinds."""
        return UnknownModel  # A generic empty model

    def _validate_dynamic(self, configuration_model=None, work_path=None):
        """Unknown services have no dynamic validation."""
        return True, []

    def is_deployment(self) -> bool:
        self._ensure_validated()
        kind = self.get_kind()
        if kind == PlatformKind.DEPLOYMENT:
            return True
        return False

    def is_environment(self) -> bool:
        self._ensure_validated()
        kind = self.get_kind()
        if kind == PlatformKind.ENVIRONMENT:
            return True
        return False

    def is_workspace(self) -> bool:
        self._ensure_validated()
        kind = self.get_kind()
        if kind == PlatformKind.WORKSPACE:
            return True
        return False

    def is_platform_model(self) -> bool:
        self._ensure_validated()
        kind = self.get_kind()
        if kind == PlatformKind.PLATFORM_MODEL:
            return True
        return False

    def get_service_by_kind(self):
        """Return the appropriate service instance based on the 'kind' field."""
        self._ensure_validated()
        kind = self.get_kind()

        if kind == PlatformKind.DEPLOYMENT:
            from xyz_platform.services.deployment_service import DeploymentService

            return DeploymentService(self.path)

        elif kind == PlatformKind.CONFIGURATION:
            from xyz_platform.services.configuration_service import ConfigurationService

            return ConfigurationService.get_instance()

        elif kind == PlatformKind.ENVIRONMENT:
            from xyz_platform.services.environment_service import EnvironmentService

            return EnvironmentService(self.path)
        elif kind == PlatformKind.FIREWALL:
            from xyz_platform.services.firewall_service import FirewallService

            return FirewallService(self.path)

        elif kind == PlatformKind.NAMESPACE:
            from xyz_platform.services.namespace_service import NamespaceService

            return NamespaceService(self.path)
        elif kind == PlatformKind.MODULE:
            from xyz_platform.services.module_service import ModuleService

            return ModuleService(self.path)
        elif kind == PlatformKind.PROVIDER:
            from xyz_platform.services.provider_service import ProviderService

            return ProviderService(self.path)

        elif kind == PlatformKind.RESOURCE:
            from xyz_platform.services.resource_service import ResourceService

            return ResourceService(self.path)

        elif kind == PlatformKind.WORKSPACE:
            from xyz_platform.services.workspace_service import WorkspaceService

            return WorkspaceService(self.path)

        elif kind == PlatformKind.PLATFORM_MODEL:
            from xyz_platform.services.platform_service import PlatformService

            return PlatformService(self.path)

        # This method should be overridden in subclasses if needed
        raise ValueError(f"Unsupported workspace kind: {kind}")
