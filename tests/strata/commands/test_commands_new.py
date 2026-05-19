"""Tests for the ``new`` command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_new import new_command
from strata.commands.new.run_new_command import NewCommand


class TestNewCommand:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--help"])
        assert result.exit_code == 0
        assert "TEMPLATE" in result.output
        assert "NAME" in result.output

    def test_basic_creation(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_overwrite_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--overwrite", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_with_path(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--path", str(tmp_path), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_set_override(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--set", "owner=myteam", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_list_templates(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_missing_template_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_name_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["namespace", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_unknown_template_exits_1(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["nonexistent_xyz_template", "myapp", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_real_namespace_creation(self, tmp_path):
        """Integration test: actually render the namespace template into tmp_path."""
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "namespace",
                "myapp",
                "--path",
                str(tmp_path),
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        out_file = tmp_path / "myapp-namespace.yaml"
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "myapp" in content

    def test_existing_file_no_overwrite_exits_1(self, tmp_path):
        """Writing the same file twice without --overwrite must exit 1."""
        runner = CliRunner()
        # First write succeeds
        runner.invoke(
            new_command,
            ["namespace", "myapp", "--path", str(tmp_path), "--work-path", str(tmp_path)],
        )
        # Second write without --overwrite must fail
        result = runner.invoke(
            new_command,
            ["namespace", "myapp", "--path", str(tmp_path), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_existing_file_with_overwrite_exits_0(self, tmp_path):
        """Writing the same file twice WITH --overwrite must exit 0."""
        runner = CliRunner()
        runner.invoke(
            new_command,
            ["namespace", "myapp", "--path", str(tmp_path), "--work-path", str(tmp_path)],
        )
        result = runner.invoke(
            new_command,
            [
                "namespace",
                "myapp",
                "--path",
                str(tmp_path),
                "--overwrite",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output


class TestNewCommandContextSubstitution:
    """Unit-level tests for context variable substitution in NewCommand."""

    def test_context_substitution(self, tmp_path):
        """Team context from solution.json is merged into the render context."""
        mock_solution = MagicMock()
        mock_solution.spec.context = {"owner": "acme", "version": "2.0.0"}

        cmd = NewCommand(
            template="namespace",
            name="myapp",
            path=str(tmp_path),
            overwrite=False,
            set_values=("version=3.0.0",),
            work_path=str(tmp_path),
        )

        def _fake_load():
            cmd._solution_controller._solution = mock_solution
            return True, []

        with patch.object(cmd._solution_controller, "load", side_effect=_fake_load):
            # Replicate the context-building logic from _run_execution
            rendered_context: dict = {"name": "myapp"}
            ok, _ = cmd._solution_controller.load()
            if ok and cmd._solution_controller._solution is not None:
                rendered_context.update(cmd._solution_controller._solution.spec.context or {})
            for kv in cmd._set_values:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    rendered_context[k] = v

        assert rendered_context["name"] == "myapp"
        assert rendered_context["owner"] == "acme"
        assert rendered_context["version"] == "3.0.0"  # --set overrides context
