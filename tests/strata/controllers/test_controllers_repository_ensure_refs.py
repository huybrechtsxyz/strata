"""Tests for RepositoryController.ensure_remote_refs."""

from unittest.mock import MagicMock, patch

from strata.controllers.repository_controller import RepositoryController
from strata.integrations.git import GitIntegration, GitStatusResult
from strata.models.repository_model import RemoteModel, RemoteType
from strata.utils.system import CommandResult


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="", command="git", duration_ms=0.0)


def _fail(stderr: str = "error") -> CommandResult:
    return CommandResult(returncode=1, stdout="", stderr=stderr, command="git", duration_ms=0.0)


def _clean_status() -> GitStatusResult:
    s = GitStatusResult()
    # all list fields default to empty — is_clean returns True
    return s


def _dirty_status() -> GitStatusResult:
    s = GitStatusResult()
    s.staged = ["modified-file.tf"]
    return s


def _make_config_service(remote_name: str, ref: str, remote_type: RemoteType = RemoteType.GITOPS):
    remote = RemoteModel(
        name=remote_name,
        type=remote_type,
        repository="https://github.com/org/repo.git",
        reference=ref,
        source_path="terraform",
        deploy_path=remote_name,
    )
    config_model = MagicMock()
    config_model.spec = MagicMock()
    config_model.spec.remotes = [remote]

    config_service = MagicMock()
    config_service.model = config_model
    return config_service


class TestEnsureRemoteRefsNoRemotes:
    def test_empty_remotes_returns_true(self, tmp_path):
        ctrl = RepositoryController()
        config_service = MagicMock()
        config_service.model.spec.remotes = []
        ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})
        assert ok is True
        assert refs == {}

    def test_no_model_returns_true(self, tmp_path):
        ctrl = RepositoryController()
        config_service = MagicMock()
        config_service.model = None
        ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})
        assert ok is True

    def test_gitops_without_reference_is_skipped(self, tmp_path):
        """A GITOPS remote whose reference is None/empty is excluded from ensure_remote_refs."""
        # Use a mock object to bypass RemoteModel validation (reference is required on the model)
        remote = MagicMock()
        remote.name = "tf_landscape"
        remote.type = RemoteType.GITOPS
        remote.reference = None  # no pin — should be skipped
        remote.deploy_path = "tf_landscape"
        remote.source_path = "terraform"
        config_service = MagicMock()
        config_service.model.spec.remotes = [remote]
        ctrl = RepositoryController()
        ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})
        assert ok is True
        assert refs == {}

    def test_bundled_remote_is_ignored(self, tmp_path):
        """BUNDLED remotes are excluded regardless of reference."""
        remote = MagicMock()
        remote.name = "bundled_stuff"
        remote.type = RemoteType.BUNDLED
        remote.reference = "v1.0.0"
        config_service = MagicMock()
        config_service.model.spec.remotes = [remote]
        ctrl = RepositoryController()
        ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})
        assert ok is True
        assert refs == {}


class TestEnsureRemoteRefsRemoteNotFetched:
    def test_missing_remote_dir_produces_error(self, tmp_path):
        config_service = _make_config_service("tf_landscape", "v1.2.3")
        ctrl = RepositoryController()
        with patch.object(ctrl, "_get_git_integration", return_value=MagicMock(spec=GitIntegration)):
            ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})
        assert not ok
        errors = ctrl.get_errors()
        assert any("tf_landscape" in e for e in errors)
        assert any("config fetch" in e.lower() or "fetched" in e.lower() for e in errors)


class TestEnsureRemoteRefsHappyPath:
    def test_checkout_and_resolve_sha(self, tmp_path):
        remote_name = "tf_landscape"
        ref = "v1.2.3"
        sha = "abc1234abc1234abc1234abc1234abc1234abc123"

        # Create the remote directory so target_path.exists() passes
        remote_dir = tmp_path / remote_name
        remote_dir.mkdir()

        config_service = _make_config_service(remote_name, ref)

        git_mock = MagicMock(spec=GitIntegration)
        git_mock.fetch.return_value = _ok()
        git_mock.status.return_value = (True, _clean_status())
        git_mock.checkout.return_value = _ok()
        git_mock.rev_parse.return_value = _ok(sha)

        ctrl = RepositoryController()
        with patch.object(ctrl, "_get_git_integration", return_value=git_mock):
            ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})

        assert ok is True
        assert refs == {remote_name: sha}
        git_mock.checkout.assert_called_once_with(str(remote_dir), ref=ref, detach=True)
        git_mock.rev_parse.assert_called_once_with(str(remote_dir), ref="HEAD")

    def test_fetch_failure_is_non_fatal(self, tmp_path):
        """Fetch errors are warnings; checkout still proceeds."""
        remote_name = "tf_landscape"
        ref = "v1.2.3"
        sha = "deadbeefdeadbeef"

        remote_dir = tmp_path / remote_name
        remote_dir.mkdir()

        config_service = _make_config_service(remote_name, ref)

        git_mock = MagicMock(spec=GitIntegration)
        git_mock.fetch.return_value = _fail("network unreachable")
        git_mock.status.return_value = (True, _clean_status())
        git_mock.checkout.return_value = _ok()
        git_mock.rev_parse.return_value = _ok(sha)

        ctrl = RepositoryController()
        with patch.object(ctrl, "_get_git_integration", return_value=git_mock):
            ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})

        assert ok is True
        assert refs[remote_name] == sha


class TestEnsureRemoteRefsDirtyTree:
    def test_dirty_tree_produces_error(self, tmp_path):
        remote_name = "tf_landscape"
        remote_dir = tmp_path / remote_name
        remote_dir.mkdir()

        config_service = _make_config_service(remote_name, "v1.0.0")

        git_mock = MagicMock(spec=GitIntegration)
        git_mock.fetch.return_value = _ok()
        git_mock.status.return_value = (True, _dirty_status())

        ctrl = RepositoryController()
        with patch.object(ctrl, "_get_git_integration", return_value=git_mock):
            ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})

        assert not ok
        errors = ctrl.get_errors()
        assert any("uncommitted" in e.lower() for e in errors)
        assert refs == {}


class TestEnsureRemoteRefsCheckoutFailure:
    def test_checkout_failure_produces_error(self, tmp_path):
        remote_name = "tf_landscape"
        remote_dir = tmp_path / remote_name
        remote_dir.mkdir()

        config_service = _make_config_service(remote_name, "v99.0.0")

        git_mock = MagicMock(spec=GitIntegration)
        git_mock.fetch.return_value = _ok()
        git_mock.status.return_value = (True, _clean_status())
        git_mock.checkout.return_value = _fail("pathspec 'v99.0.0' not found")

        ctrl = RepositoryController()
        with patch.object(ctrl, "_get_git_integration", return_value=git_mock):
            ok, refs = ctrl.ensure_remote_refs(config_service, tmp_path, {})

        assert not ok
        errors = ctrl.get_errors()
        assert any("v99.0.0" in e for e in errors)
