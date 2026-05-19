"""Tests for the ``context`` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.cli import main
from strata.commands.cli_context import context_group


class TestContextGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(context_group, ["--help"])
        assert result.exit_code == 0
        assert "set" in result.output
        assert "unset" in result.output
        assert "list" in result.output

    def test_set_help(self):
        runner = CliRunner()
        result = runner.invoke(context_group, ["set", "--help"])
        assert result.exit_code == 0
        assert "KEY" in result.output
        assert "VALUE" in result.output

    def test_unset_help(self):
        runner = CliRunner()
        result = runner.invoke(context_group, ["unset", "--help"])
        assert result.exit_code == 0
        assert "KEY" in result.output

    def test_list_help(self):
        runner = CliRunner()
        result = runner.invoke(context_group, ["list", "--help"])
        assert result.exit_code == 0

    def test_set_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.context.set_context_command.SetContextCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                context_group,
                ["set", "owner", "myteam", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_unset_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.context.set_context_command.SetContextCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                context_group,
                ["unset", "owner", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.context.set_context_command.SetContextCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                context_group,
                ["list", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_outside_workspace_exits_1(self, tmp_path):
        """``context list`` without an initialised solution must exit 1."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["context", "list", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_set_outside_workspace_exits_1(self, tmp_path):
        """``context set`` without an initialised solution must exit 1."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["context", "set", "owner", "myteam", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1
