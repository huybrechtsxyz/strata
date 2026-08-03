"""Tests for exit code 4 (lock conflict) behavior.

Covers ADR-0004 Implementation Plan — three scenarios:
  - Lock conflict       → exit 4
  - System failure      → exit 1
  - Success             → exit 0 (implicit)

Also verifies the LockConflictError / LockTimeoutError class hierarchy.
"""

from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit

from strata.commands.cli_common import handle_command_exit
from strata.integrations.lock.base_lock_backend import LockConflictError, LockTimeoutError

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestLockErrorHierarchy:
    def test_lock_timeout_error_is_lock_conflict_error(self):
        exc = LockTimeoutError(deployment_name="my-app", timeout_seconds=30, holder="ci-bot")
        assert isinstance(exc, LockConflictError)

    def test_lock_conflict_error_is_not_lock_timeout_error(self):
        exc = LockConflictError(message="held", error_code="LOCK_CONFLICT")
        assert not isinstance(exc, LockTimeoutError)

    def test_lock_timeout_error_message_includes_holder(self):
        exc = LockTimeoutError(deployment_name="my-app", timeout_seconds=60, holder="alice")
        assert "alice" in str(exc)
        assert "my-app" in str(exc)


# ---------------------------------------------------------------------------
# handle_command_exit — exit code routing
# ---------------------------------------------------------------------------


class _CommandStub:
    """Minimal stand-in for a command object."""

    def __init__(self, *, lock_conflict: bool = False, validation_errors: bool = False):
        self._lock_conflict = lock_conflict
        self._validation_errors = validation_errors

    def has_lock_conflict(self) -> bool:
        return self._lock_conflict

    def has_validation_errors(self) -> bool:
        return self._validation_errors


class TestHandleCommandExit:
    def test_success_does_not_raise(self):
        """Exit 0 — no exception is raised for a successful command."""
        cmd = _CommandStub()
        # Should complete without raising Exit
        handle_command_exit(cmd, success=True)

    def test_lock_conflict_exits_4(self):
        """Exit 4 — lock conflict takes priority over all other failure types."""
        cmd = _CommandStub(lock_conflict=True)
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=False)
        assert exc_info.value.exit_code == 4

    def test_lock_conflict_exits_4_even_if_validation_errors_set(self):
        """Exit 4 wins over exit 3 — lock conflict is checked first."""
        cmd = _CommandStub(lock_conflict=True, validation_errors=True)
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=False)
        assert exc_info.value.exit_code == 4

    def test_validation_error_exits_3(self):
        """Exit 3 — validation failure when no lock conflict."""
        cmd = _CommandStub(validation_errors=True)
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=False)
        assert exc_info.value.exit_code == 3

    def test_system_error_exits_1(self):
        """Exit 1 — generic system failure when no lock conflict and no validation errors."""
        cmd = _CommandStub()
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=False)
        assert exc_info.value.exit_code == 1

    def test_command_without_has_lock_conflict_still_exits_1(self):
        """Exit 1 — commands that don't expose has_lock_conflict() never exit 4."""

        class MinimalCommand:
            def has_validation_errors(self) -> bool:
                return False

        cmd = MinimalCommand()
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=False)
        assert exc_info.value.exit_code == 1

    def test_validation_errors_detected_when_success_true(self):
        """Exit 3 — validation errors found even when execute() returned True."""
        cmd = _CommandStub(validation_errors=True)
        with pytest.raises(Exit) as exc_info:
            handle_command_exit(cmd, success=True)
        assert exc_info.value.exit_code == 3


# ---------------------------------------------------------------------------
# BaseDeployCommand._before_execute — exit-code classification bug fix
#
# Previously: deploy run/show, values list/get/resolve (all via BaseDeployCommand)
# never implemented has_validation_errors(), so a real schema/cross-ref validation
# failure fell through to generic exit 1 instead of exit 3, and a missing/unresolvable
# file was also exit 1 instead of the documented exit 2 (usage error).
# ---------------------------------------------------------------------------


class TestBaseDeployCommandExitCodeClassification:
    def _make_cmd(self, tmp_path, file=None):
        from strata.commands.deploy.run_deploy_command import RunDeployCommand

        return RunDeployCommand(work_path=str(tmp_path), file=file)

    def test_has_validation_errors_false_by_default(self, tmp_path):
        cmd = self._make_cmd(tmp_path, file="deploy.yaml")
        assert cmd.has_validation_errors() is False

    def test_missing_file_raises_usage_error(self, tmp_path):
        import click

        cmd = self._make_cmd(tmp_path, file=None)
        with (
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
            patch("strata.deployers.factory.DeployerFactory.load_plugins"),
        ):
            with pytest.raises(click.UsageError):
                cmd._before_execute()
        # A usage error, not a validation error — has_validation_errors() stays False.
        assert cmd.has_validation_errors() is False

    def test_file_not_found_raises_usage_error(self, tmp_path):
        import click

        missing = tmp_path / "does-not-exist.yaml"
        cmd = self._make_cmd(tmp_path, file=str(missing))
        with (
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
            patch("strata.deployers.factory.DeployerFactory.load_plugins"),
        ):
            with pytest.raises(click.UsageError, match="not found"):
                cmd._before_execute()
        assert cmd.has_validation_errors() is False

    def test_validation_failed_flag_set_when_schema_invalid(self, tmp_path):
        """A deployment file that loads but fails Pydantic/cross-ref validation
        must set has_validation_errors() True (exit 3), not fall through to exit 1."""
        deploy_file = tmp_path / "deploy.yaml"
        deploy_file.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\n", encoding="utf-8")

        cmd = self._make_cmd(tmp_path, file=str(deploy_file))

        fake_deployment_service = MagicMock()
        fake_deployment_service.is_validated.return_value = False
        fake_deployment_service.get_validation_errors.return_value = ["spec.workspace is required"]

        fake_resolver = MagicMock()
        fake_resolver.needs_resolution.return_value = False

        with (
            patch("strata.commands.base_command.BaseCommand._before_execute", return_value=True),
            patch("strata.deployers.factory.DeployerFactory.load_plugins"),
            patch.object(cmd, "_load_configuration_service", return_value=MagicMock()),
            patch.object(cmd, "_get_build_path", return_value=tmp_path / "build"),
            patch(
                "strata.services.deployment_extension_resolver.DeploymentExtensionResolver",
                return_value=fake_resolver,
            ),
            patch(
                "strata.commands.deploy.base_deploy_command.DeploymentService.load",
                return_value=fake_deployment_service,
            ),
        ):
            result = cmd._before_execute()

        assert result is False
        assert cmd.has_validation_errors() is True
