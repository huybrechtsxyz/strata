"""Tests for the `deploy` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy


class TestDeployRun:
    def test_run_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_dry_run_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["run", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_run_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.run_deploy_command.RunDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestDeployDestroy:
    def test_destroy_dry_run(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["destroy", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_destroy_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["destroy", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_destroy_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=True):
            result = runner.invoke(
                deploy, ["destroy", "--stage", "production", "--dry-run", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_destroy_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.destroy_deploy_command.DestroyDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["destroy", "--dry-run", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestDeployStatus:
    def test_status_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_show_plan_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--plan", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.status_deploy_command.StatusDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["status", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestDeployHistory:
    def test_history_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_lines_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--lines", "10", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_operation_filter(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.history_deploy_command.HistoryDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["history", "--operation", "run", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_history_invalid_operation_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(deploy, ["history", "--operation", "badop", "--work-path", str(tmp_path)])
        assert result.exit_code == 2
