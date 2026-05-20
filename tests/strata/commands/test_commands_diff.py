"""Tests for the `diff` command."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_diff import diff_command


class TestDiff:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(diff_command, ["--help"])
        assert result.exit_code == 0

    def test_help_contains_description(self):
        runner = CliRunner()
        result = runner.invoke(diff_command, ["--help"])
        assert "what would change" in result.output.lower()

    def test_diff_requires_init(self, tmp_path):
        """diff should fail gracefully when workspace is not initialized."""
        runner = CliRunner()
        result = runner.invoke(diff_command, ["--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_diff_no_file_gives_error(self, tmp_path):
        """diff without --file should report missing file."""
        runner = CliRunner()
        # Create a fake .strata dir so init check passes
        (tmp_path / ".strata").mkdir()
        (tmp_path / ".strata" / "solution.json").write_text("{}")
        result = runner.invoke(diff_command, ["--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_diff_execute_success(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.diff.diff_command.DiffCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(diff_command, ["--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_diff_execute_failure(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.diff.diff_command.DiffCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(diff_command, ["--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_diff_stage_option_accepted(self):
        runner = CliRunner()
        result = runner.invoke(diff_command, ["--help"])
        assert "--stage" in result.output

    def test_diff_registered_in_main(self):
        from strata.cli import main

        assert "diff" in main.commands
