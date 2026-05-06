"""Tests for the `log` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_log import log_group


class TestLogList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_lines_option(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--lines", "10", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_level_option(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--level", "ERROR", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_invalid_level_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(log_group, ["list", "--level", "BADLEVEL", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_list_last_flag(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.show_log_command.ShowLogCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["list", "--last", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.show_log_command.ShowLogCommand.execute", return_value=False):
            result = runner.invoke(log_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestLogConfig:
    def test_config_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.log.config_log_command.LogConfigCommand.execute", return_value=True):
            result = runner.invoke(log_group, ["config", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
