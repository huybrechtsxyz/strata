"""Tests for the `sln export` command."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_sln import sln_group


class TestSlnExportCommand:
    def test_missing_name_returns_exit_2(self):
        runner = CliRunner()
        result = runner.invoke(sln_group, ["export"])
        assert result.exit_code == 2

    def test_name_option_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.export_template_command.SolutionExportCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(sln_group, ["export", "--name", "my-tpl", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_force_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.export_template_command.SolutionExportCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["export", "--name", "my-tpl", "--force", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_dry_run_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.export_template_command.SolutionExportCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["export", "--name", "my-tpl", "--dry-run", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.export_template_command.SolutionExportCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(sln_group, ["export", "--name", "my-tpl", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_sln_group_invocation(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.export_template_command.SolutionExportCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(sln_group, ["export", "--name", "my-tpl", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
