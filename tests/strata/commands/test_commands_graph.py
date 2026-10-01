#!/usr/bin/env python3
"""Tests for `strata graph`."""

import json

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
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

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - {provider}
  provisioners:
    - name: tf
      tool: terraform
      source:
        remote: infra
        source_path: terraform/main
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
    (root / "workspace.yaml").write_text(WORKSPACE.format(provider="azure-main"), encoding="utf-8")
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["graph", *[str(a) for a in args]])


def test_valid_solution_exits_zero(runner, solution):
    assert _run(runner, solution).exit_code == EXIT_SUCCESS


def test_outside_a_solution_exits_two(runner, tmp_path):
    assert _run(runner, tmp_path).exit_code == EXIT_USAGE


def test_unresolved_reference_still_exits_zero(runner, tmp_path):
    """Dangling is informational here — `validate` is where it fails the run."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE.format(provider="does-not-exist"), encoding="utf-8")
    assert _run(runner, root).exit_code == EXIT_SUCCESS


def test_console_output_shows_source_and_target(runner, solution):
    result = _run(runner, solution)
    assert "workspace/main" in result.output
    assert "provider/azure-main" in result.output


def test_console_output_marks_unresolved(runner, tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE.format(provider="does-not-exist"), encoding="utf-8")
    result = _run(runner, root)
    assert "provider/does-not-exist" in result.output


def test_json_output_carries_edges(runner, solution):
    result = runner.invoke(cli, ["graph", str(solution), "--output", "json"])
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    edges = envelope["data"]["edges"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["source_kind"] == "workspace"
    assert edge["source_name"] == "main"
    assert edge["target_kind"] == "provider"
    assert edge["target_name"] == "azure-main"
    assert edge["resolved"] is True


def test_json_output_on_empty_solution(runner, tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    result = runner.invoke(cli, ["graph", str(root), "--output", "json"])
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["data"]["edges"] == []
