"""Unit tests for AnsibleIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.ansible import AnsibleIntegration
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IInfrastructureTool
from strata.models.integration_model import IntegrationModel


def _cfg(name="ansible") -> IntegrationModel:
    return IntegrationModel(name=name, type="ansible")


class TestAnsibleIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_ansible_playbook(self):
        i = AnsibleIntegration(_cfg())
        assert i.command == "ansible-playbook"

    def test_capabilities_include_infrastructure(self):
        assert IInfrastructureTool in AnsibleIntegration.CAPABILITIES

    def test_version_command(self):
        i = AnsibleIntegration(_cfg())
        assert i.get_version_command() == ["ansible-playbook", "--version"]


class TestAnsibleParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_standard_output(self):
        i = AnsibleIntegration(_cfg())
        assert i.parse_version("ansible-playbook [core 2.15.4]") == "2.15.4"

    def test_parse_with_extra_text(self):
        i = AnsibleIntegration(_cfg())
        output = "ansible-playbook [core 2.17.0]\n  config file = /etc/ansible/ansible.cfg"
        assert i.parse_version(output) == "2.17.0"

    def test_parse_fallback_returns_stripped(self):
        i = AnsibleIntegration(_cfg())
        result = i.parse_version("  no-version-info  ")
        assert result == "no-version-info"


class TestAnsibleIntegrationSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_name_same_instance(self):
        a = AnsibleIntegration(_cfg("ansible1"))
        b = AnsibleIntegration(_cfg("ansible1"))
        assert a is b

    def test_different_names_different_instances(self):
        a = AnsibleIntegration(_cfg("ansible1"))
        BaseIntegration._instances.clear()
        b = AnsibleIntegration(_cfg("ansible2"))
        assert a is not b


class TestAnsibleIntegrationAvailability:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_success(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        ok, msg = i.ensure_available()
        assert ok
        assert msg == ""

    def test_ensure_available_not_installed(self):
        i = AnsibleIntegration(_cfg())
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("strata.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.ensure_available()
        assert not ok
        assert "not installed" in msg or "not in PATH" in msg


class TestAnsibleIntegrationSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_returns_dict(self):
        i = AnsibleIntegration(_cfg())
        info = i.get_setup_info()
        assert info["name"] == "ansible"
        assert info["command"] == "ansible-playbook"
        assert "install_url" in info
        assert len(info["env_vars"]) > 0
        assert len(info["auth_methods"]) > 0

    def test_setup_info_has_yaml_example(self):
        i = AnsibleIntegration(_cfg())
        info = i.get_setup_info()
        assert "yaml_example" in info
        assert "ansible" in info["yaml_example"]


class TestAnsibleIntegrationInitCommand:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_init_skips_when_no_requirements(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        result = i.init("/work", requirements_file=None)
        assert result["returncode"] == 0

    def test_init_calls_galaxy_install(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        mock_result = MagicMock(returncode=0, stdout="installed", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result) as mock_run:
            i.init("/work", requirements_file="requirements.yml")
        args = mock_run.call_args[0][0]
        assert "ansible-galaxy" in args
        assert "requirements.yml" in args

    def test_init_raises_when_unavailable(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = False
        i._version = None
        try:
            i.init("/work", requirements_file="requirements.yml")
            raise AssertionError("Should have raised")
        except RuntimeError as exc:
            assert "not available" in str(exc)


class TestAnsibleIntegrationPlan:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_plan_builds_check_diff_command(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result) as mock_run:
            i.plan("/work", playbook="deploy.yml", inventory="hosts.yml")
        args = mock_run.call_args[0][0]
        assert "ansible-playbook" in args
        assert "deploy.yml" in args
        assert "--check" in args
        assert "--diff" in args
        assert "-i" in args
        assert "hosts.yml" in args

    def test_plan_with_extra_vars(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result) as mock_run:
            i.plan("/work", extra_vars={"env": "prod"})
        args = mock_run.call_args[0][0]
        assert "-e" in args
        assert "env=prod" in args


class TestAnsibleIntegrationApply:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_apply_builds_playbook_command(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result) as mock_run:
            i.apply("/work", playbook="configure.yml")
        args = mock_run.call_args[0][0]
        assert "ansible-playbook" in args
        assert "configure.yml" in args
        assert "--check" not in args


class TestAnsibleIntegrationSyntaxCheck:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_syntax_check_builds_correct_command(self):
        i = AnsibleIntegration(_cfg())
        i._is_available = True
        i._version = "2.15.4"
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result) as mock_run:
            i.syntax_check("/work", playbook="site.yml")
        args = mock_run.call_args[0][0]
        assert "--syntax-check" in args
        assert "site.yml" in args
