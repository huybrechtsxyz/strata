"""Tests for EnvController."""

import os

from strata.controllers.env_controller import EnvController


class TestEnvControllerListSources:
    def test_list_sources_empty_when_no_file(self, tmp_path):
        ctrl = EnvController(tmp_path)
        assert ctrl.list_sources() == []

    def test_list_sources_sorted_by_order(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("b", "b.env", order=20)
        ctrl.add_source("a", "a.env", order=10)
        sources = ctrl.list_sources()
        assert sources[0]["name"] == "a"
        assert sources[1]["name"] == "b"


class TestEnvControllerAddSource:
    def test_add_source_persists(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ok, errors = ctrl.add_source("base", "envs/base.env", order=10)
        assert ok is True
        assert errors == []
        sources = ctrl.list_sources()
        assert len(sources) == 1
        assert sources[0]["name"] == "base"
        assert sources[0]["path"] == "envs/base.env"
        assert sources[0]["order"] == 10

    def test_add_source_duplicate_name_fails(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("base", "base.env")
        ok, errors = ctrl.add_source("base", "other.env")
        assert ok is False
        assert any("already exists" in e for e in errors)


class TestEnvControllerGetSource:
    def test_get_source_returns_matching(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("prod", "prod.env", order=50)
        src = ctrl.get_source("prod")
        assert src is not None
        assert src["name"] == "prod"

    def test_get_source_returns_none_when_missing(self, tmp_path):
        ctrl = EnvController(tmp_path)
        assert ctrl.get_source("nonexistent") is None


class TestEnvControllerRemoveSource:
    def test_remove_source_removes_entry(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("x", "x.env")
        ok, errors = ctrl.remove_source("x")
        assert ok is True
        assert ctrl.list_sources() == []

    def test_remove_source_missing_returns_error(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ok, errors = ctrl.remove_source("not_there")
        assert ok is False
        assert any("not found" in e for e in errors)

    def test_remove_one_keeps_others(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("keep", "keep.env", order=10)
        ctrl.add_source("remove", "remove.env", order=20)
        ctrl.remove_source("remove")
        sources = ctrl.list_sources()
        assert len(sources) == 1
        assert sources[0]["name"] == "keep"


class TestEnvControllerParseEnvFile:
    def test_parse_basic_key_value(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("KEY=value\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"KEY": "value"}

    def test_parse_double_quoted_value(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text('GREETING="hello world"\n', encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"GREETING": "hello world"}

    def test_parse_single_quoted_value(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("GREETING='hi there'\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"GREETING": "hi there"}

    def test_parse_skips_comments(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("# this is a comment\nKEY=val\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert "# this is a comment" not in result
        assert result == {"KEY": "val"}

    def test_parse_skips_blank_lines(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("\n\nKEY=val\n\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"KEY": "val"}

    def test_parse_export_prefix(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("export MY_VAR=exported\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"MY_VAR": "exported"}

    def test_parse_multiple_entries(self, tmp_path):
        f = tmp_path / "test.env"
        f.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
        result = EnvController._parse_env_file(f)
        assert result == {"A": "1", "B": "2", "C": "3"}


class TestEnvControllerResolveAndLoad:
    def test_resolve_and_load_missing_file_produces_warning(self, tmp_path):
        ctrl = EnvController(tmp_path)
        ctrl.add_source("base", "missing.env", order=10)
        merged, warnings = ctrl.resolve_and_load()
        assert merged == {}
        assert any("not found" in w for w in warnings)

    def test_resolve_and_load_valid_file_merges_vars(self, tmp_path):
        env_file = tmp_path / "base.env"
        env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        ctrl = EnvController(tmp_path)
        ctrl.add_source("base", str(env_file), order=10)
        merged, warnings = ctrl.resolve_and_load()
        assert merged.get("FOO") == "bar"
        assert merged.get("BAZ") == "qux"
        assert warnings == []

    def test_resolve_and_load_later_source_overrides(self, tmp_path):
        f1 = tmp_path / "f1.env"
        f2 = tmp_path / "f2.env"
        f1.write_text("KEY=first\n", encoding="utf-8")
        f2.write_text("KEY=second\n", encoding="utf-8")
        ctrl = EnvController(tmp_path)
        ctrl.add_source("base", str(f1), order=10)
        ctrl.add_source("override", str(f2), order=20)
        merged, _ = ctrl.resolve_and_load()
        assert merged["KEY"] == "second"


class TestEnvControllerInject:
    def test_inject_sets_env_vars(self, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("STRATA_TEST_INJECT=injected\n", encoding="utf-8")
        ctrl = EnvController(tmp_path)
        ctrl.add_source("test", str(env_file))
        warnings = ctrl.inject()
        assert os.environ.get("STRATA_TEST_INJECT") == "injected"
        # cleanup
        os.environ.pop("STRATA_TEST_INJECT", None)
