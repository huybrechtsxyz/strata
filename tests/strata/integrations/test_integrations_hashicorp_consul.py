#!/usr/bin/env python3
"""Unit tests for ConsulIntegration (HashiCorp Consul)."""

from unittest.mock import patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.hashicorp_consul import ConsulIntegration
from strata.models.capabilities import IFeatureStore, IKVStore, IVariableStore
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="consul", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="consul", endpoints=endpoints)


class TestConsulIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_consul(self):
        i = ConsulIntegration(_cfg())
        assert i.command == "consul"

    def test_capabilities(self):
        assert IVariableStore in ConsulIntegration.CAPABILITIES
        assert IKVStore in ConsulIntegration.CAPABILITIES
        assert IFeatureStore in ConsulIntegration.CAPABILITIES

    def test_version_command(self):
        i = ConsulIntegration(_cfg())
        assert i.get_version_command() == ["consul", "version"]

    def test_consul_address_from_endpoint(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com:8500"))
        assert i.consul_addr == "http://consul.example.com:8500"

    def test_consul_address_default_when_no_endpoint(self):
        i = ConsulIntegration(_cfg())
        assert i.consul_addr == "http://127.0.0.1:8500"


class TestConsulParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_consul_version(self):
        i = ConsulIntegration(_cfg())
        assert i.parse_version("Consul v1.16.0") == "1.16.0"

    def test_parse_no_version_fallback(self):
        i = ConsulIntegration(_cfg())
        result = i.parse_version("no version here")
        assert result == "no version here"


class TestConsulSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = ConsulIntegration(_cfg(address="http://consul.example.com:8500"))
        b = ConsulIntegration(_cfg(address="http://consul.example.com:8500"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = ConsulIntegration(_cfg(address="http://consul1.example.com"))
        BaseIntegration._instances.clear()
        b = ConsulIntegration(_cfg(address="http://consul2.example.com"))
        assert a is not b


class TestConsulGetVariable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_variable_no_address_returns_none(self):
        i = ConsulIntegration(_cfg())
        result = i.get_variable("myapp/config/debug")
        assert result is None

    def test_get_kv_no_address_returns_none(self):
        i = ConsulIntegration(_cfg())
        result = i.get_kv("myapp/config/debug")
        assert result is None


class TestConsulFeatureStore:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_feature_not_available_returns_none(self):
        i = ConsulIntegration(_cfg())
        assert i.get_feature("my-flag") is None

    def test_get_feature_true(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "get_variable", return_value="true"):
            assert i.get_feature("my-flag") is True

    def test_get_feature_false(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "get_variable", return_value="false"):
            assert i.get_feature("my-flag") is False

    def test_get_feature_not_found_returns_none(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "get_variable", return_value=None):
            assert i.get_feature("my-flag") is None

    def test_get_feature_uses_features_prefix(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        captured = {}
        with patch.object(i, "get_variable", side_effect=lambda k, **kw: captured.update({"key": k}) or "true"):
            i.get_feature("dark-mode")
        assert captured["key"] == "features/dark-mode"

    def test_get_feature_custom_prefix(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        captured = {}
        with patch.object(i, "get_variable", side_effect=lambda k, **kw: captured.update({"key": k}) or "true"):
            i.get_feature("dark-mode", features_path="flags")
        assert captured["key"] == "flags/dark-mode"

    def test_set_feature_delegates_to_set_variable_with_prefix(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        captured = {}
        with patch.object(
            i, "set_variable", side_effect=lambda k, v, **kw: captured.update({"key": k, "val": v}) or True
        ):
            result = i.set_feature("dark-mode", True)
        assert result is True
        assert captured["key"] == "features/dark-mode"
        assert captured["val"] == "true"

    def test_set_feature_false_value(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        captured = {}
        with patch.object(i, "set_variable", side_effect=lambda k, v, **kw: captured.update({"val": v}) or True):
            i.set_feature("dark-mode", False)
        assert captured["val"] == "false"

    def test_list_features_strips_prefix(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "list_variables", return_value=["features/dark-mode", "features/beta-ui"]):
            result = i.list_features()
        assert result == ["dark-mode", "beta-ui"]

    def test_list_features_with_name_prefix(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        captured = {}
        with patch.object(
            i, "list_variables", side_effect=lambda k, **kw: captured.update({"path": k}) or ["features/payment-v2"]
        ):
            i.list_features(prefix="payment-")
        assert captured["path"] == "features/payment-"


class TestConsulBulkValueCache:
    """ADR-0026: Consul's recursive KV read already returns values in the same
    call keys are fetched with — get_variable() should warm a whole-tree cache
    once and serve every subsequent call from it, not one Consul call per key.
    Feature flags (which delegate to get_variable) share the same cache."""

    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_second_get_variable_call_does_not_refetch(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(
            i, "_fetch_all_keyvalues", return_value={"config/a": "1", "features/dark-mode": "true"}
        ) as mock_fetch:
            assert i.get_variable("config/a") == "1"
            assert i.get_feature("dark-mode") is True
        mock_fetch.assert_called_once()

    def test_missing_key_returns_none_without_extra_call(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "_fetch_all_keyvalues", return_value={"config/a": "1"}):
            with patch.object(i, "get_keyvalue") as mock_direct:
                assert i.get_variable("config/missing") is None
        mock_direct.assert_not_called()

    def test_bulk_fetch_failure_falls_back_to_direct_get(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        with patch.object(i, "_fetch_all_keyvalues", return_value=None):
            with patch.object(i, "get_keyvalue", return_value="direct-value") as mock_direct:
                result = i.get_variable("config/a")
        assert result == "direct-value"
        mock_direct.assert_called_once()

    def test_set_variable_invalidates_cache(self):
        i = ConsulIntegration(_cfg(address="http://consul.example.com"))
        # Empty cache so the "never overwrite" existence check reports "not found"
        # and set_variable actually performs a write.
        with patch.object(i, "ensure_available", return_value=(True, "")):
            with patch.object(i, "_fetch_all_keyvalues", return_value={}) as mock_fetch:
                i.get_variable("config/new")  # warm (empty)
                with patch.object(i, "_put_keyvalue", return_value=True):
                    i.set_variable("config/new", "2")
                i.get_variable("config/new")  # should re-warm
            assert mock_fetch.call_count == 2
