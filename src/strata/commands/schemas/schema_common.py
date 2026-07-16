"""Shared constants and helpers for schema CLI commands."""

from typing import Dict, Type

from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_manifest_model import DeploymentManifestModel
from strata.models.deployment_model import DeploymentModel
from strata.models.dns_model import DnsModel
from strata.models.environment_model import EnvironmentModel
from strata.models.firewall_model import FirewallModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.promotion_record_model import PromotionRecordModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.tenant_model import TenantModel
from strata.models.version_lock_model import VersionLockModel
from strata.models.version_manifest_model import VersionManifestModel
from strata.models.workspace_model import WorkspaceModel

KIND_TO_MODEL: Dict[PlatformKind, Type] = {
    PlatformKind.CONFIGURATION: ConfigurationModel,
    PlatformKind.TENANT: TenantModel,
    PlatformKind.DEPLOYMENT: DeploymentModel,
    PlatformKind.DEPLOYMENT_MANIFEST: DeploymentManifestModel,
    PlatformKind.DNS: DnsModel,
    PlatformKind.ENVIRONMENT: EnvironmentModel,
    PlatformKind.FIREWALL: FirewallModel,
    PlatformKind.MODULE: ModuleModel,
    PlatformKind.NAMESPACE: NamespaceModel,
    PlatformKind.NETWORK: NetworkModel,
    PlatformKind.PLATFORM_MODEL: PlatformArtifactModel,
    PlatformKind.PROVIDER: ProviderModel,
    PlatformKind.RESOURCE: ResourceModel,
    PlatformKind.WORKSPACE: WorkspaceModel,
    PlatformKind.VERSION_LOCK: VersionLockModel,
    PlatformKind.VERSION_MANIFEST: VersionManifestModel,
    PlatformKind.PROMOTION_RECORD: PromotionRecordModel,
}

# Internal artifact kinds are excluded here because users do not author them.
KIND_TO_GLOBS: Dict[PlatformKind, list[str]] = {
    PlatformKind.CONFIGURATION: ["config/**/*.yaml"],
    PlatformKind.DEPLOYMENT: ["deploy/**/*.yaml"],
    PlatformKind.DNS: ["dns/**/*.yaml"],
    PlatformKind.ENVIRONMENT: ["envs/**/*.yaml", "environments/**/*.yaml"],
    PlatformKind.FIREWALL: ["firewalls/**/*.yaml"],
    PlatformKind.MODULE: ["modules/**/*.yaml"],
    PlatformKind.NAMESPACE: ["namespaces/**/*.yaml"],
    PlatformKind.NETWORK: ["networks/**/*.yaml"],
    PlatformKind.PROVIDER: ["providers/**/*.yaml"],
    PlatformKind.RESOURCE: ["resources/**/*.yaml"],
    PlatformKind.TENANT: ["tenants/**/*.yaml"],
    PlatformKind.WORKSPACE: ["stack/**/*.yaml"],
}
