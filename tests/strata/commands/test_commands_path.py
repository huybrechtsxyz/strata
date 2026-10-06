#!/usr/bin/env python3
"""Tests for `strata path get`/`strata path list`."""

import json

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

VERSION = """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: {name}
spec:
{workspace_line}  pins: {{}}
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE, encoding="utf-8")
    return root


def _get(runner, *args):
    return runner.invoke(cli, ["path", "get", *[str(a) for a in args]])


def _list(runner, *args):
    return runner.invoke(cli, ["path", "list", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# path get
# ---------------------------------------------------------------------------


def test_get_existing_document_exits_zero_and_prints_its_path(runner, solution):
    result = _get(runner, "workspace", "main", "--path", solution, "-q")
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == str((solution / "workspace.yaml").resolve())


def test_get_unknown_name_exits_validation_failure(runner, solution):
    result = _get(runner, "workspace", "ghost", "--path", solution)
    assert result.exit_code == EXIT_VALIDATION
    assert "ghost" in result.output


def test_get_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _get(runner, "workspace", "main", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_get_rejects_an_unknown_kind(runner, solution):
    result = _get(runner, "bogus-kind", "main", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_get_json_output_on_a_real_document(runner, solution):
    result = _get(runner, "workspace", "main", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"] == {
        "kind": "workspace",
        "name": "main",
        "path": str((solution / "workspace.yaml").resolve()),
        "exists": True,
    }


def test_get_json_output_on_an_unknown_name(runner, solution):
    result = _get(runner, "workspace", "ghost", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_VALIDATION
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["data"] == {"kind": "workspace", "name": "ghost", "path": None, "exists": False}
    assert any(d["code"] == "path_not_found" for d in envelope["diagnostics"])


# ---------------------------------------------------------------------------
# path list
# ---------------------------------------------------------------------------


def test_list_every_document_of_one_kind(runner, solution):
    result = _list(runner, "workspace", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "main" in result.output
    assert str((solution / "workspace.yaml").resolve()) in result.output


def test_list_with_no_matching_documents_still_exits_zero(runner, solution):
    result = _list(runner, "dns", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "no documents found" in result.output


def test_list_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _list(runner, "workspace", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_list_json_output_carries_every_result(runner, solution):
    result = _list(runner, "workspace", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["data"]["kind"] == "workspace"
    assert envelope["data"]["workspace"] is None
    assert envelope["data"]["results"] == [
        {
            "kind": "workspace",
            "name": "main",
            "path": str((solution / "workspace.yaml").resolve()),
            "exists": True,
        }
    ]


def test_list_workspace_filter_matches_versions_declaring_it(runner, solution):
    (solution / "version-prd.yaml").write_text(
        VERSION.format(name="prd", workspace_line="  workspace: main\n"), encoding="utf-8"
    )
    (solution / "version-other.yaml").write_text(VERSION.format(name="other", workspace_line=""), encoding="utf-8")

    result = _list(runner, "version", "--path", solution, "--workspace", "main")
    assert result.exit_code == EXIT_SUCCESS
    assert "prd" in result.output
    assert "other" not in result.output


def test_list_workspace_filter_on_a_kind_with_no_workspace_field_is_empty(runner, solution):
    """`provider` has no `spec.workspace` — filtering by it returns nothing
    rather than erroring (docs/work/version-lifecycle.md's own documented
    limitation for this flag)."""
    result = _list(runner, "provider", "--path", solution, "--workspace", "main")
    assert result.exit_code == EXIT_SUCCESS
    assert "no documents found" in result.output
