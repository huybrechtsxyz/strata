"""Tests for the add / commit / push / pull_rebase / log methods on GitIntegration."""

from unittest.mock import patch

from strata.integrations.git import GitIntegration
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult


def _make_git() -> GitIntegration:
    """Return a GitIntegration instance with availability pre-stubbed."""
    config = IntegrationModel(
        name="git-write-test",
        type="git",
        description="test",
        validation=None,
        authentication=None,
        endpoints=None,
        lifecycle=None,
    )
    git = GitIntegration(config=config)
    return git


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="", command="git", duration_ms=0.0)


def _fail(stderr: str = "error") -> CommandResult:
    return CommandResult(returncode=1, stdout="", stderr=stderr, command="git", duration_ms=0.0)


class TestGitAdd:
    def test_add_single_file(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.add("/repo", ["file.json"])
            assert result.returncode == 0
            mock_run.assert_called_once_with(["add", "--", "file.json"], cwd="/repo", timeout=30)

    def test_add_multiple_files(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.add("/repo", ["a.json", "b.json", "c.json"])
            assert result.returncode == 0
            mock_run.assert_called_once_with(["add", "--", "a.json", "b.json", "c.json"], cwd="/repo", timeout=30)

    def test_add_unavailable(self):
        git = _make_git()
        with patch.object(git, "ensure_available", return_value=(False, "git not found")):
            result = git.add("/repo", ["file.json"])
            assert result.returncode == 1
            assert "not found" in result.stderr


class TestGitCommit:
    def test_commit_success(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.commit("/repo", "chore(audit): deploy-log entry")
            assert result.returncode == 0
            mock_run.assert_called_once_with(
                ["commit", "-m", "chore(audit): deploy-log entry"], cwd="/repo", timeout=30
            )

    def test_commit_unavailable(self):
        git = _make_git()
        with patch.object(git, "ensure_available", return_value=(False, "git not found")):
            result = git.commit("/repo", "message")
            assert result.returncode == 1


class TestGitPush:
    def test_push_default(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.push("/repo")
            assert result.returncode == 0
            mock_run.assert_called_once_with(["push", "origin"], cwd="/repo", timeout=60)

    def test_push_with_remote_and_branch(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.push("/repo", remote="upstream", branch="main")
            assert result.returncode == 0
            mock_run.assert_called_once_with(["push", "upstream", "main"], cwd="/repo", timeout=60)

    def test_push_failure(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_fail("rejected")),
        ):
            result = git.push("/repo")
            assert result.returncode == 1
            assert "rejected" in result.stderr

    def test_push_unavailable(self):
        git = _make_git()
        with patch.object(git, "ensure_available", return_value=(False, "git not found")):
            result = git.push("/repo")
            assert result.returncode == 1


class TestGitPullRebase:
    def test_pull_rebase_success(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.pull_rebase("/repo")
            assert result.returncode == 0
            mock_run.assert_called_once_with(["pull", "--rebase", "origin"], cwd="/repo", timeout=60)

    def test_pull_rebase_custom_remote(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok()) as mock_run,
        ):
            result = git.pull_rebase("/repo", remote="upstream")
            mock_run.assert_called_once_with(["pull", "--rebase", "upstream"], cwd="/repo", timeout=60)

    def test_pull_rebase_unavailable(self):
        git = _make_git()
        with patch.object(git, "ensure_available", return_value=(False, "git not found")):
            result = git.pull_rebase("/repo")
            assert result.returncode == 1


class TestGitLog:
    def test_log_default(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok("abc123\n")) as mock_run,
        ):
            result = git.log("/repo")
            assert result.returncode == 0
            assert "abc123" in result.stdout
            mock_run.assert_called_once_with(["log", "--format=%H", "-1"], cwd="/repo", timeout=30)

    def test_log_custom_format_and_count(self):
        git = _make_git()
        with (
            patch.object(git, "ensure_available", return_value=(True, "")),
            patch.object(git, "_run_integration", return_value=_ok("line1\nline2\nline3\n")) as mock_run,
        ):
            result = git.log("/repo", format="%H %s", count=3)
            assert result.returncode == 0
            mock_run.assert_called_once_with(["log", "--format=%H %s", "-3"], cwd="/repo", timeout=30)

    def test_log_unavailable(self):
        git = _make_git()
        with patch.object(git, "ensure_available", return_value=(False, "git not found")):
            result = git.log("/repo")
            assert result.returncode == 1
