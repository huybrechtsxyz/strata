"""Tests for the `status` command."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_status import status_command


class TestStatusCommand:
    def test_basic_invocation_mocked(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_json_output_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.status.show_status_command.StatusCommand.execute", return_value=False):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code != 0
