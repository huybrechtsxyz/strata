#!/usr/bin/env python3
"""Tests for the CLI entry point and the version command."""

import subprocess
import sys

import pytest
from click.testing import CliRunner

from strata.commands.cli import EXIT_SUCCESS, EXIT_USAGE, cli
from strata.utils.version import get_version


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# strata version
# ---------------------------------------------------------------------------


def test_version_subcommand_prints_the_version(runner):
    """`strata version` writes just the version."""
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == get_version()


def test_version_flag_matches_the_subcommand(runner):
    """`--version` and `version` cannot report different things."""
    flag = runner.invoke(cli, ["--version"])
    sub = runner.invoke(cli, ["version"])
    assert flag.exit_code == EXIT_SUCCESS
    assert get_version() in flag.output
    assert sub.output.strip() in flag.output


def test_version_flag_names_the_program_not_the_distribution(runner):
    """Users type 'strata', so that is what the banner should say."""
    result = runner.invoke(cli, ["--version"])
    assert result.output.startswith("strata,")


# ---------------------------------------------------------------------------
# Group behaviour
# ---------------------------------------------------------------------------


def test_bare_invocation_shows_help(runner):
    """No arguments is not an error — it explains what is available."""
    result = runner.invoke(cli, [])
    assert "version" in result.output
    assert "Commands:" in result.output


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_both_help_flags_work(runner, flag):
    """-h is expected by muscle memory; click only wires --help by default."""
    result = runner.invoke(cli, [flag])
    assert result.exit_code == EXIT_SUCCESS
    assert "Show the strata version." in result.output


def test_unknown_command_is_a_usage_error(runner):
    """Exit code 2 for bad arguments, matching v1 and click convention."""
    result = runner.invoke(cli, ["nonexistent"])
    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# Module execution
# ---------------------------------------------------------------------------


def test_module_execution_runs_this_strata():
    """`python -m strata` must resolve through imports, not PATH.

    The console script is named `strata`, which a globally installed v1
    shadows during parallel development.
    """
    result = subprocess.run(
        [sys.executable, "-m", "strata", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == EXIT_SUCCESS, result.stderr
    assert result.stdout.strip() == get_version()
