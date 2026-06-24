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

    def test_list_templates_shows_builtins(self, tmp_path):
        """--list shows built-in scaffold templates without requiring --name."""
        runner = CliRunner()
        result = runner.invoke(init_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "aks" in result.output
        assert "compose" in result.output

    def test_list_templates_json_output(self, tmp_path):
        """--list --output json returns structured template data."""
        import json

        runner = CliRunner()
        result = runner.invoke(init_command, ["--list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        names = [t["name"] for t in data["data"]["templates"]]
        assert "aks" in names
        assert "compose" in names

    def test_list_templates_includes_workspace_templates(self, tmp_path):
        """--list picks up scaffold templates from .strata/templates/."""
        # Create a workspace-local scaffold template
        tpl_dir = tmp_path / ".strata" / "templates" / "custom-stack"
        scaffold_dir = tpl_dir / "scaffold"
        scaffold_dir.mkdir(parents=True)
        (scaffold_dir / "file.yaml").write_text("x: 1\n", encoding="utf-8")
        (tpl_dir / "template.yaml").write_text(
            "name: custom-stack\ndescription: My custom workspace template\nvariables: []\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(init_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "custom-stack" in result.output
        assert "My custom workspace template" in result.output

    def test_list_without_name_does_not_error(self):
        """--list without --name must succeed (no missing option error)."""
        runner = CliRunner()
        result = runner.invoke(init_command, ["--list"])
        assert result.exit_code == 0


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

    def test_sln_init_list(self, tmp_path):
        """sln init --list shows available templates via the sln group."""
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        result = runner.invoke(sln_group, ["init", "--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "aks" in result.output
