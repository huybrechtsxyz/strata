"""Tests for the `clean` command."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_clean import clean_command


class TestCleanCommand:
    def test_basic_invocation_mocked(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=True):
            result = runner.invoke(clean_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_dry_run_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=True):
            result = runner.invoke(clean_command, ["--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=False):
            result = runner.invoke(clean_command, ["--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestSlnCleanCommand:
    def test_sln_clean_basic(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["clean", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sln_clean_dry_run_flag_accepted(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["clean", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sln_clean_execute_false_returns_nonzero(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.clean.clean_solution_command.CleanSolutionCommand.execute", return_value=False):
            result = runner.invoke(sln_group, ["clean", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
