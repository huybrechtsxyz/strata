"""Tests for `strata deploy lock status|release|history` commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.lock_deploy_command import (
    LockHistoryCommand,
    LockReleaseCommand,
    LockStatusCommand,
)
from strata.integrations.lock.base_lock_backend import LockBackendError, LockEntry

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_lock_entry(
    deployment: str = "prod",
    holder: str = "alice",
    hostname: str = "box-01",
) -> LockEntry:
    return LockEntry(
        lock_id="aaaabbbb-0000-1111-2222-333344445555",
        deployment=deployment,
        holder=holder,
        hostname=hostname,
        pid=12345,
        acquired_at="2026-06-16T10:00:00Z",
        expires_at="2026-06-16T18:00:00Z",
        reason="strata deploy run",
    )


def _make_status_command(tmp_path: Path, **kwargs) -> LockStatusCommand:
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = LockStatusCommand(work_path=str(tmp_path), file="deploy.yaml", **kwargs)
    cmd._work_path = tmp_path
    cmd._deployment_service = None
    cmd._output_format = "json"
    cmd._output_quiet = False
    cmd._output_data = {}
    return cmd


def _make_release_command(tmp_path: Path, force: bool = False, **kwargs) -> LockReleaseCommand:
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = LockReleaseCommand(work_path=str(tmp_path), file="deploy.yaml", force=force, **kwargs)
    cmd._work_path = tmp_path
    cmd._deployment_service = None
    cmd._output_format = "json"
    cmd._output_quiet = False
    cmd._output_data = {}
    return cmd


def _make_deployment_service(name: str = "prod") -> MagicMock:
    svc = MagicMock()
    svc.model.meta.name = name
    return svc


# ---------------------------------------------------------------------------
# CliRunner smoke tests (invoke via Click)
# ---------------------------------------------------------------------------


class TestDeployLockCli:
    def test_status_help(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["lock", "status", "--help"])
        assert result.exit_code == 0
        assert "lock" in result.output.lower() or "status" in result.output.lower()

    def test_release_help(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["lock", "release", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output

    def test_status_mocked_execute(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.lock_deploy_command.LockStatusCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                ["lock", "status", "--file", "deploy.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_release_mocked_execute(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.lock_deploy_command.LockReleaseCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                ["lock", "release", "--file", "deploy.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_release_force_flag(self, tmp_path):
        captured = {}

        def fake_execute(self):
            captured["force"] = self._force
            return True

        runner = CliRunner()
        with patch(
            "strata.commands.deploy.lock_deploy_command.LockReleaseCommand.execute",
            fake_execute,
        ):
            runner.invoke(
                deploy,
                [
                    "lock",
                    "release",
                    "--file",
                    "deploy.yaml",
                    "--work-path",
                    str(tmp_path),
                    "--force",
                ],
            )
        assert captured.get("force") is True


# ---------------------------------------------------------------------------
# LockStatusCommand unit tests
# ---------------------------------------------------------------------------


class TestLockStatusCommand:
    def test_returns_true_when_unlocked(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.status.return_value = None

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True
        backend.status.assert_called_once_with("prod")

    def test_returns_true_when_locked(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        entry = _make_lock_entry()
        backend = MagicMock()
        backend.status.return_value = entry

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True

    def test_returns_false_on_backend_error(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.status.side_effect = LockBackendError("disk full")

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1

    def test_returns_false_when_no_deployment_service(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = None

        result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1

    def test_json_output_unlocked(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("staging")
        backend = MagicMock()
        backend.status.return_value = None

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            cmd._run()

        assert cmd._output_data.get("locked") is False
        assert cmd._output_data.get("deployment") == "staging"

    def test_json_output_locked(self, tmp_path):
        cmd = _make_status_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("staging")
        entry = _make_lock_entry(deployment="staging")
        backend = MagicMock()
        backend.status.return_value = entry

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            cmd._run()

        assert cmd._output_data.get("locked") is True
        assert cmd._output_data.get("lock_id") == entry.lock_id


# ---------------------------------------------------------------------------
# LockReleaseCommand unit tests
# ---------------------------------------------------------------------------


class TestLockReleaseCommand:
    def test_returns_true_when_not_locked(self, tmp_path):
        cmd = _make_release_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.status.return_value = None

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True
        backend.force_release.assert_not_called()

    def test_releases_own_lock(self, tmp_path):
        cmd = _make_release_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")

        import socket

        entry = _make_lock_entry(holder="unknown", hostname=socket.gethostname())
        backend = MagicMock()
        backend.status.return_value = entry

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True
        backend.force_release.assert_called_once_with("prod")

    def test_denies_other_holders_lock_without_force(self, tmp_path):
        cmd = _make_release_command(tmp_path, force=False)
        cmd._deployment_service = _make_deployment_service("prod")

        entry = _make_lock_entry(holder="bob", hostname="different-machine-xyz")
        backend = MagicMock()
        backend.status.return_value = entry

        with (
            patch(
                "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
                return_value=backend,
            ),
            patch.dict("os.environ", {"GITHUB_ACTOR": "", "USER": "alice", "USERNAME": "alice"}),
        ):
            result = cmd._run()

        assert result is False
        assert cmd._contention is True
        assert cmd.has_validation_errors() is True
        backend.force_release.assert_not_called()

    def test_force_releases_other_holders_lock(self, tmp_path):
        cmd = _make_release_command(tmp_path, force=True)
        cmd._deployment_service = _make_deployment_service("prod")

        entry = _make_lock_entry(holder="bob", hostname="different-machine-xyz")
        backend = MagicMock()
        backend.status.return_value = entry

        with (
            patch(
                "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
                return_value=backend,
            ),
            patch.dict("os.environ", {"GITHUB_ACTOR": "", "USER": "alice", "USERNAME": "alice"}),
        ):
            result = cmd._run()

        assert result is True
        assert cmd._contention is False
        backend.force_release.assert_called_once_with("prod")

    def test_returns_false_on_backend_error_during_status(self, tmp_path):
        cmd = _make_release_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.status.side_effect = LockBackendError("backend down")

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1

    def test_returns_false_on_backend_error_during_release(self, tmp_path):
        cmd = _make_release_command(tmp_path, force=True)
        cmd._deployment_service = _make_deployment_service("prod")

        import socket

        entry = _make_lock_entry(holder="unknown", hostname=socket.gethostname())
        backend = MagicMock()
        backend.status.return_value = entry
        backend.force_release.side_effect = LockBackendError("release failed")

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1

    def test_has_validation_errors_false_by_default(self, tmp_path):
        cmd = _make_release_command(tmp_path)
        assert cmd.has_validation_errors() is False

    def test_json_output_on_release(self, tmp_path):
        cmd = _make_release_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")

        import socket

        entry = _make_lock_entry(holder="unknown", hostname=socket.gethostname())
        backend = MagicMock()
        backend.status.return_value = entry

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            cmd._run()

        assert cmd._output_data.get("released") is True
        assert cmd._output_data.get("deployment") == "prod"


# ---------------------------------------------------------------------------
# LockHistoryCommand unit tests
# ---------------------------------------------------------------------------


def _make_history_command(tmp_path: Path, last: int = 10, **kwargs) -> LockHistoryCommand:
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = LockHistoryCommand(work_path=str(tmp_path), file="deploy.yaml", last=last, **kwargs)
    cmd._work_path = tmp_path
    cmd._deployment_service = None
    cmd._output_format = "json"
    cmd._output_quiet = False
    cmd._output_data = {}
    return cmd


class TestDeployLockHistoryCli:
    def test_history_help(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["lock", "history", "--help"])
        assert result.exit_code == 0
        assert "--last" in result.output

    def test_history_mocked_execute(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.lock_deploy_command.LockHistoryCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                deploy,
                ["lock", "history", "--file", "deploy.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_history_last_flag(self, tmp_path):
        captured = {}

        def fake_execute(self):
            captured["last"] = self._last
            return True

        runner = CliRunner()
        with patch(
            "strata.commands.deploy.lock_deploy_command.LockHistoryCommand.execute",
            fake_execute,
        ):
            runner.invoke(
                deploy,
                [
                    "lock",
                    "history",
                    "--file",
                    "deploy.yaml",
                    "--work-path",
                    str(tmp_path),
                    "--last",
                    "5",
                ],
            )
        assert captured.get("last") == 5


class TestLockHistoryCommand:
    def test_returns_true_with_no_history(self, tmp_path):
        cmd = _make_history_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.history.return_value = []

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True
        assert cmd._output_data.get("entries") == []

    def test_returns_true_with_entries(self, tmp_path):
        cmd = _make_history_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        entries = [
            _make_lock_entry(deployment="prod"),
            _make_lock_entry(deployment="prod", holder="bob"),
        ]
        backend = MagicMock()
        backend.history.return_value = entries

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is True
        assert len(cmd._output_data["entries"]) == 2

    def test_json_entries_have_expected_keys(self, tmp_path):
        cmd = _make_history_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.history.return_value = [_make_lock_entry()]

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            cmd._run()

        entry = cmd._output_data["entries"][0]
        for key in ("lock_id", "holder", "hostname", "pid", "acquired_at", "reason"):
            assert key in entry

    def test_passes_last_param_to_backend(self, tmp_path):
        cmd = _make_history_command(tmp_path, last=3)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.history.return_value = []

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            cmd._run()

        backend.history.assert_called_once_with("prod", limit=3)

    def test_returns_false_when_no_deployment_service(self, tmp_path):
        cmd = _make_history_command(tmp_path)
        cmd._deployment_service = None

        result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1

    def test_returns_false_on_backend_error(self, tmp_path):
        cmd = _make_history_command(tmp_path)
        cmd._deployment_service = _make_deployment_service("prod")
        backend = MagicMock()
        backend.history.side_effect = LockBackendError("backend error")

        with patch(
            "strata.commands.deploy.lock_deploy_command._resolve_lock_backend",
            return_value=backend,
        ):
            result = cmd._run()

        assert result is False
        assert len(cmd._errors) == 1
