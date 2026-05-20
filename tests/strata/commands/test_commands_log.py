"""Tests for the `log` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_config import config_group
from strata.commands.cli_log import log_group


class TestLogGroup:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(log_group, ["--help"])
        assert result.exit_code == 0

    def test_help_contains_config_log_crossreference(self):
        runner = CliRunner()
        result = runner.invoke(log_group, ["--help"])
        assert "config log" in result.output


class TestLogList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_lines_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--lines", "10", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_level_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--level", "ERROR", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_invalid_level_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(log_group, ["list", "--level", "BADLEVEL", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_list_last_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--last", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.show_log_command.ShowLogCommand.execute", return_value=False):
            result = runner.invoke(log_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestAuditLog:
    def test_log_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.logger.log_config_command.LogConfigCommand.execute", return_value=True):
            result = runner.invoke(config_group, ["log", "list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
