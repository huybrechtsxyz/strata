"""Tests for the `config` command group (set / unset / list)."""

import json

import yaml
from click.testing import CliRunner

from strata.cli import main


class TestConfigSet:
    def test_set_creates_cli_yaml(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "output", "json"])
        assert result.exit_code == 0, result.output
        cfg_path = tmp_path / ".strata" / "cli.yaml"
        assert cfg_path.exists()

    def test_set_stores_value(self, tmp_path):
        runner = CliRunner()
        runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "output", "json"])
        cfg = yaml.safe_load((tmp_path / ".strata" / "cli.yaml").read_text(encoding="utf-8"))
        assert cfg.get("values", {}).get("output") == "json"

    def test_set_invalid_key_nonzero_exit(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "unknown_key", "val"])
        assert result.exit_code != 0


class TestConfigUnset:
    def test_unset_removes_key(self, tmp_path):
        runner = CliRunner()
        runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "output", "json"])
        result = runner.invoke(main, ["config", "unset", "--work-path", str(tmp_path), "output"])
        assert result.exit_code == 0, result.output
        cfg = yaml.safe_load((tmp_path / ".strata" / "cli.yaml").read_text(encoding="utf-8"))
        assert "output" not in cfg.get("values", {})

    def test_unset_nonexistent_key_exits_zero(self, tmp_path):
        runner = CliRunner()
        # cli.yaml doesn't exist yet — unsetting a key that was never set should not crash
        result = runner.invoke(main, ["config", "unset", "--work-path", str(tmp_path), "output"])
        assert result.exit_code == 0, result.output


class TestConfigList:
    def test_list_returns_zero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_list_json_output_structure(self, tmp_path):
        runner = CliRunner()
        runner.invoke(main, ["config", "set", "--work-path", str(tmp_path), "output", "json"])
        result = runner.invoke(main, ["config", "list", "--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["values"]["output"] == "json"

    def test_list_empty_workspace(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0, result.output
