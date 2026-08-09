#!/usr/bin/env python3
"""Unit tests for OpenTofuIntegration."""

from unittest.mock import patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.opentofu import OpenTofuIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.models.capabilities import IInfrastructureTool
from strata.models.integration_model import IntegrationModel


def _cfg(name="opentofu") -> IntegrationModel:
    return IntegrationModel(name=name, type="opentofu")


class TestOpenTofuIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_tofu(self):
        i = OpenTofuIntegration(_cfg())
        assert i.command == "tofu"

    def test_is_subclass_of_terraform(self):
        assert issubclass(OpenTofuIntegration, TerraformIntegration)

    def test_capabilities_include_infrastructure(self):
        assert IInfrastructureTool in OpenTofuIntegration.CAPABILITIES

    def test_version_command_uses_tofu(self):
        i = OpenTofuIntegration(_cfg())
        assert i.get_version_command() == ["tofu", "version"]


class TestOpenTofuParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_opentofu_output(self):
        i = OpenTofuIntegration(_cfg())
        assert i.parse_version("OpenTofu v1.7.0") == "1.7.0"

    def test_parse_output_with_metadata(self):
        i = OpenTofuIntegration(_cfg())
        output = "OpenTofu v1.6.2\non linux_amd64"
        assert i.parse_version(output) == "1.6.2"

    def test_parse_fallback_returns_stripped(self):
        i = OpenTofuIntegration(_cfg())
        assert i.parse_version("  no-version  ") == "no-version"


class TestOpenTofuSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_name_same_instance(self):
        a = OpenTofuIntegration(_cfg("ot1"))
        b = OpenTofuIntegration(_cfg("ot1"))
        assert a is b

    def test_different_names_different_instances(self):
        a = OpenTofuIntegration(_cfg("ot1"))
        BaseIntegration._instances.clear()
        b = OpenTofuIntegration(_cfg("ot2"))
        assert a is not b


class TestOpenTofuEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_not_installed(self):
        i = OpenTofuIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            ok, msg = i.ensure_available()
        assert not ok
        assert "tofu" in msg.lower() or "opentofu" in msg.lower()

    def test_ensure_available_success(self):
        i = OpenTofuIntegration(_cfg())
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="1.7.0"):
                    ok, msg = i.ensure_available()
        assert ok
        assert msg == ""


class TestOpenTofuGetSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_name_is_opentofu(self):
        i = OpenTofuIntegration(_cfg())
        info = i.get_setup_info()
        assert info["name"] == "opentofu"
        assert info["command"] == "tofu"

    def test_setup_info_install_url_is_opentofu_org(self):
        i = OpenTofuIntegration(_cfg())
        info = i.get_setup_info()
        assert "opentofu.org" in info["install_url"]

    def test_setup_info_has_env_vars(self):
        i = OpenTofuIntegration(_cfg())
        info = i.get_setup_info()
        env_names = [e["name"] for e in info["env_vars"]]
        assert "TERRAFORM_API_TOKEN" in env_names
