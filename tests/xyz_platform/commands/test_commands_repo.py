"""Tests for the `repo` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from xyz_platform.commands.cli_repo import repo_group


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
