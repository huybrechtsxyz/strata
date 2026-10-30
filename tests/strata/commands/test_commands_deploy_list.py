#!/usr/bin/env python3
"""Tests for `strata deployment list` (docs/work/vscode-integrations-policies-views.md V1)."""

import json
from pathlib import Path

from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE
from strata.controllers.deployment_resolution import list_deployments
from strata.controllers.solution_context import open_solution

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(root, "base.yaml", "deployment", "base", "  partial: true\n  workspace: main\n")
    _write(root, "app.yaml", "deployment", "app", "  extends: base\n  environments:\n    - prd\n")
    return root


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["deployment", "list", *args])


def test_list_resolves_extends_and_marks_partials(tmp_path):
    rows = {row.name: row for row in list_deployments(open_solution(_solution(tmp_path)).controller.index)}
    assert (rows["app"].workspace, rows["app"].environments, rows["app"].partial) == ("main", ("prd",), False)
    assert (rows["base"].workspace, rows["base"].partial) == ("main", True)
    assert Path(rows["app"].source).name == "app.yaml"


def test_list_json_shape(tmp_path):
    result = _invoke("--path", str(_solution(tmp_path)), "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    rows = json.loads(result.output)["data"]["deployments"]
    assert [row["name"] for row in rows] == ["app", "base"]
    assert set(rows[0]) == {"name", "workspace", "environments", "partial", "source"}
    assert rows[0]["environments"] == ["prd"]


def test_list_console_marks_partials(tmp_path):
    result = _invoke("--path", str(_solution(tmp_path)))
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "base (partial)" in result.output
    assert "app " in result.output and "main" in result.output and "prd" in result.output


def test_list_outside_a_solution_is_a_usage_error(tmp_path):
    result = _invoke("--path", str(tmp_path))
    assert result.exit_code == EXIT_USAGE, result.output
