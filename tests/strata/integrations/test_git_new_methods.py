"""Tests for the fetch / checkout / rev_parse methods added to GitIntegration."""

from unittest.mock import patch

from strata.integrations.git import GitIntegration
from strata.models.integration_model import IntegrationModel
from strata.utils.system import CommandResult


def _make_git() -> GitIntegration:
    """Return a GitIntegration instance with availability pre-stubbed."""
    config = IntegrationModel(
        name="git",
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


class TestGitFetch:
    def test_fetch_calls_fetch_origin_with_tags(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            result = git.fetch("/repo")
        mock_run.assert_called_once_with(["fetch", "origin", "--tags"], cwd="/repo", timeout=180)
        assert result.returncode == 0

    def test_fetch_without_tags_omits_flag(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            git.fetch("/repo", tags=False)
        mock_run.assert_called_once_with(["fetch", "origin"], cwd="/repo", timeout=180)

    def test_fetch_custom_timeout(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            git.fetch("/repo", timeout=60)
        assert mock_run.call_args[1]["timeout"] == 60

    def test_fetch_returns_failure_result_when_git_errors(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_fail("network error")):
            result = git.fetch("/repo")
        assert result.returncode == 1
        assert "network error" in result.stderr


class TestGitCheckout:
    def test_checkout_detach_by_default(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            git.checkout("/repo", ref="v1.2.3")
        mock_run.assert_called_once_with(["checkout", "--detach", "v1.2.3"], cwd="/repo", timeout=60)

    def test_checkout_without_detach(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            git.checkout("/repo", ref="main", detach=False)
        mock_run.assert_called_once_with(["checkout", "main"], cwd="/repo", timeout=60)

    def test_checkout_returns_failure_when_ref_not_found(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_fail("pathspec 'v9.9.9' not found")):
            result = git.checkout("/repo", ref="v9.9.9")
        assert result.returncode == 1

    def test_checkout_custom_timeout(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok()) as mock_run:
            git.checkout("/repo", ref="main", timeout=120)
        assert mock_run.call_args[1]["timeout"] == 120


class TestGitRevParse:
    def test_rev_parse_head_by_default(self):
        sha = "abc1234" * 5 + "ab"
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok(sha)) as mock_run:
            result = git.rev_parse("/repo")
        mock_run.assert_called_once_with(["rev-parse", "HEAD"], cwd="/repo", timeout=30)
        assert result.stdout == sha

    def test_rev_parse_custom_ref(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_ok("deadbeef")) as mock_run:
            git.rev_parse("/repo", ref="v1.0.0")
        mock_run.assert_called_once_with(["rev-parse", "v1.0.0"], cwd="/repo", timeout=30)

    def test_rev_parse_failure_returns_nonzero(self):
        git = _make_git()
        with patch.object(git, "_run_integration", return_value=_fail("unknown revision")):
            result = git.rev_parse("/repo", ref="bad-ref")
        assert result.returncode == 1
