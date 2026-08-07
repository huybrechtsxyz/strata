"""Unit tests for DockerIntegration."""

from unittest.mock import patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.docker import DockerIntegration
from strata.models.capabilities import IContainerTool
from strata.models.integration_model import IntegrationModel


def _make_integration(name: str = "docker") -> DockerIntegration:
    return DockerIntegration(config=IntegrationModel(name=name, type="docker"))


class TestDockerIntegrationMetadata:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_docker(self):
        assert DockerIntegration.COMMAND == "docker"

    def test_capabilities_include_container(self):
        assert IContainerTool in DockerIntegration.CAPABILITIES

    def test_version_command_returns_docker_version_flag(self):
        i = _make_integration()
        assert i.get_version_command() == ["docker", "--version"]


class TestDockerIntegrationParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_standard_format(self):
        i = _make_integration()
        result = i.parse_version("Docker version 24.0.7, build afdd53b")
        assert result == "24.0.7"

    def test_parse_plain_semver(self):
        i = _make_integration()
        assert i.parse_version("24.0.5") == "24.0.5"

    def test_parse_no_semver_returns_stripped(self):
        i = _make_integration()
        assert i.parse_version("  docker not found  ") == "docker not found"


class TestDockerIntegrationEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = _make_integration()
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "get_version", return_value="24.0.7"),
        ):
            ok, msg = i.ensure_available()
        assert ok is True
        assert msg == ""

    def test_ensure_available_not_installed(self):
        i = _make_integration()
        with patch.object(i, "is_available", return_value=False):
            ok, msg = i.ensure_available()
        assert ok is False
        assert "not in PATH" in msg or "not installed" in msg

    def test_ensure_available_version_invalid(self):
        i = _make_integration()
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(False, "version too old")),
        ):
            ok, msg = i.ensure_available()
        assert ok is False
        assert msg == "version too old"


class TestDockerIntegrationSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_returns_dict(self):
        i = _make_integration()
        assert isinstance(i.get_setup_info(), dict)

    def test_setup_info_has_required_keys(self):
        i = _make_integration()
        info = i.get_setup_info()
        for key in ("name", "command", "install_url", "env_vars", "auth_methods"):
            assert key in info

    def test_setup_info_install_url_is_docker_docs(self):
        i = _make_integration()
        assert "docker" in i.get_setup_info()["install_url"].lower()
