"""Tests for the `values` command group (list / get)."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_values import values_group


class TestValuesList:
    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_basic_list_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(values_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_type_filter_variables(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "variables", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_type_filter_secrets(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "secrets", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_type_filter_features(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "features", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_invalid_type_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            values_group, ["list", "-f", "deploy.yaml", "--type", "badtype", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 2

    def test_show_store_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--show-store", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_unresolved_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--unresolved", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--stage", "production", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(values_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestValuesGet:
    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["get", "DB_PASSWORD", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_key_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["get", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_single_key_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["get", "-f", "deploy.yaml", "DB_PASSWORD", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_multiple_keys_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["get", "-f", "deploy.yaml", "DB_PASSWORD", "API_KEY", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                values_group, ["get", "-f", "deploy.yaml", "MISSING_KEY", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0
