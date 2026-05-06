#!/usr/bin/env python3
"""Unit tests for ConsulIntegration (HashiCorp Consul)."""

from unittest.mock import MagicMock, patch

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.hashicorp_consul import ConsulIntegration
from xyz_platform.integrations.capabilities import IVariableStore, IKVStore
from xyz_platform.models.integration_model import IntegrationModel, IntegrationEndpointsSpecModel


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
