"""Tests for the `audit` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_audit import audit_group


class TestAuditList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.show_audit_command.ShowAuditCommand.execute", return_value=True):
            result = runner.invoke(audit_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_lines_option(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.show_audit_command.ShowAuditCommand.execute", return_value=True):
            result = runner.invoke(audit_group, ["list", "--lines", "10", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_level_option(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.show_audit_command.ShowAuditCommand.execute", return_value=True):
            result = runner.invoke(audit_group, ["list", "--level", "ERROR", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_invalid_level_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(audit_group, ["list", "--level", "BADLEVEL", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_list_last_flag(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.show_audit_command.ShowAuditCommand.execute", return_value=True):
            result = runner.invoke(audit_group, ["list", "--last", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.show_audit_command.ShowAuditCommand.execute", return_value=False):
            result = runner.invoke(audit_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestAuditLog:
    def test_log_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.audit.log_config_command.LogConfigCommand.execute", return_value=True):
            result = runner.invoke(audit_group, ["log", "list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
