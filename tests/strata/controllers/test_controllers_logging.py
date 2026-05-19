"""Tests for LoggingController."""

from strata.controllers.logging_controller import VALID_LEVELS, LoggingController


class TestLoggingControllerLoad:
    def test_load_missing_file_returns_empty(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ok, data = ctrl.load()
        assert ok is True
        assert data == {}

    def test_load_valid_yaml(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text("handlers:\n  console:\n    level: INFO\n", encoding="utf-8")
        ctrl = LoggingController(tmp_path)
        ok, data = ctrl.load()
        assert ok is True
        assert data["handlers"]["console"]["level"] == "INFO"

    def test_load_non_dict_returns_error(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text("- item\n", encoding="utf-8")
        ctrl = LoggingController(tmp_path)
        ok, data = ctrl.load()
        assert ok is False
        assert ctrl.has_errors()


class TestLoggingControllerWrite:
    def test_write_creates_directory_and_file(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ok, errors = ctrl.write({"key": "value"})
        assert ok is True
        assert (tmp_path / ".strata" / "logging.yaml").exists()

    def test_write_roundtrip(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ctrl.write({"handlers": {"console": {"level": "WARNING"}}})
        ok, data = ctrl.load()
        assert ok is True
        assert data["handlers"]["console"]["level"] == "WARNING"


class TestLoggingControllerListValues:
    def test_list_values_empty_dict_when_missing(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        result = ctrl.list_values()
        assert result == {}

    def test_list_values_returns_parsed_content(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text("x: 1\ny: 2\n", encoding="utf-8")
        ctrl = LoggingController(tmp_path)
        result = ctrl.list_values()
        assert result == {"x": 1, "y": 2}


class TestLoggingControllerGetValue:
    def test_get_value_returns_nested_value(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text("handlers:\n  console:\n    level: DEBUG\n", encoding="utf-8")
        ctrl = LoggingController(tmp_path)
        found, val = ctrl.get_value("handlers.console.level")
        assert found is True
        assert val == "DEBUG"

    def test_get_value_missing_key_returns_not_found(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        found, val = ctrl.get_value("handlers.console.level")
        assert found is False
        assert val is None


class TestLoggingControllerSetValue:
    def test_set_value_arbitrary_key(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ok, errors = ctrl.set_value("custom.key", "hello")
        assert ok is True
        found, val = ctrl.get_value("custom.key")
        assert found is True
        assert val == "hello"

    def test_set_value_level_updates_both_paths(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text(
            "handlers:\n  console:\n    level: INFO\nloggers:\n  strata:\n    level: INFO\n",
            encoding="utf-8",
        )
        ctrl = LoggingController(tmp_path)
        ok, errors = ctrl.set_value("level", "DEBUG")
        assert ok is True
        _, v1 = ctrl.get_value("handlers.console.level")
        _, v2 = ctrl.get_value("loggers.strata.level")
        assert v1 == "DEBUG"
        assert v2 == "DEBUG"

    def test_set_value_invalid_level_returns_error(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ok, errors = ctrl.set_value("level", "VERBOSE")
        assert ok is False
        assert ctrl.has_errors()
        assert any("VERBOSE" in e for e in ctrl.get_errors())

    def test_set_value_valid_levels(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text(
            "handlers:\n  console:\n    level: INFO\nloggers:\n  strata:\n    level: INFO\n",
            encoding="utf-8",
        )
        for level in VALID_LEVELS:
            ctrl = LoggingController(tmp_path)
            ok, _ = ctrl.set_value("level", level)
            assert ok is True


class TestLoggingControllerUnsetValue:
    def test_unset_value_removes_key(self, tmp_path):
        ctrl = LoggingController(tmp_path)
        ctrl.set_value("foo.bar", "baz")
        ctrl.unset_value("foo.bar")
        found, _ = ctrl.get_value("foo.bar")
        assert found is False

    def test_unset_level_removes_both_paths(self, tmp_path):
        state_dir = tmp_path / ".strata"
        state_dir.mkdir()
        (state_dir / "logging.yaml").write_text(
            "handlers:\n  console:\n    level: DEBUG\nloggers:\n  strata:\n    level: DEBUG\n",
            encoding="utf-8",
        )
        ctrl = LoggingController(tmp_path)
        ok, errors = ctrl.unset_value("level")
        assert ok is True
        found1, _ = ctrl.get_value("handlers.console.level")
        found2, _ = ctrl.get_value("loggers.strata.level")
        assert found1 is False
        assert found2 is False


class TestLoggingControllerNestedHelpers:
    def test_split_path(self):
        parts = LoggingController._split_path("a.b.c")
        assert parts == ["a", "b", "c"]

    def test_set_nested_creates_path(self):
        data = {}
        LoggingController._set_nested(data, ["a", "b", "c"], "val")
        assert data == {"a": {"b": {"c": "val"}}}

    def test_get_nested_finds_deep_value(self):
        data = {"a": {"b": {"c": 42}}}
        found, val = LoggingController._get_nested(data, ["a", "b", "c"])
        assert found is True
        assert val == 42

    def test_get_nested_missing_key_returns_not_found(self):
        data = {"a": {}}
        found, val = LoggingController._get_nested(data, ["a", "b", "c"])
        assert found is False

    def test_unset_nested_removes_key(self):
        data = {"a": {"b": "v"}}
        removed = LoggingController._unset_nested(data, ["a", "b"])
        assert removed is True
        assert "b" not in data["a"]

    def test_unset_nested_missing_key_returns_false(self):
        data = {}
        removed = LoggingController._unset_nested(data, ["x"])
        assert removed is False
