#!/usr/bin/env python3
"""Tests for `strata values get`."""

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

ENVIRONMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  variables:
    - key: REGION
      store: constant
      value: westeurope
  secrets:
    - key: DB_PASSWORD
      store: constant
      value: hunter2
"""

DEPLOYMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app
spec:
  partial: true
  environments: [prd]
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "environment.yaml").write_text(ENVIRONMENT, encoding="utf-8")
    (root / "deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["values", "get", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_all_keys_resolved_exits_zero(runner, solution):
    result = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS


def test_unresolved_key_exits_three(runner, solution):
    result = _run(runner, "app", "REGION", "GHOST", "--path", solution)
    assert result.exit_code == EXIT_VALIDATION


def test_unknown_deployment_exits_two(runner, solution):
    result = _run(runner, "ghost-deployment", "REGION", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_outside_a_solution_exits_two(runner, tmp_path):
    result = _run(runner, "app", "REGION", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_raw_format_requires_exactly_one_key(runner, solution):
    result = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution, "--format", "raw")
    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------------


def test_table_format_shows_every_requested_key(runner, solution):
    output = _run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution).output
    assert "westeurope" in output
    assert "hunter2" in output


def test_raw_format_prints_the_bare_value(runner, solution):
    result = _run(runner, "app", "REGION", "--path", solution, "--format", "raw")
    assert "westeurope" in result.output
    # Bare value — no key name, no report chrome, on its own line.
    assert "REGION" not in [line.strip() for line in result.output.splitlines()]


def test_env_format_renders_key_equals_value(runner, solution):
    output = _run(runner, "app", "REGION", "--path", solution, "--format", "env").output
    assert "REGION=westeurope" in output


def test_export_format_quotes_the_value(runner, solution):
    output = _run(runner, "app", "REGION", "--path", solution, "--format", "export").output
    assert "export REGION=westeurope" in output


def test_raw_env_export_emit_nothing_when_a_key_fails(runner, solution):
    """A script must never mistake a partial result for a complete one."""
    output = _run(runner, "app", "REGION", "GHOST", "--path", solution, "--format", "env").output
    assert "REGION=" not in output


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_emits_the_resolved_values(runner, solution):
    payload = json.loads(_run(runner, "app", "REGION", "DB_PASSWORD", "--path", solution, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["results"] == {"REGION": "westeurope", "DB_PASSWORD": "hunter2"}


def test_json_reports_a_failed_key_as_null_and_a_diagnostic(runner, solution):
    payload = json.loads(_run(runner, "app", "REGION", "GHOST", "--path", solution, "--output", "json").output)
    assert payload["ok"] is False
    assert payload["data"]["results"] == {"REGION": "westeurope", "GHOST": None}
    assert any("GHOST" in d["message"] for d in payload["diagnostics"])
