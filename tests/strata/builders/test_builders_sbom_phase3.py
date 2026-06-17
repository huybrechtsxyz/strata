"""Unit tests for Phase 3: LockfileParserRegistry, built-in parsers, and DependencyFileCollector."""

import json
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from strata.builders.sbom.deps_collector import DependencyFileCollector
from strata.builders.sbom.lockfile_parsers import (
    DEFAULT_REGISTRY,
    GoSumParser,
    LockfileParser,
    LockfileParserRegistry,
    PackageLockJsonParser,
    PyprojectTomlParser,
    RawDependency,
    RequirementsTxtParser,
    UvLockParser,
)
from strata.builders.sbom_builder import SbomBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def _mock_platform():
    return MagicMock()


# ---------------------------------------------------------------------------
# LockfileParserRegistry
# ---------------------------------------------------------------------------


class TestLockfileParserRegistry:
    def test_register_and_find_by_exact_filename(self):
        reg = LockfileParserRegistry()
        parser = RequirementsTxtParser()
        reg.register(parser)
        assert reg.find("requirements.txt") is parser

    def test_find_with_glob_pattern(self):
        reg = LockfileParserRegistry()
        parser = RequirementsTxtParser()
        reg.register(parser)
        assert reg.find("requirements-dev.txt") is parser
        assert reg.find("requirements-prod.txt") is parser

    def test_find_returns_none_for_unknown_file(self):
        reg = LockfileParserRegistry()
        assert reg.find("Cargo.lock") is None

    def test_last_registered_wins(self):
        reg = LockfileParserRegistry()

        class ParserA(LockfileParser, register=False):
            @property
            def ecosystem(self):
                return "pypi"

            def filename_patterns(self):
                return ["requirements.txt"]

            def parse(self, path):
                return []

        class ParserB(LockfileParser, register=False):
            @property
            def ecosystem(self):
                return "pypi"

            def filename_patterns(self):
                return ["requirements.txt"]

            def parse(self, path):
                return []

        reg.register(ParserA())
        reg.register(ParserB())
        assert isinstance(reg.find("requirements.txt"), ParserB)

    def test_all_patterns_deduplicates(self):
        reg = LockfileParserRegistry()
        reg.register(RequirementsTxtParser())
        reg.register(RequirementsTxtParser())
        patterns = reg.all_patterns()
        assert patterns.count("requirements*.txt") == 1

    def test_copy_is_independent(self):
        reg = LockfileParserRegistry()
        reg.register(RequirementsTxtParser())
        copy = reg.copy()
        copy.register(UvLockParser())
        assert reg.find("uv.lock") is None
        assert copy.find("uv.lock") is not None

    def test_all_patterns_preserves_order(self):
        reg = LockfileParserRegistry()
        reg.register(RequirementsTxtParser())
        reg.register(UvLockParser())
        patterns = reg.all_patterns()
        assert patterns.index("requirements*.txt") < patterns.index("uv.lock")


# ---------------------------------------------------------------------------
# DEFAULT_REGISTRY auto-registration
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_five_built_in_patterns_registered(self):
        patterns = DEFAULT_REGISTRY.all_patterns()
        assert "requirements*.txt" in patterns
        assert "pyproject.toml" in patterns
        assert "uv.lock" in patterns
        assert "package-lock.json" in patterns
        assert "go.sum" in patterns

    def test_register_false_does_not_pollute(self):
        before = len(DEFAULT_REGISTRY.all_patterns())

        class FakeParser(LockfileParser, register=False):
            @property
            def ecosystem(self):
                return "test"

            def filename_patterns(self):
                return ["fake.lock"]

            def parse(self, path):
                return []

        assert len(DEFAULT_REGISTRY.all_patterns()) == before
        assert DEFAULT_REGISTRY.find("fake.lock") is None


# ---------------------------------------------------------------------------
# RequirementsTxtParser
# ---------------------------------------------------------------------------


class TestRequirementsTxtParser:
    def _parse(self, content: str, tmp_path: Path) -> List[RawDependency]:
        p = _write(tmp_path, "requirements.txt", content)
        return RequirementsTxtParser().parse(p)

    def test_strict_pin_extracts_version(self, tmp_path):
        deps = self._parse("click==8.1.8\npydantic==2.11.5\n", tmp_path)
        assert RawDependency("click", "8.1.8") in deps
        assert RawDependency("pydantic", "2.11.5") in deps

    def test_loose_constraint_version_is_none(self, tmp_path):
        deps = self._parse("requests>=2.28.0\n", tmp_path)
        assert any(d.name == "requests" and d.version is None for d in deps)

    def test_skips_comments(self, tmp_path):
        deps = self._parse("# this is a comment\nclick==8.0\n", tmp_path)
        assert len(deps) == 1

    def test_skips_blank_lines(self, tmp_path):
        deps = self._parse("\n\nclick==8.0\n\n", tmp_path)
        assert len(deps) == 1

    def test_skips_options(self, tmp_path):
        deps = self._parse("-r base.txt\n-e .\n--extra-index-url ...\nclick==8.0\n", tmp_path)
        assert len([d for d in deps if d.name == "click"]) == 1
        assert all(d.name != "r" for d in deps)

    def test_inline_comment_stripped(self, tmp_path):
        deps = self._parse("click==8.0  # the CLI\n", tmp_path)
        assert RawDependency("click", "8.0") in deps

    def test_glob_pattern_matches_requirements_dev(self, tmp_path):
        p = tmp_path / "requirements-dev.txt"
        p.write_text("pytest==8.0\n")
        deps = RequirementsTxtParser().parse(p)
        assert RawDependency("pytest", "8.0") in deps

    def test_empty_file(self, tmp_path):
        deps = self._parse("", tmp_path)
        assert deps == []

    def test_missing_file_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            RequirementsTxtParser().parse(tmp_path / "nonexistent.txt")


# ---------------------------------------------------------------------------
# PyprojectTomlParser
# ---------------------------------------------------------------------------


class TestPyprojectTomlParser:
    def _parse(self, content: str, tmp_path: Path) -> List[RawDependency]:
        p = _write(tmp_path, "pyproject.toml", content)
        return PyprojectTomlParser().parse(p)

    def test_pinned_dependency_extracted(self, tmp_path):
        toml = '[project]\ndependencies = ["click==8.1.8"]\n'
        deps = self._parse(toml, tmp_path)
        assert RawDependency("click", "8.1.8") in deps

    def test_unpinned_dependency_version_none(self, tmp_path):
        toml = '[project]\ndependencies = ["requests>=2.28"]\n'
        deps = self._parse(toml, tmp_path)
        assert any(d.name == "requests" and d.version is None for d in deps)

    def test_no_project_section_returns_empty(self, tmp_path):
        deps = self._parse("[tool.ruff]\n", tmp_path)
        assert deps == []

    def test_no_dependencies_key_returns_empty(self, tmp_path):
        deps = self._parse("[project]\nname = 'foo'\n", tmp_path)
        assert deps == []

    def test_invalid_toml_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            PyprojectTomlParser().parse(_write(tmp_path, "pyproject.toml", "[[[ bad toml"))


# ---------------------------------------------------------------------------
# UvLockParser
# ---------------------------------------------------------------------------


class TestUvLockParser:
    def _parse(self, content: str, tmp_path: Path) -> List[RawDependency]:
        p = _write(tmp_path, "uv.lock", content)
        return UvLockParser().parse(p)

    def test_package_entries_extracted(self, tmp_path):
        toml = '[[package]]\nname = "click"\nversion = "8.1.8"\n\n[[package]]\nname = "pydantic"\nversion = "2.11.5"\n'
        deps = self._parse(toml, tmp_path)
        assert RawDependency("click", "8.1.8") in deps
        assert RawDependency("pydantic", "2.11.5") in deps

    def test_package_without_version(self, tmp_path):
        toml = '[[package]]\nname = "local-pkg"\n'
        deps = self._parse(toml, tmp_path)
        assert any(d.name == "local-pkg" and d.version is None for d in deps)

    def test_empty_lock_file(self, tmp_path):
        deps = self._parse("", tmp_path)
        assert deps == []

    def test_invalid_toml_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            UvLockParser().parse(_write(tmp_path, "uv.lock", "{{{"))


# ---------------------------------------------------------------------------
# PackageLockJsonParser
# ---------------------------------------------------------------------------


class TestPackageLockJsonParser:
    def _parse(self, data: dict, tmp_path: Path) -> List[RawDependency]:
        p = _write(tmp_path, "package-lock.json", json.dumps(data))
        return PackageLockJsonParser().parse(p)

    def test_packages_extracted(self, tmp_path):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "my-app"},
                "node_modules/react": {"version": "18.3.0"},
                "node_modules/typescript": {"version": "5.4.5"},
            },
        }
        deps = self._parse(data, tmp_path)
        assert RawDependency("react", "18.3.0") in deps
        assert RawDependency("typescript", "5.4.5") in deps
        assert not any(d.name == "" for d in deps)

    def test_root_package_skipped(self, tmp_path):
        data = {"packages": {"": {"name": "root"}}}
        deps = self._parse(data, tmp_path)
        assert deps == []

    def test_scoped_package_name_preserved(self, tmp_path):
        data = {"packages": {"node_modules/@scope/pkg": {"version": "1.0.0"}}}
        deps = self._parse(data, tmp_path)
        assert RawDependency("@scope/pkg", "1.0.0") in deps

    def test_invalid_json_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            PackageLockJsonParser().parse(_write(tmp_path, "package-lock.json", "{bad json"))

    def test_empty_packages_returns_empty(self, tmp_path):
        deps = self._parse({"packages": {}}, tmp_path)
        assert deps == []


# ---------------------------------------------------------------------------
# GoSumParser
# ---------------------------------------------------------------------------


class TestGoSumParser:
    def _parse(self, content: str, tmp_path: Path) -> List[RawDependency]:
        p = _write(tmp_path, "go.sum", content)
        return GoSumParser().parse(p)

    def test_source_entries_extracted(self, tmp_path):
        content = "github.com/user/repo v1.2.3 h1:abc123=\ngithub.com/user/repo v1.2.3/go.mod h1:def456=\n"
        deps = self._parse(content, tmp_path)
        assert RawDependency("github.com/user/repo", "v1.2.3") in deps
        assert len(deps) == 1  # /go.mod entry skipped

    def test_go_mod_entries_skipped(self, tmp_path):
        content = "github.com/foo/bar v0.1.0/go.mod h1:xxx=\n"
        deps = self._parse(content, tmp_path)
        assert deps == []

    def test_deduplication_first_wins(self, tmp_path):
        content = "github.com/foo/bar v1.0.0 h1:aaa=\ngithub.com/foo/bar v1.1.0 h1:bbb=\n"
        deps = self._parse(content, tmp_path)
        result = [d for d in deps if d.name == "github.com/foo/bar"]
        assert len(result) == 1
        assert result[0].version == "v1.0.0"

    def test_empty_file(self, tmp_path):
        deps = self._parse("", tmp_path)
        assert deps == []

    def test_missing_file_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            GoSumParser().parse(tmp_path / "nonexistent.sum")


# ---------------------------------------------------------------------------
# DependencyFileCollector
# ---------------------------------------------------------------------------


class TestDependencyFileCollector:
    def _fresh_registry(self) -> LockfileParserRegistry:
        reg = LockfileParserRegistry()
        reg.register(RequirementsTxtParser())
        return reg

    def test_collects_from_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.name == "click" and c.version == "8.1.8" for c in comps)

    def test_component_type_is_library(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert all(c.component_type == "library" for c in comps)

    def test_source_collector_is_deps(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert all(c.source_collector == "deps" for c in comps)

    def test_purl_format_pinned(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.purl == "pkg:pypi/click@8.1.8" for c in comps)

    def test_purl_format_unpinned(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests>=2.0\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.purl == "pkg:pypi/requests" for c in comps)

    def test_deduplication_across_files(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        click_comps = [c for c in comps if c.name == "click"]
        assert len(click_comps) == 1

    def test_parse_error_becomes_warning(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        bad_parser = MagicMock(spec=RequirementsTxtParser)
        bad_parser.filename_patterns.return_value = ["requirements*.txt"]
        bad_parser.ecosystem = "pypi"
        bad_parser.parse.side_effect = ValueError("bad parse")
        reg = LockfileParserRegistry()
        reg.register(bad_parser)
        collector = DependencyFileCollector(registry=reg)
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert comps == []
        assert collector.get_warnings()

    def test_empty_registry_returns_empty(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=LockfileParserRegistry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert comps == []

    def test_nonexistent_scan_path_returns_empty(self, tmp_path):
        # Write solution.json pointing to a missing repo path
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        solution = {"spec": {"repositories": [{"name": "repo", "path": str(tmp_path / "missing-repo")}]}}
        (strata_dir / "solution.json").write_text(json.dumps(solution))
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert comps == []

    def test_sbom_ignore_excludes_file(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        (tmp_path / "requirements-dev.txt").write_text("pytest==8.0\n")
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "sbom-ignore.yaml").write_text("ignore_files:\n  - requirements-dev.txt\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert not any(c.name == "pytest" for c in comps)
        assert any(c.name == "click" for c in comps)

    def test_default_ignore_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "requirements.txt").write_text("evil==1.0\n")
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert not any(c.name == "evil" for c in comps)
        assert any(c.name == "click" for c in comps)

    def test_scan_scoped_to_solution_repos(self, tmp_path):
        # Only the registered repo path should be scanned
        repo_dir = tmp_path / "repos" / "my-app"
        repo_dir.mkdir(parents=True)
        (repo_dir / "requirements.txt").write_text("django==5.0\n")
        # File outside repo scope — should NOT be picked up
        (tmp_path / "requirements.txt").write_text("noise==9.9\n")
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        solution = {"spec": {"repositories": [{"name": "my-app", "path": str(repo_dir)}]}}
        (strata_dir / "solution.json").write_text(json.dumps(solution))
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.name == "django" for c in comps)
        assert not any(c.name == "noise" for c in comps)

    def test_falls_back_to_work_path_when_no_solution(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("click==8.1.8\n")
        collector = DependencyFileCollector(registry=self._fresh_registry())
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.name == "click" for c in comps)

    def test_get_collector_name(self):
        assert DependencyFileCollector().get_collector_name() == "deps"

    def test_npm_purl_format(self, tmp_path):
        data = {"packages": {"node_modules/react": {"version": "18.3.0"}}}
        (tmp_path / "package-lock.json").write_text(json.dumps(data))
        reg = LockfileParserRegistry()
        reg.register(PackageLockJsonParser())
        collector = DependencyFileCollector(registry=reg)
        comps = collector.collect(_mock_platform(), tmp_path, tmp_path)
        assert any(c.purl == "pkg:npm/react@18.3.0" for c in comps)


# ---------------------------------------------------------------------------
# CollectorPluginLoader — lockfile_parser type
# ---------------------------------------------------------------------------


class TestCollectorPluginLoaderLockfileParser:
    def test_lockfile_parser_plugin_auto_registers(self, tmp_path):
        """Importing a plugin module with a LockfileParser subclass auto-registers it."""
        plugin_content = """
from strata.builders.sbom.lockfile_parsers import LockfileParser, RawDependency
from pathlib import Path
from typing import List

class CargoLockParser(LockfileParser):
    @property
    def ecosystem(self):
        return "cargo"
    def filename_patterns(self):
        return ["Cargo.lock"]
    def parse(self, path: Path) -> List[RawDependency]:
        return [RawDependency("serde", "1.0.0")]
"""
        plugin_file = tmp_path / ".strata" / "plugins" / "cargo_parser.py"
        plugin_file.parent.mkdir(parents=True)
        plugin_file.write_text(plugin_content)

        collectors_yaml = tmp_path / ".strata" / "collectors.yaml"
        collectors_yaml.write_text(
            "collectors:\n  - name: cargo\n    path: .strata/plugins/cargo_parser.py\n    type: lockfile_parser\n"
        )

        from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
        from strata.builders.sbom.lockfile_parsers import DEFAULT_REGISTRY

        extra = CollectorPluginLoader.load(tmp_path)
        assert extra == []  # lockfile_parser returns no extra collectors
        # The parser should now be in DEFAULT_REGISTRY
        assert DEFAULT_REGISTRY.find("Cargo.lock") is not None

    def test_lockfile_parser_missing_file_raises(self, tmp_path):
        from strata.builders.sbom.collector_plugin_loader import CollectorPluginLoader
        from strata.exceptions.base_exception import PlatformError

        collectors_yaml = tmp_path / ".strata" / "collectors.yaml"
        collectors_yaml.parent.mkdir(parents=True)
        collectors_yaml.write_text(
            "collectors:\n  - name: missing\n    path: .strata/plugins/nonexistent.py\n    type: lockfile_parser\n"
        )
        with pytest.raises(PlatformError, match="file not found"):
            CollectorPluginLoader.load(tmp_path)


# ---------------------------------------------------------------------------
# SbomBuilder — default collector count (7 after Phase 3)
# ---------------------------------------------------------------------------


class TestSbomBuilderDefaultCollectorCount:
    def test_default_collectors_count_is_seven(self):
        builder = SbomBuilder()
        assert len(builder._collectors) == 7

    def test_default_collectors_includes_deps(self):
        builder = SbomBuilder()
        names = [c.get_collector_name() for c in builder._collectors]
        assert "deps" in names
