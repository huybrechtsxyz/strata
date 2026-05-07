"""Tests for the `repo` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_repo import repo_group
from xyz_platform.commands.repo.add_repo_solution_command import (
    AddRepoSolutionCommand,
    _is_local_path,
)


class TestRepoAdd:
    def test_add_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_with_branch_and_path(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(
                repo_group,
                [
                    "add",
                    "myrepo",
                    "https://github.com/org/myrepo.git",
                    "--branch",
                    "develop",
                    "--path",
                    "repos/custom",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_add_clone_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--clone", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_missing_args_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(repo_group, ["add", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_add_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute", return_value=False
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0


class TestRepoList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(repo_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_name_filter(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(repo_group, ["list", "--name", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestRepoRemove:
    def test_remove_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.remove_repo_solution_command.RemoveRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["remove", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_remove_purge_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.remove_repo_solution_command.RemoveRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["remove", "myrepo", "--purge", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_remove_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(repo_group, ["remove", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


class TestRepoSync:
    def test_sync_all(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(repo_group, ["sync", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sync_single_name(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(repo_group, ["sync", "--name", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sync_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute", return_value=True
        ):
            result = runner.invoke(repo_group, ["sync", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestRepoStatus:
    def test_status_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.status_repo_solution_command.StatusRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_name_filter(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.status_repo_solution_command.StatusRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["status", "--name", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


class TestIsLocalPath:
    """Unit tests for the _is_local_path helper."""

    def test_windows_drive_backslash(self):
        assert _is_local_path(r"C:\repos\xyz") is True

    def test_windows_drive_forward_slash(self):
        assert _is_local_path("C:/repos/xyz") is True

    def test_unc_double_forward_slash(self):
        assert _is_local_path("//server/share") is True

    def test_unc_double_backslash(self):
        assert _is_local_path(r"\\server\share") is True

    def test_https_url_is_not_local(self):
        assert _is_local_path("https://github.com/org/repo.git") is False

    def test_git_ssh_url_is_not_local(self):
        assert _is_local_path("git@github.com:org/repo.git") is False

    def test_relative_path_is_not_local(self):
        assert _is_local_path("repos/myrepo") is False


class TestRepoAddLocalPath:
    """Integration tests for local-path detection inside AddRepoSolutionCommand._run_execution."""

    def _make_command(self, url: str, work_path) -> AddRepoSolutionCommand:
        return AddRepoSolutionCommand(name="myrepo", url=url, work_path=str(work_path))

    def test_valid_local_directory_succeeds_with_type_local(self, tmp_path):
        local_dir = tmp_path / "mylocal"
        local_dir.mkdir()
        cmd = self._make_command(str(local_dir), tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "local"
        assert cmd._added_repo["branch"] == ""

    def test_nonexistent_local_path_fails_with_error(self, tmp_path):
        nonexistent = str(tmp_path / "no_such_dir")
        cmd = self._make_command(nonexistent, tmp_path)

        result = cmd._run_execution()

        assert result is False
        assert any("does not exist" in e for e in cmd._errors)

    def test_local_path_pointing_to_file_fails_with_error(self, tmp_path):
        local_file = tmp_path / "myfile.txt"
        local_file.write_text("content")
        cmd = self._make_command(str(local_file), tmp_path)

        result = cmd._run_execution()

        assert result is False
        assert any("not a directory" in e for e in cmd._errors)

    def test_https_url_remains_type_gitops(self, tmp_path):
        cmd = self._make_command("https://github.com/org/repo.git", tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "gitops"
        assert cmd._added_repo["branch"] == "main"
