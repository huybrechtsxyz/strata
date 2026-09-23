#!/usr/bin/env python3
"""Tests for `strata validate`."""

import json
import shutil
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

TOPOLOGY = """apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: main-topology
spec:
  type: kubernetes
  components:
    - resource: web
"""

BROKEN = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: broken
spec:
  providers: [azure]
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "topology.yaml").write_text(TOPOLOGY, encoding="utf-8")
    return root


def _run(runner, *args):
    return runner.invoke(cli, ["validate", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# Exit codes — what pipelines branch on
# ---------------------------------------------------------------------------


def test_valid_solution_exits_zero(runner, solution):
    """The signal a pipeline checks first."""
    assert _run(runner, solution).exit_code == EXIT_SUCCESS


def test_invalid_document_exits_three(runner, solution):
    """Validation failure is distinct from a crash or a usage mistake."""
    (solution / "broken.yaml").write_text(BROKEN, encoding="utf-8")
    assert _run(runner, solution).exit_code == EXIT_VALIDATION


def test_outside_a_solution_exits_two(runner, tmp_path):
    """'Wrong directory' is a usage error, not broken configuration."""
    assert _run(runner, tmp_path).exit_code == EXIT_USAGE


def test_nonexistent_path_exits_two(runner, tmp_path):
    """A mistyped path is an invocation mistake."""
    assert _run(runner, tmp_path / "nope").exit_code == EXIT_USAGE


def test_runs_from_inside_the_solution(runner, solution, monkeypatch):
    """PATH is optional — the common case is 'validate where I am'."""
    monkeypatch.chdir(solution)
    assert runner.invoke(cli, ["validate"]).exit_code == EXIT_SUCCESS


def test_runs_from_a_subdirectory(runner, solution, monkeypatch):
    """Discovery walks upwards, so any nested directory works."""
    nested = solution / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert runner.invoke(cli, ["validate"]).exit_code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------


def test_console_reports_what_ran(runner, solution, monkeypatch):
    """The header is the record a bug report needs.

    argv is pinned because `command_line()` reads the real process argv —
    correct for a CLI run, but under CliRunner the callback is invoked
    directly and argv still belongs to pytest.
    """
    monkeypatch.setattr("sys.argv", ["strata", "validate", str(solution)])
    output = _run(runner, solution).output
    assert "test-solution" in output
    assert "strata validate" in output
    assert "UTC" in output


def test_console_redacts_secrets_in_the_invocation(runner, solution, monkeypatch):
    """Echoing the command line must never leak a secret into CI logs."""
    monkeypatch.setattr("sys.argv", ["strata", "validate", str(solution), "--token", "hunter2"])
    output = _run(runner, solution).output
    assert "hunter2" not in output
    assert "redacted" in output


def test_console_reports_the_verdict(runner, solution):
    """Pass/fail must be unmissable."""
    assert "PASSED" in _run(runner, solution).output


def test_console_reports_findings_with_location(runner, solution):
    """The structured fields are what make a finding actionable."""
    (solution / "broken.yaml").write_text(BROKEN, encoding="utf-8")
    output = _run(runner, solution).output
    assert "broken.yaml" in output
    assert "spec.provisioners" in output
    assert "FAILED" in output


def test_quiet_drops_chrome_but_keeps_findings(runner, solution):
    """--quiet must not become a way to hide problems."""
    (solution / "broken.yaml").write_text(BROKEN, encoding="utf-8")
    result = runner.invoke(cli, ["validate", str(solution), "--quiet"])
    assert "solution" not in result.output.split("broken.yaml")[0]
    assert "spec.provisioners" in result.output
    assert result.exit_code == EXIT_VALIDATION


def test_quiet_is_silent_on_success(runner, solution):
    """Nothing to say when there is nothing wrong."""
    assert runner.invoke(cli, ["validate", str(solution), "--quiet"]).output.strip() == ""


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_emits_one_parseable_document(runner, solution):
    """Pipelines capture stdout and pipe it straight to jq."""
    payload = json.loads(_run(runner, solution, "--output", "json").output)
    assert payload["ok"] is True
    assert payload["command"] == "validate"
    assert payload["summary"]["documents"] == 1


def test_json_is_parseable_on_validation_failure(runner, solution):
    """haven parses stdout *after* a non-zero exit."""
    (solution / "broken.yaml").write_text(BROKEN, encoding="utf-8")
    result = _run(runner, solution, "--output", "json")
    payload = json.loads(result.output)
    assert result.exit_code == EXIT_VALIDATION
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["location"] == "spec.provisioners"


def test_json_is_parseable_on_usage_error(runner, tmp_path):
    """The path v1 could not manage, which forced a jq fallback in workflows."""
    result = _run(runner, tmp_path, "--output", "json")
    payload = json.loads(result.output)
    assert result.exit_code == EXIT_USAGE
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "UsageError"


def test_json_sources_are_relative(runner, solution):
    """Stable between a laptop and CI."""
    (solution / "broken.yaml").write_text(BROKEN, encoding="utf-8")
    payload = json.loads(_run(runner, solution, "--output", "json").output)
    assert payload["diagnostics"][0]["source"] == "broken.yaml"


# ---------------------------------------------------------------------------
# --strict
# ---------------------------------------------------------------------------


def test_warnings_do_not_fail_by_default(runner, solution, monkeypatch):
    """A warning is reportable without breaking the build."""
    from strata.controllers import solution_context as module

    original = module.open_solution

    def with_warning(path=None):
        context = original(path)
        context.diagnostics.warning("stale pin", source="versions/prd.yaml")
        return context

    monkeypatch.setattr("strata.commands.validate_command.open_solution", with_warning)
    result = _run(runner, solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "stale pin" in result.output


def test_strict_promotes_warnings_to_failure(runner, solution, monkeypatch):
    """A release pipeline should be able to refuse a stale pin."""
    from strata.controllers import solution_context as module

    original = module.open_solution

    def with_warning(path=None):
        context = original(path)
        context.diagnostics.warning("stale pin", source="versions/prd.yaml")
        return context

    monkeypatch.setattr("strata.commands.validate_command.open_solution", with_warning)
    result = runner.invoke(cli, ["validate", str(solution), "--strict"])
    assert result.exit_code == EXIT_VALIDATION
    assert "FAILED" in result.output


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


def test_validate_is_registered(runner):
    """It appears in the top-level help."""
    assert "validate" in runner.invoke(cli, ["--help"]).output


def test_no_file_option(runner):
    """v2 validates a solution, not a file — `-f` has nothing to refer to."""
    assert "-f" not in runner.invoke(cli, ["validate", "--help"]).output


def test_no_deep_flag(runner):
    """Full validation is the default; the weak mode is not the one you get."""
    assert "--deep" not in runner.invoke(cli, ["validate", "--help"]).output


def test_help_documents_exit_codes(runner):
    """Pipelines branch on them, so they belong in the help."""
    output = runner.invoke(cli, ["validate", "--help"]).output
    assert "Exit codes" in output


@pytest.mark.skipif(not Path("config").exists(), reason="example config not present")
def test_shipped_example_solution_validates(runner, tmp_path):
    """The example ships valid, and must stay that way."""
    root = tmp_path / "config"
    shutil.copytree("config", root)
    assert _run(runner, root).exit_code == EXIT_SUCCESS
