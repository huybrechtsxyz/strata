"""Tests for the `repo` command group."""

from typing import Optional
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from xyz_platform.commands.cli_repo import repo_group
from xyz_platform.commands.repo.add_repo_solution_command import (
    AddRepoSolutionCommand,
    _is_local_path,
)

# ---------------------------------------------------------------------------
# TestIsLocalPath — unit tests for the path-detection helper
# ---------------------------------------------------------------------------


class TestIsLocalPath:
    def test_relative_path_is_local(self):
        assert _is_local_path("repos/myrepo") is True

    def test_relative_path_dot_prefix_is_local(self):
        assert _is_local_path("./repos/myrepo") is True

    def test_windows_absolute_forward_slash_is_local(self):
        assert _is_local_path("C:/repos/xyz") is True

    def test_windows_absolute_backslash_is_local(self):
        assert _is_local_path(r"C:\repos\xyz") is True

    def test_unc_double_forward_slash_is_local(self):
        assert _is_local_path("//server/share") is True

    def test_unc_double_backslash_is_local(self):
        assert _is_local_path(r"\\server\share") is True

    def test_unix_absolute_is_local(self):
        assert _is_local_path("/unix/absolute") is True

    def test_https_url_is_not_local(self):
        assert _is_local_path("https://github.com/org/repo.git") is False

    def test_git_at_url_is_not_local(self):
        assert _is_local_path("git@github.com:org/repo.git") is False

    def test_ssh_url_is_not_local(self):
        assert _is_local_path("ssh://git@github.com/org/repo.git") is False

    def test_http_url_is_not_local(self):
        assert _is_local_path("http://example.com/repo.git") is False


# ---------------------------------------------------------------------------
# TestRepoAdd — CLI wiring tests (execute mocked)
# ---------------------------------------------------------------------------


class TestRepoAdd:
    def test_help_flag_exits_0(self):
        runner = CliRunner()
        result = runner.invoke(repo_group, ["add", "--help"])
        assert result.exit_code == 0

    def test_add_remote_https_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_remote_https_with_branch(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                [
                    "add",
                    "myrepo",
                    "https://github.com/org/myrepo.git",
                    "--branch",
                    "develop",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_add_remote_https_with_path(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                [
                    "add",
                    "myrepo",
                    "https://github.com/org/myrepo.git",
                    "--path",
                    "repos/custom",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_add_remote_https_with_clone(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--clone", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_local_relative_path_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "repos/myrepo", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_local_relative_path_with_explicit_path(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                [
                    "add",
                    "myrepo",
                    "repos/myrepo",
                    "--path",
                    "custom/mount",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_add_local_absolute_path(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", str(tmp_path), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_add_missing_args_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(repo_group, ["add", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_add_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.add_repo_solution_command.AddRepoSolutionCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                repo_group,
                ["add", "myrepo", "https://github.com/org/myrepo.git", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# TestRepoList — CLI wiring tests
# ---------------------------------------------------------------------------


class TestRepoList:
    def test_list_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_name_filter(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["list", "--name", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_output_json(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_output_text(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["list", "--output", "text", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_output_console(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.list_repo_solution_command.ListRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["list", "--output", "console", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TestRepoRemove — CLI wiring tests
# ---------------------------------------------------------------------------


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

    def test_remove_output_json(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.remove_repo_solution_command.RemoveRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["remove", "myrepo", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_remove_output_text(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.remove_repo_solution_command.RemoveRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["remove", "myrepo", "--output", "text", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_remove_missing_name_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(repo_group, ["remove", "--work-path", str(tmp_path)])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# TestRepoSync — CLI wiring tests
# ---------------------------------------------------------------------------


class TestRepoSync:
    def test_sync_basic(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["sync", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sync_name_filter(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["sync", "--name", "myrepo", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sync_force_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["sync", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sync_output_json(self, tmp_path):
        runner = CliRunner()
        with patch(
            "xyz_platform.commands.repo.sync_repo_solution_command.SyncRepoSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(repo_group, ["sync", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TestRepoStatus — CLI wiring tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestAddRepoCommand_LocalPath — _run_execution() unit tests
# ---------------------------------------------------------------------------


class TestAddRepoCommand_LocalPath:
    def _make_command(self, url: str, work_path, path: Optional[str] = None) -> AddRepoSolutionCommand:
        return AddRepoSolutionCommand(name="myrepo", url=url, path=path, work_path=str(work_path))

    def test_valid_existing_directory_succeeds(self, tmp_path):
        local_dir = tmp_path / "mylocal"
        local_dir.mkdir()
        cmd = self._make_command(str(local_dir), tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "local"
        assert cmd._added_repo["branch"] == ""

    def test_relative_path_that_exists_is_local(self, tmp_path, monkeypatch):
        sub_dir = tmp_path / "myrepo"
        sub_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        cmd = self._make_command("myrepo", tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "local"

    def test_nonexistent_local_path_returns_false_with_error(self, tmp_path):
        nonexistent = str(tmp_path / "no_such_dir")
        cmd = self._make_command(nonexistent, tmp_path)

        result = cmd._run_execution()

        assert result is False
        assert any("does not exist" in e for e in cmd._errors)

    def test_local_path_is_file_returns_false_with_error(self, tmp_path):
        local_file = tmp_path / "myfile.txt"
        local_file.write_text("content")
        cmd = self._make_command(str(local_file), tmp_path)

        result = cmd._run_execution()

        assert result is False
        assert any("not a directory" in e for e in cmd._errors)

    def test_https_url_is_gitops_with_branch(self, tmp_path):
        cmd = self._make_command("https://github.com/org/repo.git", tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "gitops"
        assert cmd._added_repo["branch"] == "main"

    def test_git_at_url_is_gitops(self, tmp_path):
        cmd = self._make_command("git@github.com:org/repo.git", tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["type"] == "gitops"

    def test_custom_path_is_stored_correctly(self, tmp_path):
        cmd = self._make_command("https://github.com/org/repo.git", tmp_path, path="custom/mount")

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["path"] == "custom/mount"

    def test_default_path_is_repos_slash_name(self, tmp_path):
        cmd = self._make_command("https://github.com/org/repo.git", tmp_path)

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", return_value=(True, [])),
        ):
            result = cmd._run_execution()

        assert result is True
        assert cmd._added_repo["path"] == "repos/myrepo"


# ---------------------------------------------------------------------------
# TestAddRepoCommand_WorkspaceRegen — generate_workspace is called after save
# ---------------------------------------------------------------------------


class TestAddRepoCommand_WorkspaceRegen:
    def test_generate_workspace_called_after_successful_save(self, tmp_path):
        cmd = AddRepoSolutionCommand(
            name="myrepo",
            url="https://github.com/org/repo.git",
            work_path=str(tmp_path),
        )
        mock_generate = MagicMock(return_value=(True, []))

        with (
            patch.object(cmd._solution_controller, "add_repository", return_value=(True, [])),
            patch.object(cmd._solution_controller, "get_messages", return_value=[]),
            patch.object(cmd._solution_controller, "save", return_value=(True, [])),
            patch.object(cmd._solution_controller, "generate_workspace", mock_generate),
        ):
            result = cmd._run_execution()

        assert result is True
        mock_generate.assert_called_once()
