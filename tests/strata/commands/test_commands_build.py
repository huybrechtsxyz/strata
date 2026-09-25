#!/usr/bin/env python3
"""Tests for `strata build run`."""

import json
from pathlib import Path

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

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: p1
spec:
  properties:
    type: local
    region: local
"""

RESOURCE = """apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: r1
spec:
  properties:
    provider_type: local
    resource_type: server
    category: compute
  default_tags:
    managed-by: strata
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - p1
  provisioners:
    - name: tf_main
      tool: terraform
      source:
        source_path: infra
  execution:
    - name: apply_infra
      provisioner: tf_main
      targets:
        - r1
  resources:
    - name: r1
      resource: r1
"""

ENVIRONMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec: {}
"""

DEPLOYMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app
spec:
  workspace: main
  environments:
    - prd
"""


@pytest.fixture
def runner():
    return CliRunner()


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "provider.yaml", PROVIDER)
    _write(root, "resource.yaml", RESOURCE)
    _write(root, "workspace.yaml", WORKSPACE)
    _write(root, "environment.yaml", ENVIRONMENT)
    _write(root, "deployment.yaml", DEPLOYMENT)
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["build", "run", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_successful_build_exits_zero(runner, solution):
    result = _run(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS, result.output


def test_unknown_deployment_exits_two(runner, solution):
    result = _run(runner, "ghost-deployment", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_outside_a_solution_exits_two(runner, tmp_path):
    result = _run(runner, "app", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_invalid_solution_exits_three(runner, solution):
    # A deployment naming a workspace that does not exist fails cross-document
    # reference checks, so `require_valid()` raises before build_run() runs.
    _write(solution, "deployment.yaml", DEPLOYMENT.replace("workspace: main", "workspace: ghost-workspace"))
    result = _run(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_VALIDATION


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------


def test_build_writes_rendered_artifacts_under_the_default_build_path(runner, solution):
    result = _run(runner, "app", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS, result.output
    materialised = solution / "build" / "app" / "infra"
    assert (materialised / "main.tf").exists()
    assert (materialised / "workspace.auto.tfvars.json").exists()


def test_build_path_option_overrides_the_default(runner, solution, tmp_path):
    custom = tmp_path / "out"
    result = _run(runner, "app", "--path", solution, "--build-path", custom)
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert (custom / "infra" / "main.tf").exists()
    assert not (solution / "build").exists()


# ---------------------------------------------------------------------------
# --clean / --no-clean
# ---------------------------------------------------------------------------


def test_default_build_path_is_always_cleaned(runner, solution):
    """The default path is exclusively this build's own directory - always
    safe to wipe, no flag needed."""
    stale = solution / "build" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")

    result = _run(runner, "app", "--path", solution)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert not stale.exists()


def test_custom_build_path_is_not_cleaned_unless_requested(runner, solution, tmp_path):
    custom = tmp_path / "out"
    custom.mkdir()
    (custom / "leftover.txt").write_text("still here")

    result = _run(runner, "app", "--path", solution, "--build-path", custom)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert (custom / "leftover.txt").read_text() == "still here"


def test_custom_build_path_is_cleaned_when_clean_flag_given(runner, solution, tmp_path):
    custom = tmp_path / "out"
    custom.mkdir()
    (custom / "leftover.txt").write_text("still here")

    result = _run(runner, "app", "--path", solution, "--build-path", custom, "--clean")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert not (custom / "leftover.txt").exists()


def test_no_clean_flag_disables_cleaning_even_for_the_default_path(runner, solution):
    stale = solution / "build" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")

    result = _run(runner, "app", "--path", solution, "--no-clean")

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert stale.exists()


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_reports_success(runner, solution):
    payload = json.loads(_run(runner, "app", "--path", solution, "--output", "json").output)
    assert payload["ok"] is True
