#!/usr/bin/env python3
"""Unit tests for IntegrationFactory."""

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.factory import IntegrationFactory
from strata.models.integration_model import IntegrationModel


class ConcreteA(BaseIntegration):
    COMMAND = "tool_a"

    def get_version_command(self):
        return ["tool_a", "--version"]

    def parse_version(self, o):
        return o.strip()


class ConcreteB(BaseIntegration):
    COMMAND = "tool_b"

    def get_version_command(self):
        return ["tool_b", "--version"]

    def parse_version(self, o):
        return o.strip()


def _cfg(name="test", itype="tool_a") -> IntegrationModel:
    return IntegrationModel(name=name, type=itype)


class TestIntegrationFactoryRegister:
    def setup_method(self):
        BaseIntegration._instances.clear()
        IntegrationFactory._type_mapping.clear()

    def test_register_type(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        assert IntegrationFactory.is_type_registered("tool_a")

    def test_unregister_type(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        IntegrationFactory.unregister_type("tool_a")
        assert not IntegrationFactory.is_type_registered("tool_a")

    def test_unregister_nonexistent_is_safe(self):
        IntegrationFactory.unregister_type("nonexistent")  # no error

    def test_get_registered_types(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        IntegrationFactory.register_type("tool_b", ConcreteB)
        types = IntegrationFactory.get_registered_types()
        assert "tool_a" in types
        assert "tool_b" in types


class TestIntegrationFactoryCreate:
    def setup_method(self):
        BaseIntegration._instances.clear()
        IntegrationFactory._type_mapping.clear()

    def test_create_returns_instance(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        instance = IntegrationFactory.create(_cfg("t", "tool_a"))
        assert isinstance(instance, ConcreteA)

    def test_create_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError, match="not registered"):
            IntegrationFactory.create(_cfg("t", "unknown_type"))

    def test_create_same_config_returns_same_singleton(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        cfg = _cfg("t", "tool_a")
        a = IntegrationFactory.create(cfg)
        b = IntegrationFactory.create(cfg)
        assert a is b

    def test_create_different_names_different_instances(self):
        IntegrationFactory.register_type("tool_a", ConcreteA)
        a = IntegrationFactory.create(_cfg("alpha", "tool_a"))
        BaseIntegration._instances.clear()
        b = IntegrationFactory.create(_cfg("beta", "tool_a"))
        assert a is not b
