"""Tests for the `init` command."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_init import init_command


class TestInitCommand:
    def test_missing_name_returns_exit_2(self):
        runner = CliRunner()
        result = runner.invoke(init_command, [])
        assert result.exit_code == 2

    def test_name_option_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=True):
            result = runner.invoke(init_command, ["--name", "myapp", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_from_template_nonexistent_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            init_command,
            ["--name", "myapp", "--from-template", str(tmp_path / "missing.yaml"), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 2

    def test_from_template_existing_file_accepted(self, tmp_path):
        template = tmp_path / "tmpl.yaml"
        template.write_text("apiVersion: platform.huybrechts.xyz/v1\nkind: workspace\n")
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=True):
            result = runner.invoke(
                init_command,
                ["--name", "myapp", "--from-template", str(template), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=False):
            result = runner.invoke(init_command, ["--name", "myapp", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
