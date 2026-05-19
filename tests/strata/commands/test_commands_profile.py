"""Tests for the `profile` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_profile import profile_group


class TestProfileAdd:
    def test_add_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.profile.add_profile_command.AddProfileCommand.execute", return_value=True):
            result = runner.invoke(profile_group, ["add", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_add_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(profile_group, ["add", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_add_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.profile.add_profile_command.AddProfileCommand.execute", return_value=False):
            result = runner.invoke(profile_group, ["add", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestProfileRemove:
    def test_remove_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.profile.remove_profile_command.RemoveProfileCommand.execute", return_value=True):
            result = runner.invoke(profile_group, ["remove", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_remove_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(profile_group, ["remove", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


class TestProfileList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.profile.list_profile_command.ListProfileCommand.execute", return_value=True):
            result = runner.invoke(profile_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_name_filter(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.profile.list_profile_command.ListProfileCommand.execute", return_value=True):
            result = runner.invoke(profile_group, ["list", "--name", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestProfileActivate:
    def test_activate_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.profile.activate_profile_command.ActivateProfileCommand.execute", return_value=True
        ):
            result = runner.invoke(profile_group, ["activate", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_activate_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(profile_group, ["activate", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


class TestProfileShow:
    def test_show_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.ref.list_profile_path_command.ListProfilePathCommand.execute", return_value=True):
            result = runner.invoke(profile_group, ["show", "staging", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_show_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(profile_group, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code == 2
