"""Tests for ConfigurationController."""

from xyz_platform.controllers.configuration_controller import ConfigurationController


class TestConfigurationControllerLoad:
    def test_load_config_missing_file_returns_empty(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ok, data = ctrl.load_config()
        assert ok is True
        assert data == {}

    def test_load_config_valid_yaml(self, tmp_path):
        state_dir = tmp_path / ".platform"
        state_dir.mkdir()
        (state_dir / "cli.yaml").write_text("values:\n  foo: bar\n", encoding="utf-8")
        ctrl = ConfigurationController(tmp_path)
        ok, data = ctrl.load_config()
        assert ok is True
        assert data == {"values": {"foo": "bar"}}

    def test_load_config_invalid_yaml_returns_error(self, tmp_path):
        state_dir = tmp_path / ".platform"
        state_dir.mkdir()
        (state_dir / "cli.yaml").write_text("{not: valid: yaml:", encoding="utf-8")
        ctrl = ConfigurationController(tmp_path)
        ok, data = ctrl.load_config()
        assert ok is False
        assert data == {}

    def test_load_config_non_dict_content_returns_error(self, tmp_path):
        state_dir = tmp_path / ".platform"
        state_dir.mkdir()
        (state_dir / "cli.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
        ctrl = ConfigurationController(tmp_path)
        ok, data = ctrl.load_config()
        assert ok is False
        assert ctrl.has_errors()


class TestConfigurationControllerWrite:
    def test_write_config_creates_directory_and_file(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ok, errors = ctrl.write_config({"values": {"x": 1}})
        assert ok is True
        assert errors == []
        assert (tmp_path / ".platform" / "cli.yaml").exists()

    def test_write_config_roundtrip(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.write_config({"values": {"key": "value"}})
        ok, data = ctrl.load_config()
        assert ok is True
        assert data["values"]["key"] == "value"


class TestConfigurationControllerCliValues:
    def test_list_cli_values_empty_when_no_file(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        assert ctrl.list_cli_values() == {}

    def test_list_cli_values_returns_values_section(self, tmp_path):
        state_dir = tmp_path / ".platform"
        state_dir.mkdir()
        (state_dir / "cli.yaml").write_text("values:\n  a: 1\n  b: 2\n", encoding="utf-8")
        ctrl = ConfigurationController(tmp_path)
        result = ctrl.list_cli_values()
        assert result == {"a": 1, "b": 2}

    def test_set_cli_value_creates_values_section(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ok, errors = ctrl.set_cli_value("mykey", "myval")
        assert ok is True
        assert ctrl.list_cli_values()["mykey"] == "myval"

    def test_set_cli_value_updates_existing(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.set_cli_value("k", "v1")
        ctrl.set_cli_value("k", "v2")
        assert ctrl.list_cli_values()["k"] == "v2"

    def test_unset_cli_value_removes_key(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.set_cli_value("remove_me", "bye")
        ok, errors = ctrl.unset_cli_value("remove_me")
        assert ok is True
        assert "remove_me" not in ctrl.list_cli_values()

    def test_unset_cli_value_missing_key_returns_success(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.set_cli_value("other", "stays")
        ok, errors = ctrl.unset_cli_value("nonexistent")
        assert ok is True
        assert ctrl.list_cli_values()["other"] == "stays"

    def test_unset_cli_value_last_key_removes_section(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.set_cli_value("only_key", "v")
        ctrl.unset_cli_value("only_key")
        ok, data = ctrl.load_config()
        assert "values" not in data

    def test_set_multiple_values_persisted(self, tmp_path):
        ctrl = ConfigurationController(tmp_path)
        ctrl.set_cli_value("a", 1)
        ctrl.set_cli_value("b", 2)
        vals = ctrl.list_cli_values()
        assert vals["a"] == 1
        assert vals["b"] == 2
