#!/usr/bin/env python3
"""Unit tests for GitIntegration."""

from unittest.mock import MagicMock, patch

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.capabilities import IRepositoryTool
from xyz_platform.integrations.git import GitIntegration
from xyz_platform.models.integration_model import IntegrationModel


def _cfg(name="git") -> IntegrationModel:
    return IntegrationModel(name=name, type="git")


class TestGitIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_git(self):
        i = GitIntegration(_cfg())
        assert i.command == "git"

    def test_integration_name(self):
        i = GitIntegration(_cfg("my_git"))
        assert i.integration_name == "my_git"

    def test_capabilities_include_repository(self):
        assert IRepositoryTool in GitIntegration.CAPABILITIES

    def test_version_command(self):
        i = GitIntegration(_cfg())
        assert i.get_version_command() == ["git", "--version"]


class TestGitIntegrationParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_standard_output(self):
        i = GitIntegration(_cfg())
        assert i.parse_version("git version 2.40.0") == "2.40.0"

    def test_parse_windows_output(self):
        i = GitIntegration(_cfg())
        assert i.parse_version("git version 2.39.1.windows.1") == "2.39.1"

    def test_parse_fallback_returns_stripped(self):
        i = GitIntegration(_cfg())
        assert i.parse_version("  no-version-here  ") == "no-version-here"


class TestGitIntegrationSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_name_same_instance(self):
        a = GitIntegration(_cfg("git1"))
        b = GitIntegration(_cfg("git1"))
        assert a is b

    def test_different_names_different_instances(self):
        a = GitIntegration(_cfg("git1"))
        BaseIntegration._instances.clear()
        b = GitIntegration(_cfg("git2"))
        assert a is not b


class TestGitIntegrationAvailability:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"
        ok, msg = i.ensure_available()
        assert ok
        assert msg == ""

    def test_ensure_available_not_installed(self):
        i = GitIntegration(_cfg())
        mock_result = MagicMock(returncode=1, stdout="", stderr="command not found")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.ensure_available()
        assert not ok
        assert msg != ""
