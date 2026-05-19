#!/usr/bin/env python3
"""Unit tests for TerraformIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IInfrastructureTool
from strata.integrations.terraform import TerraformIntegration
from strata.models.integration_model import IntegrationModel


def _cfg(name="terraform") -> IntegrationModel:
    return IntegrationModel(name=name, type="terraform")


class TestTerraformIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_terraform(self):
        i = TerraformIntegration(_cfg())
        assert i.command == "terraform"

    def test_capabilities_include_infrastructure(self):
        assert IInfrastructureTool in TerraformIntegration.CAPABILITIES

    def test_version_command(self):
        i = TerraformIntegration(_cfg())
        assert i.get_version_command() == ["terraform", "version"]


class TestTerraformParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_standard_output(self):
        i = TerraformIntegration(_cfg())
        assert i.parse_version("Terraform v1.5.7") == "1.5.7"

    def test_parse_with_extra_lines(self):
        i = TerraformIntegration(_cfg())
        output = "Terraform v1.6.0\non linux_amd64"
        assert i.parse_version(output) == "1.6.0"

    def test_parse_fallback_returns_stripped(self):
        i = TerraformIntegration(_cfg())
        result = i.parse_version("  no-version  ")
        assert result == "no-version"


class TestTerraformIntegrationSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_name_same_instance(self):
        a = TerraformIntegration(_cfg("tf1"))
        b = TerraformIntegration(_cfg("tf1"))
        assert a is b

    def test_different_names_different_instances(self):
        a = TerraformIntegration(_cfg("tf1"))
        BaseIntegration._instances.clear()
        b = TerraformIntegration(_cfg("tf2"))
        assert a is not b


class TestTerraformIntegrationAvailability:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = TerraformIntegration(_cfg())
        i._is_available = True
        i._version = "1.5.7"
        ok, msg = i.ensure_available()
        assert ok
        assert msg == ""

    def test_ensure_available_not_installed(self):
        i = TerraformIntegration(_cfg())
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("strata.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.ensure_available()
        assert not ok
