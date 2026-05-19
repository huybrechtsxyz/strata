#!/usr/bin/env python3
"""Unit tests for StoreIntegration."""

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.store_integration import StoreIntegration
from strata.models.integration_model import IntegrationModel


def _cfg(name="store", itype="custom_store") -> IntegrationModel:
    return IntegrationModel(name=name, type=itype)


class ConcreteStore(StoreIntegration):
    COMMAND = "echo"

    def get_version_command(self):
        return ["echo", "--version"]

    def parse_version(self, o):
        return o.strip()


class TestStoreIntegrationDefaults:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_variable_returns_none_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.get_variable("key") is None

    def test_set_variable_returns_false_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.set_variable("key", "val") is False

    def test_list_variables_returns_empty_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.list_variables() == []

    def test_get_secret_returns_none_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.get_secret("key") is None

    def test_set_secret_returns_false_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.set_secret("key", "val") is False

    def test_list_secrets_returns_empty_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.list_secrets() == []

    def test_get_feature_returns_none_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.get_feature("flag") is None

    def test_set_feature_returns_false_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.set_feature("flag", True) is False

    def test_list_features_returns_empty_by_default(self):
        i = ConcreteStore(_cfg())
        assert i.list_features() == []


class TestStoreIntegrationOverrides:
    """Test that subclass overrides work correctly."""

    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_override_get_variable(self):
        class MyStore(ConcreteStore):
            def get_variable(self, key, **kwargs):
                return f"value_for_{key}"

        BaseIntegration._instances.clear()
        i = MyStore(_cfg("mystore"))
        assert i.get_variable("mykey") == "value_for_mykey"

    def test_override_get_secret(self):
        class MySecrets(ConcreteStore):
            def get_secret(self, key, **kwargs):
                return "topsecret"

        BaseIntegration._instances.clear()
        i = MySecrets(_cfg("mysecrets"))
        assert i.get_secret("password") == "topsecret"
