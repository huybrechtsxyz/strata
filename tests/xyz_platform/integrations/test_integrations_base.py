#!/usr/bin/env python3
"""Unit tests for BaseIntegration."""

from unittest.mock import MagicMock, patch

from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.models.integration_model import IntegrationModel

# ---------------------------------------------------------------------------
# Concrete subclass for testing (not abstract)
# ---------------------------------------------------------------------------

class ConcreteIntegration(BaseIntegration):
    COMMAND = "echo"

    def get_version_command(self):
        return ["echo", "--version"]

    def parse_version(self, output: str) -> str:
        return output.strip()


def _make_config(name="test", itype="echo", **kwargs) -> IntegrationModel:
    return IntegrationModel(name=name, type=itype, **kwargs)


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------

class TestBaseIntegrationSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_class_returns_same_instance(self):
        cfg = _make_config()
        a = ConcreteIntegration(cfg)
        b = ConcreteIntegration(cfg)
        assert a is b

    def test_reset_instances_gives_new_instance(self):
        cfg = _make_config()
        a = ConcreteIntegration(cfg)
        BaseIntegration._instances.clear()
        b = ConcreteIntegration(cfg)
        assert a is not b


# ---------------------------------------------------------------------------
# Initialisation attributes
# ---------------------------------------------------------------------------

class TestBaseIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_integration_name_from_config(self):
        cfg = _make_config(name="my_git")
        i = ConcreteIntegration(cfg)
        assert i.integration_name == "my_git"

    def test_integration_type_from_config(self):
        cfg = _make_config(itype="git")
        i = ConcreteIntegration(cfg)
        assert i.integration_type == "git"

    def test_command_from_class_attribute(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i.command == "echo"

    def test_command_fallback_to_type_when_no_class_attr(self):
        class NoCommandIntegration(BaseIntegration):
            def get_version_command(self): return ["mytool", "--version"]
            def parse_version(self, o): return o.strip()

        cfg = _make_config(itype="mytool")
        i = NoCommandIntegration(cfg)
        assert i.command == "mytool"

    def test_initial_state_none(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i._is_available is None
        assert i._version is None


# ---------------------------------------------------------------------------
# _get_command_from_config: validation.command path
# ---------------------------------------------------------------------------

class TestGetCommandFromConfig:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_validation_command_takes_first_word(self):
        from xyz_platform.models.integration_model import IntegrationValidationSpecModel

        class CustomIntegration(BaseIntegration):
            def get_version_command(self): return ["customtool", "--version"]
            def parse_version(self, o): return o.strip()

        cfg = _make_config(
            itype="customtool",
            validation=IntegrationValidationSpecModel(command="customtool --version"),
        )
        i = CustomIntegration(cfg)
        assert i.command == "customtool"


# ---------------------------------------------------------------------------
# _get_env_var / _resolve_env_vars
# ---------------------------------------------------------------------------

class TestEnvVarHelpers:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_env_var_returns_value(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i._get_env_var("MY_VAR") == "hello"

    def test_get_env_var_returns_default(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i._get_env_var("__MISSING_VAR__", "fallback") == "fallback"

    def test_resolve_env_vars_curly(self, monkeypatch):
        monkeypatch.setenv("HOST", "myhost")
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i._resolve_env_vars("https://${HOST}/api") == "https://myhost/api"

    def test_resolve_env_vars_dollar(self, monkeypatch):
        monkeypatch.setenv("PORT", "8080")
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        assert i._resolve_env_vars("http://host:$PORT") == "http://host:8080"

    def test_resolve_env_vars_missing_unchanged(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        result = i._resolve_env_vars("${__TOTALLY_MISSING__}")
        assert result == "${__TOTALLY_MISSING__}"


# ---------------------------------------------------------------------------
# is_available / get_version
# ---------------------------------------------------------------------------

class TestAvailabilityAndVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_is_available_true_when_command_succeeds(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        mock_result = MagicMock(returncode=0, stdout="1.0.0", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            assert i.is_available(use_cache=False) is True

    def test_is_available_false_when_command_fails(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            assert i.is_available(use_cache=False) is False

    def test_is_available_false_on_exception(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        with patch("xyz_platform.integrations.base_integration.run_command", side_effect=FileNotFoundError):
            assert i.is_available(use_cache=False) is False

    def test_is_available_uses_cache(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        i._is_available = True
        # Should return cached value without calling run_command
        with patch("xyz_platform.integrations.base_integration.run_command") as mock_cmd:
            assert i.is_available() is True
            mock_cmd.assert_not_called()

    def test_get_version_returns_parsed_string(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        i._is_available = True
        mock_result = MagicMock(returncode=0, stdout="  2.40.0  ", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            assert i.get_version(use_cache=False) == "2.40.0"

    def test_get_version_none_when_unavailable(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        i._is_available = False
        assert i.get_version(use_cache=False) is None


# ---------------------------------------------------------------------------
# validate_version
# ---------------------------------------------------------------------------

class TestValidateVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_no_validation_spec_always_valid(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        ok, msg = i.validate_version()
        assert ok
        assert msg == ""

    def test_version_meets_minimum(self):
        from xyz_platform.models.integration_model import IntegrationValidationSpecModel
        cfg = _make_config(validation=IntegrationValidationSpecModel(command="echo --version", min_version="1.0.0"))
        i = ConcreteIntegration(cfg)
        i._is_available = True
        mock_result = MagicMock(returncode=0, stdout="2.0.0", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.validate_version()
        assert ok

    def test_version_below_minimum_fails(self):
        from xyz_platform.models.integration_model import IntegrationValidationSpecModel
        cfg = _make_config(validation=IntegrationValidationSpecModel(command="echo --version", min_version="3.0.0"))
        i = ConcreteIntegration(cfg)
        i._is_available = True
        mock_result = MagicMock(returncode=0, stdout="2.0.0", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.validate_version()
        assert not ok
        assert "below minimum" in msg

    def test_version_above_maximum_fails(self):
        from xyz_platform.models.integration_model import IntegrationValidationSpecModel
        cfg = _make_config(validation=IntegrationValidationSpecModel(command="echo --version", max_version="1.9.9"))
        i = ConcreteIntegration(cfg)
        i._is_available = True
        mock_result = MagicMock(returncode=0, stdout="2.0.0", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result):
            ok, msg = i.validate_version()
        assert not ok
        assert "above maximum" in msg


# ---------------------------------------------------------------------------
# get_info
# ---------------------------------------------------------------------------

class TestGetInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_info_returns_dict(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        i._is_available = False
        info = i.get_info()
        assert info["name"] == "test"
        assert info["type"] == "echo"
        assert info["command"] == "echo"
        assert "available" in info
        assert "version" in info


# ---------------------------------------------------------------------------
# _run_integration
# ---------------------------------------------------------------------------

class TestRunIntegration:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_run_integration_prepends_command(self):
        cfg = _make_config()
        i = ConcreteIntegration(cfg)
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("xyz_platform.integrations.base_integration.run_command", return_value=mock_result) as mock_cmd:
            i._run_integration(["arg1", "arg2"])
            called_cmd = mock_cmd.call_args[0][0]
            assert called_cmd == ["echo", "arg1", "arg2"]
