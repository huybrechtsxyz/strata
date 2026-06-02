"""Integrations package — connects strata to external tools, services, and cloud resources."""

from strata.integrations.ansible import AnsibleIntegration
from strata.integrations.azure_appconfig import AzureAppConfigIntegration
from strata.integrations.azure_keyvault import AzureKeyVaultIntegration
from strata.integrations.base_integration import BaseIntegration

# Concrete integrations
from strata.integrations.bitwarden import BitwardenIntegration
from strata.integrations.capabilities import (
    IContainerTool,
    IFeatureStore,
    IInfrastructureTool,
    IKVStore,
    IRepositoryTool,
    ISecretStore,
    IVariableStore,
)
from strata.integrations.docker import DockerIntegration
from strata.integrations.etcd import EtcdIntegration
from strata.integrations.factory import IntegrationFactory
from strata.integrations.flagsmith import FlagsmithIntegration
from strata.integrations.git import GitIntegration
from strata.integrations.hashicorp_consul import ConsulIntegration
from strata.integrations.hashicorp_vault import VaultIntegration
from strata.integrations.helm import HelmIntegration
from strata.integrations.infisical import InfisicalIntegration
from strata.integrations.openbao import OpenBaoIntegration
from strata.integrations.opentofu import OpenTofuIntegration
from strata.integrations.registry import IntegrationRegistry
from strata.integrations.store_integration import StoreIntegration
from strata.integrations.terraform import TerraformIntegration

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
    "AnsibleIntegration",
    "BitwardenIntegration",
    "GitIntegration",
    "DockerIntegration",
    "HelmIntegration",
    "TerraformIntegration",
    "AzureKeyVaultIntegration",
    "AzureAppConfigIntegration",
    "ConsulIntegration",
    "VaultIntegration",
    "OpenBaoIntegration",
    "OpenTofuIntegration",
    "InfisicalIntegration",
    "EtcdIntegration",
    "FlagsmithIntegration",
]
