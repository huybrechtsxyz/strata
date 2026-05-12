"""Tests for the `ref` command group (env / config / data / secret)."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_ref import ref_group

_FILE_TYPES = ["env", "config", "data", "secret"]


class TestRefAdd:
    def test_add_basic_each_type(self, tmp_path):
        runner = CliRunner()
        for ftype in _FILE_TYPES:
            with patch(
                "xyz_platform.commands.ref.add_profile_path_command.AddProfilePathCommand.execute", return_value=True
            ):
                result = runner.invoke(
                    ref_group,
                    [ftype, "add", "myref", "/some/path", "--profile", "staging", "--work-path", str(tmp_path)],
                )
            assert result.exit_code == 0, f"{ftype} add failed: {result.output}"

    def test_add_missing_args_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(ref_group, ["env", "add", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


class TestRefRemove:
    def test_remove_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.ref.remove_profile_path_command.RemoveProfilePathCommand.execute", return_value=True
        ):
            result = runner.invoke(
                ref_group,
                ["config", "remove", "myref", "--profile", "staging", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_remove_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(ref_group, ["config", "remove", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


class TestRefList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.cli_ref._ListSingleTypeCommand.execute", return_value=True):
            result = runner.invoke(
                ref_group,
                ["env", "list", "--profile", "staging", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0


class TestRefShow:
    def test_show_basic(self, tmp_path):
        runner = CliRunner()
        with patch("xyz_platform.commands.ref.show_ref_command.ShowRefCommand.execute", return_value=True):
            result = runner.invoke(
                ref_group,
                ["env", "show", "myref", "--profile", "staging", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_show_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(ref_group, ["env", "show", "--work-path", str(tmp_path)])
        assert result.exit_code == 2
