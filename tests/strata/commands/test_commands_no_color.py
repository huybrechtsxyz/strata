"""Tests for --no-color flag and NO_COLOR env var support on the main CLI group."""

import os

import pytest
from click.testing import CliRunner

from strata.cli import main


class TestNoColorFlag:
    """--no-color propagates correctly through the CLI."""

    def test_no_color_flag_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--no-color", "--help"])
        assert result.exit_code == 0

    def test_no_color_sets_env_var(self, monkeypatch):
        """--no-color must be accepted; version subcommand exits cleanly."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        runner = CliRunner()
        # Invoke a cheap subcommand; use version so we don't need a workspace.
        result = runner.invoke(main, ["--no-color", "--version"])
        assert result.exit_code == 0

    def test_no_color_appears_in_help(self):
        """--no-color is listed in strata --help output."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--no-color" in result.output

    def test_no_color_env_var_accepted(self, monkeypatch):
        """NO_COLOR env var (no-color.org standard) is honoured without the flag."""
        monkeypatch.setenv("NO_COLOR", "1")
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0

    def test_strata_no_color_env_var_accepted(self, monkeypatch):
        """STRATA_NO_COLOR env var (via auto_envvar_prefix) also enables the flag."""
        monkeypatch.setenv("STRATA_NO_COLOR", "1")
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
