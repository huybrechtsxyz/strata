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

ENVIRONMENT_FULL = """apiVersion: strata.huybrechts.xyz/v2
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
      store: infisical
      value: db/password
      generate:
        type: password
        length: 24
      rotate:
        max_age: 90
        policy: warn
  features:
    - key: ENABLE_BETA
      store: constant
      value: "true"
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


@pytest.fixture
def solution_full(tmp_path):
    """A solution whose environment declares one of each kind — a secret
    with `generate:`/`rotate:` specs, for `values list --declared-only`."""
    root = tmp_path / "sln-full"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "environment.yaml").write_text(ENVIRONMENT_FULL, encoding="utf-8")
    (root / "deployment.yaml").write_text(DEPLOYMENT, encoding="utf-8")
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["values", "get", *[str(a) for a in args]])


def _run_list(runner, *args):
    return runner.invoke(cli, ["values", "list", *[str(a) for a in args]])


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


# ---------------------------------------------------------------------------
# `values list --declared-only`
# ---------------------------------------------------------------------------


def test_list_without_declared_only_exits_two(runner, solution_full):
    """Live resolution (the eventual default mode) isn't built yet (Phase 4)."""
    result = _run_list(runner, "app", "--path", solution_full)
    assert result.exit_code == EXIT_USAGE


def test_list_declared_only_exits_zero(runner, solution_full):
    result = _run_list(runner, "app", "--path", solution_full, "--declared-only")
    assert result.exit_code == EXIT_SUCCESS


def test_list_declared_only_shows_every_kind(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "REGION" in output
    assert "DB_PASSWORD" in output
    assert "ENABLE_BETA" in output


def test_list_declared_only_never_resolves_or_reveals_a_secret_value(runner, solution_full):
    """No store I/O at all — never resolves/reveals the actual secret value."""
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "hunter2" not in output  # never resolved, so never revealed


def test_list_declared_only_json_includes_the_declared_value_ref(runner, solution_full):
    """Console rendering omits `value_ref` (matches v1's `secret list`); JSON carries it."""
    payload = json.loads(
        _run_list(runner, "app", "--path", solution_full, "--declared-only", "--output", "json").output
    )
    by_key = {row["key"]: row for row in payload["data"]["values"]}
    assert by_key["DB_PASSWORD"]["value_ref"] == "db/password"


def test_list_declared_only_shows_generate_and_rotate_specs(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only").output
    assert "generate:password/24" in output
    assert "rotate:90d/warn" in output


def test_list_declared_only_type_filter_restricts_to_one_kind(runner, solution_full):
    output = _run_list(runner, "app", "--path", solution_full, "--declared-only", "--type", "secrets").output
    assert "DB_PASSWORD" in output
    assert "REGION" not in output
    assert "ENABLE_BETA" not in output


def test_list_unknown_deployment_exits_two(runner, solution_full):
    result = _run_list(runner, "ghost-deployment", "--path", solution_full, "--declared-only")
    assert result.exit_code == EXIT_USAGE


def test_list_declared_only_json_shape(runner, solution_full):
    payload = json.loads(
        _run_list(runner, "app", "--path", solution_full, "--declared-only", "--output", "json").output
    )
    assert payload["ok"] is True
    assert payload["data"]["deployment"] == "app"
    assert payload["data"]["declared_only"] is True
    by_key = {row["key"]: row for row in payload["data"]["values"]}
    assert by_key["REGION"] == {
        "key": "REGION",
        "kind": "variable",
        "store": "constant",
        "value_ref": "westeurope",
        "generate": None,
        "rotate": None,
    }
    assert by_key["DB_PASSWORD"]["generate"] == "password/24"
    assert by_key["DB_PASSWORD"]["rotate"] == "90d/warn"
    assert by_key["ENABLE_BETA"]["kind"] == "feature"


def test_list_declared_only_with_no_environment_exits_zero_with_empty_list(runner, tmp_path):
    root = tmp_path / "sln-empty"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "deployment.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n  partial: true\n",
        encoding="utf-8",
    )
    result = _run_list(runner, "app", "--path", root, "--declared-only")
    assert result.exit_code == EXIT_SUCCESS
    assert "no variables, secrets, or features declared" in result.output


# ---------------------------------------------------------------------------
# `values generate` / `values mask` — pure utilities, no solution needed
# ---------------------------------------------------------------------------


def test_generate_prints_a_bare_value_by_default(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "hex", "--length", "8"])
    assert result.exit_code == EXIT_SUCCESS
    assert len(result.output.strip()) == 16  # 8 bytes -> 16 hex chars, plus trailing newline


def test_generate_needs_no_solution_at_all(runner, tmp_path, monkeypatch):
    """Unlike every other `values` subcommand, `generate` never calls `open_solution()`."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["values", "generate", "--format", "uuid4"])
    assert result.exit_code == EXIT_SUCCESS


def test_generate_json_includes_format_and_length(runner):
    payload = json.loads(
        runner.invoke(cli, ["values", "generate", "--format", "numeric", "--length", "6", "--output", "json"]).output
    )
    assert payload["format"] == "numeric"
    assert payload["length"] == 6
    assert len(payload["secret"]) == 6


def test_generate_json_omits_length_for_uuid_formats(runner):
    payload = json.loads(runner.invoke(cli, ["values", "generate", "--format", "uuid7", "--output", "json"]).output)
    assert "length" not in payload


def test_generate_rejects_unknown_format(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "rot13"])
    assert result.exit_code == EXIT_USAGE


def test_generate_rejects_too_short_a_password(runner):
    result = runner.invoke(cli, ["values", "generate", "--format", "password", "--length", "3"])
    assert result.exit_code == EXIT_USAGE


def test_mask_prints_the_bare_masked_value_by_default(runner):
    result = runner.invoke(cli, ["values", "mask", "hunter2-supersecret", "--show", "4"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == "hunt***************"


def test_mask_needs_no_solution_at_all(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["values", "mask", "abcdef"])
    assert result.exit_code == EXIT_SUCCESS


def test_mask_uses_a_custom_char(runner):
    result = runner.invoke(cli, ["values", "mask", "abcdefgh", "--show", "2", "--char", "#"])
    assert result.output.strip() == "ab######"


def test_mask_json_includes_show_and_char(runner):
    payload = json.loads(runner.invoke(cli, ["values", "mask", "abcdefgh", "--output", "json"]).output)
    assert payload == {"masked": "abcd****", "show": 4, "char": "*"}


def test_mask_rejects_a_multi_character_replacement(runner):
    result = runner.invoke(cli, ["values", "mask", "abcdefgh", "--char", "**"])
    assert result.exit_code == EXIT_USAGE
