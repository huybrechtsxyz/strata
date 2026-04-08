"""Integrations package — connects xyz-platform to external tools, services, and cloud resources."""

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.store_integration import StoreIntegration
from xyz_platform.integrations.capabilities import (
    IVariableStore,
    ISecretStore,
    IFeatureStore,
    IKVStore,
    IRepositoryTool,
    IInfrastructureTool,
    IContainerTool,
)
from xyz_platform.integrations.registry import IntegrationRegistry
from xyz_platform.integrations.factory import IntegrationFactory

# Concrete integrations
from xyz_platform.integrations.bitwarden import BitwardenIntegration
from xyz_platform.integrations.git import GitIntegration
from xyz_platform.integrations.docker import DockerIntegration
from xyz_platform.integrations.terraform import TerraformIntegration
from xyz_platform.integrations.azure_keyvault import AzureKeyVaultIntegration
from xyz_platform.integrations.azure_appconfig import AzureAppConfigIntegration
from xyz_platform.integrations.hashicorp_consul import ConsulIntegration
from xyz_platform.integrations.hashicorp_vault import VaultIntegration

__all__ = [
    # Base classes
    "BaseIntegration",
    "StoreIntegration",
    # Capability protocols
    "IVariableStore",
    "ISecretStore",
    "IFeatureStore",
    "IKVStore",
    "IRepositoryTool",
    "IInfrastructureTool",
    "IContainerTool",
    # Infrastructure
    "IntegrationRegistry",
    "IntegrationFactory",
    # Concrete integrations
    "BitwardenIntegration",
    "GitIntegration",
    "DockerIntegration",
    "TerraformIntegration",
    "AzureKeyVaultIntegration",
    "AzureAppConfigIntegration",
    "ConsulIntegration",
    "VaultIntegration",
]
