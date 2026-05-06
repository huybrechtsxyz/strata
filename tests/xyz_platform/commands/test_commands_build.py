"""Tests for the `build` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_builders import build


class TestBuildRun:
    def test_run_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.run_build_command.RunBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["run", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.run_build_command.RunBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestBuildClean:
    def test_clean_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_clean_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.clean_build_command.CleanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["clean", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestBuildPlan:
    def test_plan_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_with_stage(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_artifacts_only_flag(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=True):
            result = runner.invoke(build, ["plan", "--artifacts-only", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_plan_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.builders.plan_build_command.PlanBuildCommand.execute", return_value=False):
            result = runner.invoke(build, ["plan", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
