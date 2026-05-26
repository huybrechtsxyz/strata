#!/usr/bin/env python3
"""Unit tests for FlagsmithIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IFeatureStore
from strata.integrations.flagsmith import FlagsmithIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="flagsmith", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="flagsmith", endpoints=endpoints)


class TestFlagsmithIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_flagsmith(self):
        i = FlagsmithIntegration(_cfg())
        assert i.command == "flagsmith"

    def test_capabilities(self):
        assert IFeatureStore in FlagsmithIntegration.CAPABILITIES

    def test_address_from_endpoint(self):
        i = FlagsmithIntegration(_cfg(address="https://flagsmith.example.com"))
        assert i.flagsmith_addr == "https://flagsmith.example.com"

    def test_address_default_cloud(self):
        i = FlagsmithIntegration(_cfg())
        assert i.flagsmith_addr == "https://edge.api.flagsmith.com"

    def test_address_trailing_slash_stripped(self):
        i = FlagsmithIntegration(_cfg(address="https://flagsmith.example.com/"))
        assert not i.flagsmith_addr.endswith("/")


class TestFlagsmithParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_version_returns_api(self):
        i = FlagsmithIntegration(_cfg())
        assert i.get_version() == "api"

    def test_parse_version_semver(self):
        i = FlagsmithIntegration(_cfg())
        assert i.parse_version("flagsmith 1.2.3") == "1.2.3"

    def test_parse_version_no_match_returns_api(self):
        i = FlagsmithIntegration(_cfg())
        assert i.parse_version("no version") == "api"


class TestFlagsmithSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = FlagsmithIntegration(_cfg(address="https://flagsmith.example.com"))
        b = FlagsmithIntegration(_cfg(address="https://flagsmith.example.com"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = FlagsmithIntegration(_cfg(address="https://flagsmith1.example.com"))
        BaseIntegration._instances.clear()
        b = FlagsmithIntegration(_cfg(address="https://flagsmith2.example.com"))
        assert a is not b


class TestFlagsmithIsAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_not_available_without_env_key(self):
        i = FlagsmithIntegration(_cfg())
        assert i.is_available() is False

    def test_is_available_checks_api_not_binary(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert i.is_available() is True

    def test_is_available_returns_false_on_http_error(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            assert i.is_available() is False


class TestFlagsmithEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_fails_without_env_key(self):
        i = FlagsmithIntegration(_cfg())
        ok, msg = i.ensure_available()
        assert not ok
        assert "key" in msg.lower() or "flagsmith" in msg.lower()

    def test_fails_when_api_unreachable(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            ok, msg = i.ensure_available()
        assert not ok
        assert "not reachable" in msg.lower() or "api" in msg.lower()

    def test_succeeds_when_key_set_and_api_available(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        with patch.object(i, "is_available", return_value=True):
            ok, msg = i.ensure_available()
        assert ok
        assert msg == ""


class TestFlagsmithGetFeature:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_feature_no_key_returns_none(self):
        i = FlagsmithIntegration(_cfg())
        assert i.get_feature("my-flag") is None

    def test_get_feature_returns_enabled_state(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        flags = [
            {"feature": {"name": "dark-mode"}, "enabled": True, "feature_state_value": None},
            {"feature": {"name": "beta-ui"}, "enabled": False, "feature_state_value": None},
        ]
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "_fetch_flags", return_value=flags):
                assert i.get_feature("dark-mode") is True
                assert i.get_feature("beta-ui") is False

    def test_get_feature_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        flags = [
            {"feature": {"name": "max-items"}, "enabled": True, "feature_state_value": "50"},
        ]
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "_fetch_flags", return_value=flags):
                assert i.get_feature("max-items") == "50"

    def test_get_feature_returns_none_when_flag_not_found(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "_fetch_flags", return_value=[]):
                assert i.get_feature("unknown-flag") is None


class TestFlagsmithSetFeature:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_set_feature_always_returns_false(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        with patch.object(i, "is_available", return_value=True):
            assert i.set_feature("my-flag", True) is False

    def test_set_feature_no_key_also_returns_false(self):
        i = FlagsmithIntegration(_cfg())
        assert i.set_feature("my-flag", True) is False


class TestFlagsmithListFeatures:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_list_features_no_key_returns_empty(self):
        i = FlagsmithIntegration(_cfg())
        assert i.list_features() == []

    def test_list_features_returns_all_names(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        flags = [
            {"feature": {"name": "dark-mode"}, "enabled": True},
            {"feature": {"name": "beta-ui"}, "enabled": False},
        ]
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "_fetch_flags", return_value=flags):
                result = i.list_features()
        assert "dark-mode" in result
        assert "beta-ui" in result

    def test_list_features_with_prefix_filter(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        flags = [
            {"feature": {"name": "payment-v2"}, "enabled": True},
            {"feature": {"name": "payment-v3"}, "enabled": True},
            {"feature": {"name": "dark-mode"}, "enabled": False},
        ]
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "_fetch_flags", return_value=flags):
                result = i.list_features(prefix="payment-")
        assert result == ["payment-v2", "payment-v3"]


class TestFlagsmithFetchFlags:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_flags_cached_after_first_fetch(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        flags = [{"feature": {"name": "flag1"}, "enabled": True}]
        with patch.object(i, "_fetch_flags", wraps=i._fetch_flags):
            i._flags_cache = flags
            result = i._fetch_flags()
        assert result == flags

    def test_refresh_bypasses_cache(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg())
        i._flags_cache = [{"feature": {"name": "stale"}, "enabled": False}]
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = i._fetch_flags(refresh=True)
        # Cache bypassed, network failed → returns empty
        assert result == []


class TestFlagsmithGetInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_info_contains_expected_fields(self, monkeypatch):
        monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "test.env.key")
        BaseIntegration._instances.clear()
        i = FlagsmithIntegration(_cfg(address="https://flagsmith.example.com"))
        info = i.get_info()
        assert info["flagsmith_addr"] == "https://flagsmith.example.com"
        assert info["has_env_key"] is True

    def test_has_env_key_false_without_env(self):
        i = FlagsmithIntegration(_cfg())
        assert i.get_info()["has_env_key"] is False


class TestFlagsmithGetSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_has_required_fields(self):
        i = FlagsmithIntegration(_cfg())
        info = i.get_setup_info()
        assert info["name"] == "flagsmith"
        env_names = [e["name"] for e in info["env_vars"]]
        assert "FLAGSMITH_ENVIRONMENT_KEY" in env_names
