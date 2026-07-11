"""Service for detecting and delegating unsupported/unknown platform kinds."""

from typing import Optional

from strata.exceptions.model_exception import UnsupportedKindError
from strata.models.common_models import PlatformKind
from strata.models.unknown_model import UnknownModel
from strata.services.base_service import BaseService


class UnknownService(BaseService["UnknownModel"]):
    """Service class for unknown or unsupported kinds."""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the UnknownService."""
        super().__init__(path=path, data=data)
        self.model = None

    def _get_model_class(self):
        """Return a generic model class for unknown kinds."""
        return UnknownModel  # A generic empty model

    def _validate_dynamic(self, configuration_model=None, work_path=None):
        """Unknown services have no dynamic validation."""
        return True, []

    def is_deployment(self) -> bool:
        self._ensure_validated()
        return self.get_kind() == PlatformKind.DEPLOYMENT

    def is_environment(self) -> bool:
        self._ensure_validated()
        return self.get_kind() == PlatformKind.ENVIRONMENT

    def is_workspace(self) -> bool:
        self._ensure_validated()
        return self.get_kind() == PlatformKind.WORKSPACE

    def is_platform_model(self) -> bool:
        self._ensure_validated()
        return self.get_kind() == PlatformKind.PLATFORM_MODEL

    def get_service_by_kind(self):
        """Return the appropriate service instance based on the 'kind' field."""
        self._ensure_validated()
        kind = self.get_kind()

        if kind == PlatformKind.DEPLOYMENT:
            from strata.services.deployment_service import DeploymentService

            return DeploymentService(self.path)

        elif kind == PlatformKind.CONFIGURATION:
            from strata.services.configuration_service import ConfigurationService

            return ConfigurationService.get_instance()

        elif kind == PlatformKind.TENANT:
            from strata.services.tenant_service import TenantService

            return TenantService(self.path)

        elif kind == PlatformKind.ENVIRONMENT:
            from strata.services.environment_service import EnvironmentService

            return EnvironmentService(self.path)
        elif kind == PlatformKind.FIREWALL:
            from strata.services.firewall_service import FirewallService

            return FirewallService(self.path)

        elif kind == PlatformKind.DNS:
            from strata.services.dns_service import DnsService

            return DnsService(self.path)

        elif kind == PlatformKind.NETWORK:
            from strata.services.network_service import NetworkService

            return NetworkService(self.path)

        elif kind == PlatformKind.NAMESPACE:
            from strata.services.namespace_service import NamespaceService

            return NamespaceService(self.path)
        elif kind == PlatformKind.MODULE:
            from strata.services.module_service import ModuleService

            return ModuleService(self.path)
        elif kind == PlatformKind.PROVIDER:
            from strata.services.provider_service import ProviderService

            return ProviderService(self.path)

        elif kind == PlatformKind.RESOURCE:
            from strata.services.resource_service import ResourceService

            return ResourceService(self.path)

        elif kind == PlatformKind.WORKSPACE:
            from strata.services.workspace_service import WorkspaceService

            return WorkspaceService(self.path)

        elif kind == PlatformKind.PLATFORM_MODEL:
            from strata.services.platform_artifact_service import PlatformService

            return PlatformService(self.path)

        elif kind == PlatformKind.VERSION_LOCK:
            from strata.services.version_lock_service import VersionLockService

            return VersionLockService(self.path)

        elif kind == PlatformKind.VERSION_MANIFEST:
            from strata.services.version_manifest_service import VersionManifestService

            return VersionManifestService(self.path)

        # This method should be overridden in subclasses if needed
        raise UnsupportedKindError(str(kind))
