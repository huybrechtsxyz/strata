#!/usr/bin/env python3
"""Unit tests for AzureAppConfigIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.azure_appconfig import AzureAppConfigIntegration
from strata.integrations.base_integration import BaseIntegration
from strata.models.capabilities import IFeatureStore, IVariableStore
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


class TestAzureAppConfigBulkValueCache:
    """ADR-0026: Azure App Configuration's list API already returns the value
    field for every entry in the same call keys are listed with — get_variable()
    should warm a whole-namespace cache once and serve every subsequent call
    from it. Feature flags (stored under a .appconfig.featureflag/ prefix in the
    same namespace) share the same cache."""

    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_second_get_variable_call_does_not_refetch(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(i, "_fetch_all_keyvalues", return_value={"key1": "val1", "key2": "val2"}) as mock_fetch:
            assert i.get_variable("key1") == "val1"
            assert i.get_variable("key2") == "val2"
        mock_fetch.assert_called_once()

    def test_get_feature_shares_the_same_cache_as_get_variable(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(
            i,
            "_fetch_all_keyvalues",
            return_value={"key1": "val1", ".appconfig.featureflag/dark-mode": '{"enabled": true}'},
        ) as mock_fetch:
            assert i.get_variable("key1") == "val1"
            assert i.get_feature("dark-mode") is True
        mock_fetch.assert_called_once()

    def test_different_label_bypasses_cache(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(
            i, "_fetch_all_keyvalues", side_effect=[{"key1": "prod-val"}, {"key1": "staging-val"}]
        ) as mock_fetch:
            assert i.get_variable("key1", label="prod") == "prod-val"
            assert i.get_variable("key1", label="staging") == "staging-val"
        assert mock_fetch.call_count == 2

    def test_missing_key_returns_none_without_extra_call(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(i, "_fetch_all_keyvalues", return_value={"key1": "val1"}):
            with patch.object(i, "_get_value") as mock_direct:
                assert i.get_variable("missing") is None
        mock_direct.assert_not_called()

    def test_bulk_fetch_failure_falls_back_to_direct_get(self):
        i = AzureAppConfigIntegration(_cfg(address="https://ac.azconfig.io"))
        with patch.object(i, "_fetch_all_keyvalues", return_value=None):
            with patch.object(i, "_get_value", return_value="direct-value") as mock_direct:
                result = i.get_variable("key1")
        assert result == "direct-value"
        mock_direct.assert_called_once()
