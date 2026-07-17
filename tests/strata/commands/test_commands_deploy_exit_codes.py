"""Tests for exit code 4 (lock conflict) behavior.

Covers ADR-0004 Implementation Plan — three scenarios:
  - Lock conflict       → exit 4
  - System failure      → exit 1
  - Success             → exit 0 (implicit)

Also verifies the LockConflictError / LockTimeoutError class hierarchy.
"""

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
