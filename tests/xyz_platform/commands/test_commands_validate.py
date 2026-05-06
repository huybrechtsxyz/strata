"""Tests for the `validate` command."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_validate import validate_command


class TestValidateCommand:
    def test_missing_file_arg_returns_exit_2(self):
        runner = CliRunner()
        result = runner.invoke(validate_command, [])
        assert result.exit_code == 2

    def test_valid_file_mocked_success(self, tmp_path):
        target = tmp_path / "workspace.yaml"
        target.write_text("apiVersion: platform.huybrechts.xyz/v1\nkind: workspace\n")
        runner = CliRunner()
        with patch("xyz_platform.commands.validate.run_validate_command.ValidateCommand.execute", return_value=True):
            result = runner.invoke(validate_command, [str(target), "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_deep_flag_accepted(self, tmp_path):
        target = tmp_path / "workspace.yaml"
        target.write_text("apiVersion: platform.huybrechts.xyz/v1\nkind: workspace\n")
        runner = CliRunner()
        with patch("xyz_platform.commands.validate.run_validate_command.ValidateCommand.execute", return_value=True):
            result = runner.invoke(validate_command, [str(target), "--deep", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        target = tmp_path / "bad.yaml"
        target.write_text("not: valid: yaml: content")
        runner = CliRunner()
        with patch("xyz_platform.commands.validate.run_validate_command.ValidateCommand.execute", return_value=False):
            result = runner.invoke(validate_command, [str(target), "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_validation_errors_produce_exit_3(self, tmp_path):
        target = tmp_path / "bad.yaml"
        target.write_text("not: valid: yaml: content")
        runner = CliRunner()
        with patch("xyz_platform.commands.validate.run_validate_command.ValidateCommand.execute", return_value=False):
            with patch(
                "xyz_platform.commands.validate.run_validate_command.ValidateCommand.has_validation_errors",
                return_value=True,
            ):
                result = runner.invoke(validate_command, [str(target), "--work-path", str(tmp_path)])
        assert result.exit_code == 3
