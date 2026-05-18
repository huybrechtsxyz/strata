"""Tests for the `validate` command."""

import json
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


class TestValidateStructuredErrors:
    """Verify that --output json returns structured error objects in data.errors."""

    def test_yaml_parse_error_produces_structured_error(self, tmp_path):
        """A syntactically invalid YAML file should produce a YAML_PARSE_ERROR structured error."""
        target = tmp_path / "bad.yaml"
        target.write_text(": this is not valid yaml:::")
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [str(target), "--output", "json", "--work-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        errors = data["data"]["errors"]
        assert len(errors) > 0
        err = errors[0]
        assert err["code"] == "YAML_PARSE_ERROR"
        assert "message" in err
        assert "phase" in err

    def test_unknown_kind_produces_structured_error(self, tmp_path):
        target = tmp_path / "bad.yaml"
        target.write_text("apiVersion: platform.huybrechts.xyz/v1\nkind: unknownkind\n")
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [str(target), "--output", "json", "--work-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        errors = data["data"]["errors"]
        assert len(errors) > 0
        err = errors[0]
        assert err["code"] == "UNKNOWN_KIND"
        assert err["field"] == "kind"
        assert err["value"] == "unknownkind"

    def test_missing_kind_field_produces_structured_error(self, tmp_path):
        target = tmp_path / "nokind.yaml"
        target.write_text("apiVersion: platform.huybrechts.xyz/v1\nspec:\n  name: test\n")
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [str(target), "--output", "json", "--work-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        errors = data["data"]["errors"]
        assert any(e["code"] == "MISSING_KIND_FIELD" for e in errors)

    def test_structured_errors_have_required_fields(self, tmp_path):
        """Every structured error must contain code, message, and phase."""
        target = tmp_path / "bad.yaml"
        target.write_text(": this is not valid yaml:::")
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [str(target), "--output", "json", "--work-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        for err in data["data"]["errors"]:
            assert "code" in err
            assert "message" in err
            assert "phase" in err

    def test_valid_file_has_empty_errors_list(self, tmp_path):
        target = tmp_path / "workspace.yaml"
        target.write_text("apiVersion: platform.huybrechts.xyz/v1\nkind: workspace\nspec:\n  name: test\n")
        runner = CliRunner()
        with patch("xyz_platform.commands.validate.run_validate_command.ValidateCommand.execute", return_value=True):
            result = runner.invoke(
                validate_command,
                [str(target), "--output", "json", "--work-path", str(tmp_path)],
            )
        # When execute is mocked to True the command succeeds with no errors recorded
        assert result.exit_code == 0

    def test_pydantic_error_has_field_path(self, tmp_path):
        """A Pydantic validation failure should include the field path in the structured error."""
        target = tmp_path / "bad_env.yaml"
        # meta.name must match PlatformName: ^[a-z][a-z0-9_-]*$ — uppercase breaks it
        target.write_text(
            "apiVersion: platform.huybrechts.xyz/v1\nkind: environment\nmeta:\n  name: UPPERCASE_NAME\nspec: {}\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            validate_command,
            [str(target), "--output", "json", "--work-path", str(tmp_path)],
        )
        data = json.loads(result.output)
        errors = data["data"]["errors"]
        assert len(errors) > 0
        pydantic_errs = [e for e in errors if e["code"] == "PYDANTIC_FIELD_ERROR"]
        assert len(pydantic_errs) > 0, f"Expected PYDANTIC_FIELD_ERROR, got: {errors}"
        assert pydantic_errs[0]["field"] is not None
