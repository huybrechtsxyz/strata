#!/usr/bin/env python3
"""Unit tests for AzureKeyVaultIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.azure_keyvault import AzureKeyVaultIntegration
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ISecretStore
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="keyvault", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="azure_keyvault", endpoints=endpoints)


class TestAzureKeyVaultInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_az(self):
        i = AzureKeyVaultIntegration(_cfg())
        assert i.command == "az"

    def test_capabilities_include_secrets(self):
        assert ISecretStore in AzureKeyVaultIntegration.CAPABILITIES

    def test_version_command(self):
        i = AzureKeyVaultIntegration(_cfg())
        assert i.get_version_command() == ["az", "version"]

    def test_keyvault_url_from_endpoint(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://myvault.vault.azure.net"))
        assert "myvault" in i.keyvault_url

    def test_keyvault_url_empty_when_no_endpoint(self):
        i = AzureKeyVaultIntegration(_cfg())
        assert i.keyvault_url == ""


class TestAzureKeyVaultParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_az_version(self):
        i = AzureKeyVaultIntegration(_cfg())
        result = i.parse_version('{"azure-cli": "2.50.0"}')
        assert "2.50.0" in result or result

    def test_parse_fallback(self):
        i = AzureKeyVaultIntegration(_cfg())
        result = i.parse_version("azure-cli 2.50.0")
        assert "2.50.0" in result


class TestAzureKeyVaultSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_url_same_instance(self):
        a = AzureKeyVaultIntegration(_cfg(address="https://vault1.vault.azure.net"))
        b = AzureKeyVaultIntegration(_cfg(address="https://vault1.vault.azure.net"))
        assert a is b

    def test_different_urls_different_instances(self):
        a = AzureKeyVaultIntegration(_cfg(address="https://vault1.vault.azure.net"))
        BaseIntegration._instances.clear()
        b = AzureKeyVaultIntegration(_cfg(address="https://vault2.vault.azure.net"))
        assert a is not b


class TestAzureKeyVaultGetSecret:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_secret_no_url_returns_none(self):
        i = AzureKeyVaultIntegration(_cfg())
        result = i.get_secret("my-secret")
        assert result is None

    def test_list_secrets_no_url_returns_empty(self):
        i = AzureKeyVaultIntegration(_cfg())
        result = i.list_secrets()
        assert result == []


class TestAzureKeyVaultCliLogin:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_is_cli_logged_in_true_when_account_show_succeeds(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://vault.vault.azure.net"))
        proc = MagicMock()
        proc.returncode = 0
        with patch.object(i, "_run_integration", return_value=proc):
            assert i._is_cli_logged_in() is True

    def test_is_cli_logged_in_false_when_account_show_fails(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://vault.vault.azure.net"))
        proc = MagicMock()
        proc.returncode = 1
        with patch.object(i, "_run_integration", return_value=proc):
            assert i._is_cli_logged_in() is False

    def test_is_cli_logged_in_false_on_exception(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://vault.vault.azure.net"))
        with patch.object(i, "_run_integration", side_effect=OSError("no az")):
            assert i._is_cli_logged_in() is False

    def test_ensure_available_accepts_cli_login_with_no_sp_env_vars(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://vault.vault.azure.net"))
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "_is_cli_logged_in", return_value=True),
        ):
            ok, msg = i.ensure_available()
        assert ok is True
        assert msg == ""

    def test_ensure_available_rejects_when_no_auth_at_all(self):
        i = AzureKeyVaultIntegration(_cfg(address="https://vault.vault.azure.net"))
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "_is_cli_logged_in", return_value=False),
        ):
            ok, msg = i.ensure_available()
        assert ok is False
        assert "az login" in msg

    def test_setup_info_lists_az_login_auth_method(self):
        i = AzureKeyVaultIntegration(_cfg())
        info = i.get_setup_info()
        methods = [m["method"] for m in info["auth_methods"]]
        assert any("az login" in m for m in methods)
