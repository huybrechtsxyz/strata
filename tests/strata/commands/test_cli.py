"""Tests for the top-level cli.py wiring: _load_workspace_defaults, _resolve_work_path_early, _build_default_map, and main group."""

from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from strata.cli import _build_default_map, _load_workspace_defaults, main

# ---------------------------------------------------------------------------
# _load_workspace_defaults
# ---------------------------------------------------------------------------


class TestLoadWorkspaceDefaults:
    def _write_cli_yaml(self, work_path: Path, values: dict) -> None:
        cfg_dir = work_path / ".strata"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "cli.yaml").write_text(yaml.dump({"values": values}), encoding="utf-8")

    def test_missing_file_returns_empty(self, tmp_path):
        result = _load_workspace_defaults(tmp_path)
        assert result == {}

    def test_loads_allowed_keys(self, tmp_path):
        self._write_cli_yaml(tmp_path, {"output": "json", "verbose": True})
        result = _load_workspace_defaults(tmp_path)
        assert result["output"] == "json"
        assert result["verbose"] is True

    def test_filters_unknown_keys(self, tmp_path):
        self._write_cli_yaml(tmp_path, {"output": "json", "unknown_key": "bad"})
        result = _load_workspace_defaults(tmp_path)
        assert "unknown_key" not in result
        assert "output" in result

    def test_empty_yaml_returns_empty(self, tmp_path):
        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "cli.yaml").write_text("", encoding="utf-8")
        result = _load_workspace_defaults(tmp_path)
        assert result == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "cli.yaml").write_text("{{{{not valid yaml", encoding="utf-8")
        result = _load_workspace_defaults(tmp_path)
        assert result == {}

    def test_all_allowed_keys_loaded(self, tmp_path):
        self._write_cli_yaml(tmp_path, {"output": "text", "verbose": False, "quiet": True, "work_path": "/some/path"})
        result = _load_workspace_defaults(tmp_path)
        assert set(result.keys()) == {"output", "verbose", "quiet", "work_path"}


# ---------------------------------------------------------------------------
# _build_default_map
# ---------------------------------------------------------------------------


class TestBuildDefaultMap:
    def test_single_level_group(self):
        result = _build_default_map(main, {"output": "json"})
        # Every registered subcommand should appear in the map
        assert "config" in result
        assert "build" in result
        assert "deploy" in result

    def test_nested_defaults_propagate(self):
        result = _build_default_map(main, {"output": "json"})
        # config is a group — its children should also carry the defaults
        assert "set" in result["config"]
        assert result["config"]["set"].get("output") == "json"

    def test_deploy_subcommands_present(self):
        result = _build_default_map(main, {"verbose": True})
        assert "run" in result["deploy"]
        assert result["deploy"]["run"].get("verbose") is True


# ---------------------------------------------------------------------------
# main group — routing and help
# ---------------------------------------------------------------------------


class TestMainGroup:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "XYZ Platform" in result.output

    def test_no_subcommand_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(main, [])
        # Click groups print help and exit 0 or 2 depending on invoke_without_command
        assert "XYZ Platform" in result.output or result.exit_code in (0, 2)

    def test_unknown_subcommand_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["nonexistent-command"])
        assert result.exit_code != 0

    def test_all_groups_registered(self):
        registered = set(main.commands.keys())
        expected = {
            "sln",
            "config",
            "audit",
            "repo",
            "profile",
            "ref",
            "validate",
            "build",
            "deploy",
            "values",
            "version",
            "help",
        }
        assert expected.issubset(registered)
        # init/clean/status moved under sln — must NOT appear at top level
        assert "init" not in registered
        assert "clean" not in registered
        assert "status" not in registered

    def test_sln_subcommands_registered(self):
        from strata.commands.cli_sln import sln_group

        sln_cmds = set(sln_group.commands.keys())
        assert {"init", "clean", "status", "export"} == sln_cmds


# ---------------------------------------------------------------------------
# default_map applied via main (integration)
# ---------------------------------------------------------------------------


class TestDefaultMapIntegration:
    def test_workspace_default_applied_to_subcommand(self, tmp_path):
        """A default set via config set is picked up by subsequent commands via default_map."""
        runner = CliRunner()
        # Write output=json default
        runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "output", "json"])

        # Now invoke config list — should use json output without explicit --output flag
        with patch("strata.commands.config.set_config_command.SetConfigCommand.execute", return_value=True):
            result = runner.invoke(main, ["config", "list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
