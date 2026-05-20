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

    def test_template_builtin_name_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=True):
            result = runner.invoke(
                init_command,
                ["--name", "myapp", "--template", "aks", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_template_local_folder_accepted(self, tmp_path):
        template_dir = tmp_path / "mytpl"
        (template_dir / "scaffold").mkdir(parents=True)
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=True):
            result = runner.invoke(
                init_command,
                ["--name", "myapp", "--template", str(template_dir), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=False):
            result = runner.invoke(init_command, ["--name", "myapp", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestSlnInitCommand:
    def test_sln_init_missing_name_returns_exit_2(self):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        result = runner.invoke(sln_group, ["init"])
        assert result.exit_code == 2

    def test_sln_init_accepted(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.init.init_solution_command.InitSolutionCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["init", "--name", "myapp", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
