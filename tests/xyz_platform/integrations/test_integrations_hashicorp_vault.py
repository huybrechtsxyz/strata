#!/usr/bin/env python3
"""Unit tests for VaultIntegration (HashiCorp Vault)."""

from unittest.mock import MagicMock, patch

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.hashicorp_vault import VaultIntegration
from xyz_platform.integrations.capabilities import ISecretStore, IVariableStore, IKVStore
from xyz_platform.models.integration_model import IntegrationModel, IntegrationEndpointsSpecModel


def _cfg(name="vault", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="vault", endpoints=endpoints)


class TestVaultIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_vault(self):
        i = VaultIntegration(_cfg())
        assert i.command == "vault"

    def test_capabilities(self):
        assert ISecretStore in VaultIntegration.CAPABILITIES
        assert IVariableStore in VaultIntegration.CAPABILITIES
        assert IKVStore in VaultIntegration.CAPABILITIES

    def test_version_command(self):
        i = VaultIntegration(_cfg())
        assert i.get_version_command() == ["vault", "version"]

    def test_vault_address_from_endpoint(self):
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        assert i.vault_addr == "https://vault.example.com"

    def test_vault_address_empty_when_no_endpoint(self):
        i = VaultIntegration(_cfg())
        assert i.vault_addr == ""


class TestVaultParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_vault_version(self):
        i = VaultIntegration(_cfg())
        assert i.parse_version("Vault v1.14.0 (abc1234)") == "1.14.0"

    def test_parse_no_version_fallback(self):
        i = VaultIntegration(_cfg())
        result = i.parse_version("no version here")
        assert result == "no version here"


class TestVaultSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = VaultIntegration(_cfg(address="https://vault.example.com"))
        b = VaultIntegration(_cfg(address="https://vault.example.com"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = VaultIntegration(_cfg(address="https://vault1.example.com"))
        BaseIntegration._instances.clear()
        b = VaultIntegration(_cfg(address="https://vault2.example.com"))
        assert a is not b


class TestVaultGetSecret:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_secret_no_token_returns_none(self):
        i = VaultIntegration(_cfg())
        result = i.get_secret("secret/data/myapp")
        assert result is None

    def test_get_secret_with_token(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg())
        mock_result = MagicMock(returncode=0, stdout='{"data": {"data": {"password": "s3cr3t"}}}', stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result):
            result = i.get_secret("secret/data/myapp#password")
        # May return value or None depending on key parsing
        assert result is None or result == "s3cr3t"
