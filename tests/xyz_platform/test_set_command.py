import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from xyz_platform.cli import main


def test_set_and_list(tmp_path: Path):
    runner = CliRunner()
    work_path = tmp_path

    # Set output to json
    result = runner.invoke(main, ["set", "--work-path", str(work_path), "output", "json"])
    assert result.exit_code == 0, result.output

    cfg_path = work_path / ".platform" / "cli.yaml"
    assert cfg_path.exists()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert cfg.get("defaults", {}).get("output") == "json"

    # List defaults with structured JSON output
    result = runner.invoke(main, ["config", "--work-path", str(work_path), "--output", "json", "list"])
    assert result.exit_code == 0, result.output
    # The command outputs a JSON envelope when --output json is used
    data = json.loads(result.output)
    assert data["success"] is True
    assert data["data"]["defaults"]["output"] == "json"


def test_unset(tmp_path: Path):
    runner = CliRunner()
    work_path = tmp_path

    # Set and then unset
    result = runner.invoke(main, ["set", "--work-path", str(work_path), "output", "json"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["config", "--work-path", str(work_path), "unset", "output"])
    assert result.exit_code == 0, result.output

    cfg_path = work_path / ".platform" / "cli.yaml"
    assert cfg_path.exists()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "defaults" not in cfg or "output" not in cfg.get("defaults", {})
