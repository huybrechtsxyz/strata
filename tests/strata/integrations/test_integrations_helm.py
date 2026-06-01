"""Unit tests for HelmIntegration."""

from unittest.mock import patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IInfrastructureTool
from strata.integrations.helm import HelmIntegration
from strata.models.integration_model import IntegrationModel


def _make_integration(name: str = "helm") -> HelmIntegration:
    return HelmIntegration(config=IntegrationModel(name=name, type="helm"))


class TestHelmIntegrationMetadata:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_helm(self):
        assert HelmIntegration.COMMAND == "helm"

    def test_capabilities_include_infrastructure(self):
        assert IInfrastructureTool in HelmIntegration.CAPABILITIES

    def test_version_command_returns_helm_version(self):
        i = _make_integration()
        assert i.get_version_command() == ["helm", "version"]


class TestHelmIntegrationParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_buildinfo_format(self):
        i = _make_integration()
        result = i.parse_version('version.BuildInfo{Version:"v3.14.0",GitCommit:"abc123def456"}')
        assert result == "3.14.0"

    def test_parse_v_prefix_only(self):
        i = _make_integration()
        assert i.parse_version("v3.12.3") == "3.12.3"

    def test_parse_plain_semver(self):
        i = _make_integration()
        assert i.parse_version("3.11.0") == "3.11.0"

    def test_parse_no_semver_returns_stripped(self):
        i = _make_integration()
        assert i.parse_version("  helm not found  ") == "helm not found"


class TestHelmIntegrationEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = _make_integration()
        with (
            patch.object(i, "is_available", return_value=True),
            patch.object(i, "validate_version", return_value=(True, "")),
            patch.object(i, "get_version", return_value="3.14.0"),
        ):
            ok, msg = i.ensure_available()
        assert ok is True

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


class TestHelmIntegrationSetupInfo:
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

    def test_setup_info_install_url_is_helm_docs(self):
        i = _make_integration()
        assert "helm.sh" in i.get_setup_info()["install_url"]
