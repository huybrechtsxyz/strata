#!/usr/bin/env python3
"""Unit tests for AzureAppConfigIntegration."""


from xyz_platform.integrations.azure_appconfig import AzureAppConfigIntegration
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.capabilities import IFeatureStore, IVariableStore
from xyz_platform.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


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
