#!/usr/bin/env python3
"""Unit tests for AzureAppConfigIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.azure_appconfig import AzureAppConfigIntegration
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IFeatureStore, IVariableStore
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="appconfig", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="azure_appconfig", endpoints=endpoints)


class TestAzureAppConfigInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_az(self):
        i = AzureAppConfigIntegration(_cfg())
        assert i.command == "az"

    def test_capabilities(self):
        assert IVariableStore in AzureAppConfigIntegration.CAPABILITIES
        assert IFeatureStore in AzureAppConfigIntegration.CAPABILITIES

    def test_version_command(self):
        i = AzureAppConfigIntegration(_cfg())
        assert i.get_version_command() == ["az", "version"]

    def test_endpoint_from_config(self):
        i = AzureAppConfigIntegration(_cfg(address="https://myappconfig.azconfig.io"))
        assert "myappconfig" in i.appconfig_endpoint

    def test_endpoint_empty_when_no_address(self):
        i = AzureAppConfigIntegration(_cfg())
        assert i.appconfig_endpoint == ""

    def test_endpoint_trailing_slash_added(self):
        i = AzureAppConfigIntegration(_cfg(address="https://myappconfig.azconfig.io"))
        assert i.appconfig_endpoint.endswith("/")


class TestAzureAppConfigParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_az_version(self):
        i = AzureAppConfigIntegration(_cfg())
        result = i.parse_version("azure-cli 2.50.0")
        assert "2.50.0" in result


class TestAzureAppConfigSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_endpoint_same_instance(self):
        a = AzureAppConfigIntegration(_cfg(address="https://ac1.azconfig.io"))
        b = AzureAppConfigIntegration(_cfg(address="https://ac1.azconfig.io"))
        assert a is b

    def test_different_endpoints_different_instances(self):
        a = AzureAppConfigIntegration(_cfg(address="https://ac1.azconfig.io"))
        BaseIntegration._instances.clear()
        b = AzureAppConfigIntegration(_cfg(address="https://ac2.azconfig.io"))
        assert a is not b


class TestAzureAppConfigGetVariable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_variable_no_endpoint_returns_none(self):
        i = AzureAppConfigIntegration(_cfg())
        result = i.get_variable("my/key")
        assert result is None

    def test_get_feature_no_endpoint_returns_none(self):
        i = AzureAppConfigIntegration(_cfg())
        result = i.get_feature("my-flag")
        assert result is None

    def test_list_variables_no_endpoint_returns_empty(self):
        i = AzureAppConfigIntegration(_cfg())
        result = i.list_variables()
        assert result == []


class TestAzureAppConfigCliLogin:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_is_cli_logged_in_true_when_account_show_succeeds(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        proc = MagicMock()
        proc.returncode = 0
        with patch.object(i, "_run_integration", return_value=proc):
            assert i._is_cli_logged_in() is True

    def test_is_cli_logged_in_false_when_account_show_fails(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        proc = MagicMock()
        proc.returncode = 1
        with patch.object(i, "_run_integration", return_value=proc):
            assert i._is_cli_logged_in() is False

    def test_is_cli_logged_in_false_on_exception(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(i, "_run_integration", side_effect=OSError("no az")):
            assert i._is_cli_logged_in() is False

    def test_ensure_available_accepts_cli_login_with_no_sp_env_vars(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "_is_cli_logged_in", return_value=True),
        ):
            ok, msg = i.ensure_available()
        assert ok is True
        assert msg == ""

    def test_ensure_available_rejects_when_no_auth_at_all(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "_is_cli_logged_in", return_value=False),
        ):
            ok, msg = i.ensure_available()
        assert ok is False
        assert "az login" in msg

    def test_setup_info_lists_az_login_auth_method(self):
        i = AzureAppConfigIntegration(_cfg())
        info = i.get_setup_info()
        methods = [m["method"] for m in info["auth_methods"]]
        assert any("az login" in m for m in methods)
