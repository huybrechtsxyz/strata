#!/usr/bin/env python3
"""Integration tests verifying capability protocol compliance across store integrations."""


from xyz_platform.integrations.azure_appconfig import AzureAppConfigIntegration
from xyz_platform.integrations.azure_keyvault import AzureKeyVaultIntegration
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.bitwarden import BitwardenIntegration
from xyz_platform.integrations.capabilities import (
    IFeatureStore,
    IKVStore,
    ISecretStore,
    IVariableStore,
)
from xyz_platform.integrations.hashicorp_consul import ConsulIntegration
from xyz_platform.integrations.hashicorp_vault import VaultIntegration
from xyz_platform.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name, itype, address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type=itype, endpoints=endpoints)


class TestCapabilityProtocolCompliance:
    """
    Verify each store integration satisfies the expected capability protocols
    using isinstance checks against runtime_checkable Protocols.
    """

    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_appconfig_satisfies_variable_store(self):
        i = AzureAppConfigIntegration(_cfg("ac", "azure_appconfig"))
        assert isinstance(i, IVariableStore)

    def test_appconfig_satisfies_feature_store(self):
        i = AzureAppConfigIntegration(_cfg("ac", "azure_appconfig"))
        assert isinstance(i, IFeatureStore)

    def test_keyvault_satisfies_secret_store(self):
        i = AzureKeyVaultIntegration(_cfg("kv", "azure_keyvault"))
        assert isinstance(i, ISecretStore)

    def test_bitwarden_satisfies_secret_store(self):
        i = BitwardenIntegration(_cfg("bw", "bitwarden"))
        assert isinstance(i, ISecretStore)

    def test_vault_satisfies_secret_store(self):
        i = VaultIntegration(_cfg("v", "vault"))
        assert isinstance(i, ISecretStore)

    def test_vault_satisfies_variable_store(self):
        i = VaultIntegration(_cfg("v", "vault"))
        assert isinstance(i, IVariableStore)

    def test_vault_satisfies_kv_store(self):
        i = VaultIntegration(_cfg("v", "vault"))
        assert isinstance(i, IKVStore)

    def test_consul_satisfies_variable_store(self):
        i = ConsulIntegration(_cfg("c", "consul"))
        assert isinstance(i, IVariableStore)

    def test_consul_satisfies_kv_store(self):
        i = ConsulIntegration(_cfg("c", "consul"))
        assert isinstance(i, IKVStore)
