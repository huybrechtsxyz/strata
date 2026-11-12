#!/usr/bin/env python3
"""Tests for `strata deploy steps` (docs/design/deploy-steps-command.md).

The controller has its own tests (`test_deployment_steps.py`); these cover the command: options, console table,
JSON envelope and exit codes.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION
from strata.integrations import base as base_module

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"

_PROVISIONERS = "".join(
    f"    - name: tf_{x}\n      tool: terraform\n      source:\n        source_path: infra_{x}\n" for x in "ab"
)
_EXECUTION = (
    "    - name: network\n      provisioner: tf_a\n      resources: [ra]\n      scope: infra\n"
    "    - name: apps\n      provisioner: tf_b\n      resources: [rb]\n      depends_on: [network]\n      scope: apps\n"
)


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, execution: str = _EXECUTION) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    for x in "ab":
        (root / f"infra_{x}").mkdir()
        (root / f"infra_{x}" / "main.tf").write_text("# module\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        f"  provisioners:\n{_PROVISIONERS}"
        f"  execution:\n{execution}"
        "  resources:\n    - name: ra\n      resource: r1\n    - name: rb\n      resource: r1\n",
    )
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(root, "app.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _steps(runner: CliRunner, root: Path, *args: str):
    return runner.invoke(cli, ["deploy", "steps", "app", "--path", str(root), *args])


def _data(result) -> dict:
    return json.loads(result.output)["data"]


def test_console_table_lists_steps_and_scopes(runner, tmp_path):
    result = _steps(runner, _solution(tmp_path))

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "ORDER  LEVEL  STEP" in result.output
    assert "network" in result.output
    assert "Scopes: infra -> apps" in result.output


def test_json_carries_the_contract(runner, tmp_path):
    result = _steps(runner, _solution(tmp_path), "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    envelope = json.loads(result.output)
    assert envelope["command"] == "deploy steps"
    data = envelope["data"]
    assert [(s["name"], s["order"], s["level"], s["scope"]) for s in data["steps"]] == [
        ("network", 0, 0, "infra"),
        ("apps", 1, 1, "apps"),
    ]
    assert data["steps"][0]["integration"]["type"] == "terraform"
    assert data["steps"][0]["capabilities"] == {"saved_plan": True, "destroy": True, "container": False}
    assert data["scopes"] == [
        {"scope": "infra", "steps": ["network"], "depends_on": [], "orderable": True},
        {"scope": "apps", "steps": ["apps"], "depends_on": ["infra"], "orderable": True},
    ]
    assert data["tools_checked"] is False


def test_repeated_filters(runner, tmp_path):
    result = _steps(runner, _solution(tmp_path), "--scope", "apps", "--scope", "infra", "--stage", "apps", "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    data = _data(result)
    assert [s["name"] for s in data["steps"]] == ["apps"]
    assert data["filters"] == {"stages": ["apps"], "scopes": ["apps", "infra"]}


def test_an_empty_selection_exits_zero_with_unmatched(runner, tmp_path):
    root = _solution(tmp_path)

    result = _steps(runner, root, "--scope", "db", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert _data(result)["steps"] == []
    assert _data(result)["unmatched"] == {"stages": [], "scopes": ["db"]}

    console = _steps(runner, root, "--scope", "db")
    assert console.exit_code == EXIT_SUCCESS, console.output
    assert "(no steps match)" in console.output
    assert "No step matches --scope db" in console.output


def test_destroy_lists_teardown_order(runner, tmp_path):
    result = _steps(runner, _solution(tmp_path), "--destroy", "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    data = _data(result)
    assert data["direction"] == "destroy"
    assert [s["name"] for s in data["steps"]] == ["apps", "network"]


def test_verbose_prints_effective_targets(runner, tmp_path):
    result = _steps(runner, _solution(tmp_path), "-v")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "resources: ra" in result.output


def test_an_unknown_deployment_exits_usage(runner, tmp_path):
    result = runner.invoke(cli, ["deploy", "steps", "ghost", "--path", str(_solution(tmp_path))])

    assert result.exit_code == EXIT_USAGE, result.output


def test_an_invalid_solution_exits_validation(runner, tmp_path):
    root = _solution(tmp_path, execution="    - name: network\n      provisioner: missing\n      resources: [ra]\n")

    result = _steps(runner, root)
    assert result.exit_code == EXIT_VALIDATION, result.output


def test_check_tools_exits_validation_when_a_tool_is_missing(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: None)

    result = _steps(runner, _solution(tmp_path), "--check-tools", "-o", "json")

    assert result.exit_code == EXIT_VALIDATION, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["tools_checked"] is True
    assert {d["code"] for d in envelope["diagnostics"]} == {"integration_unavailable"}


def test_check_tools_adds_a_ready_column(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    result = _steps(runner, _solution(tmp_path), "--check-tools")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "TOOL READY" in result.output
