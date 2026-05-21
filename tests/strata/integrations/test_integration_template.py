#!/usr/bin/env python3
"""Tests that validate the MyIntegration template compiles and satisfies
the BaseIntegration contract."""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import strata
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.factory import IntegrationFactory
from strata.models.integration_model import IntegrationModel


def _load_template() -> ModuleType:
    """Load my_integration.py from the templates package tree.

    The file lives at strata/templates/solution/dot.strata/integrations/my_integration.py.
    The ``dot.strata`` path component is intentionally not a Python package (it
    uses the dot. prefix convention for the scaffolder), so we load it via
    importlib to avoid requiring it to be importable by name.
    """
    template_path = (
        Path(strata.__file__).parent / "templates" / "solution" / "dot.strata" / "integrations" / "my_integration.py"
    )
    spec = importlib.util.spec_from_file_location("my_integration_template", template_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_tpl = _load_template()
MyIntegration = _tpl.MyIntegration
register = _tpl.register


def _make_config() -> IntegrationModel:
    return IntegrationModel(name="my_integration", type="my_integration")


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestMyIntegrationInstantiation:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_can_be_instantiated_with_integration_model(self):
        config = _make_config()
        instance = MyIntegration(config)
        assert isinstance(instance, MyIntegration)
        assert isinstance(instance, BaseIntegration)


# ---------------------------------------------------------------------------
# Abstract method implementations
# ---------------------------------------------------------------------------


class TestMyIntegrationAbstractMethods:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_version_command_returns_non_empty_list(self):
        instance = MyIntegration(_make_config())
        cmd = instance.get_version_command()
        assert isinstance(cmd, list)
        assert len(cmd) > 0

    def test_parse_version_returns_string_given_sample_output(self):
        instance = MyIntegration(_make_config())
        result = instance.parse_version("my-tool version 1.2.3")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# get_setup_info — required keys
# ---------------------------------------------------------------------------


class TestMyIntegrationSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_setup_info_returns_dict_with_required_keys(self):
        instance = MyIntegration(_make_config())
        info = instance.get_setup_info()
        assert isinstance(info, dict)
        required_keys = {"name", "command", "install_url", "env_vars", "auth_methods", "yaml_example"}
        assert required_keys <= info.keys(), f"Missing keys: {required_keys - info.keys()}"


# ---------------------------------------------------------------------------
# ensure_available — return type contract
# ---------------------------------------------------------------------------


class TestMyIntegrationEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_ensure_available_returns_bool_str_tuple(self):
        instance = MyIntegration(_make_config())
        with patch.object(instance, "is_available", return_value=False):
            result = instance.ensure_available()
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, msg = result
        assert isinstance(ok, bool)
        assert isinstance(msg, str)


# ---------------------------------------------------------------------------
# register — calls IntegrationFactory.register_type
# ---------------------------------------------------------------------------


class TestMyIntegrationRegister:
    def test_register_calls_integration_factory_register_type(self):
        with patch.object(IntegrationFactory, "register_type") as mock_register:
            register()
        mock_register.assert_called_once_with("my_integration", MyIntegration)


# ---------------------------------------------------------------------------
# do_something — returns (False, non-empty str) when tool unavailable
# ---------------------------------------------------------------------------


class TestMyIntegrationDoSomething:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_do_something_returns_false_with_message_when_unavailable(self):
        instance = MyIntegration(_make_config())
        with patch.object(instance, "is_available", return_value=False):
            ok, msg = instance.do_something("test-arg")
        assert ok is False
        assert isinstance(msg, str)
        assert len(msg) > 0
