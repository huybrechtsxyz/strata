"""Tests for ShutdownCoordinator (ADR-0028).

Tests signal handling, process termination, lock release, and the
module-level process registry used by run_command().
"""

import signal
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from strata.utils.shutdown_coordinator import (
    ShutdownCoordinator,
    deregister_process,
    get_active,
    register_process,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_coordinator(name: str = "test-deploy") -> ShutdownCoordinator:
    """Return an un-activated coordinator with mock lock objects."""
    coord = ShutdownCoordinator(
        lock_backend=MagicMock(),
        lock_handle=MagicMock(),
        deployment_name=name,
    )
    return coord


def _make_proc(running: bool = True) -> MagicMock:
    """Return a mock subprocess.Popen."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = 12345
    proc.poll.return_value = None if running else 0
    proc.wait.return_value = 0
    return proc


# ===========================================================================
# ShutdownCoordinator unit tests
# ===========================================================================


class TestShutdownCoordinator:
    def test_activate_sets_global(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "my-deploy")
        try:
            assert get_active() is coord
        finally:
            coord.deactivate()

    def test_deactivate_clears_global(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "my-deploy")
        coord.deactivate()
        assert get_active() is None

    def test_deactivate_marks_done(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "my-deploy")
        coord.deactivate()
        assert coord._done.is_set()

    def test_shutdown_releases_lock(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = _make_coordinator()
        coord._lock_backend = backend
        coord._lock_handle = handle

        with pytest.raises(SystemExit) as exc:
            coord.shutdown("test")
        assert exc.value.code == 1
        backend.release.assert_called_once_with(handle)

    def test_shutdown_exits_1(self):
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None
        with pytest.raises(SystemExit) as exc:
            coord.shutdown("test")
        assert exc.value.code == 1

    def test_shutdown_reentrant_guard(self):
        """Second call to shutdown() is a no-op."""
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None

        with pytest.raises(SystemExit):
            coord.shutdown("first")

        # Mark as "handled" so we can check second call doesn't re-exit
        backend = MagicMock()
        coord._lock_backend = backend
        coord.shutdown("second")  # must NOT call sys.exit again
        backend.release.assert_not_called()

    def test_shutdown_terminates_registered_processes(self):
        proc = _make_proc(running=True)
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None
        coord._register(proc)

        with pytest.raises(SystemExit):
            coord.shutdown("test")

        proc.send_signal.assert_called_once_with(signal.SIGTERM)

    def test_shutdown_skips_already_exited_process(self):
        proc = _make_proc(running=False)
        proc.poll.return_value = 0  # already done
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None
        coord._register(proc)

        with pytest.raises(SystemExit):
            coord.shutdown("test")

        proc.send_signal.assert_not_called()

    def test_lock_release_failure_does_not_raise(self):
        backend = MagicMock()
        backend.release.side_effect = RuntimeError("backend unavailable")
        handle = MagicMock()
        coord = _make_coordinator()
        coord._lock_backend = backend
        coord._lock_handle = handle

        with pytest.raises(SystemExit):
            coord.shutdown("test")  # must not propagate RuntimeError

    def test_no_lock_shutdown_still_exits(self):
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None
        with pytest.raises(SystemExit) as exc:
            coord.shutdown("test")
        assert exc.value.code == 1

    def test_add_child_fanned_out_on_shutdown(self):
        parent = _make_coordinator("parent")
        parent._lock_backend = None
        parent._lock_handle = None

        child = _make_coordinator("child")
        child._lock_backend = MagicMock()
        child._lock_handle = MagicMock()
        parent.add_child(child)

        with pytest.raises(SystemExit):
            parent.shutdown("test")

        # Child lock should have been released
        child._lock_backend.release.assert_called_once()


# ===========================================================================
# Process registry (module-level helpers)
# ===========================================================================


class TestProcessRegistry:
    def setup_method(self):
        # Ensure clean state before each test
        import strata.utils.shutdown_coordinator as sc

        sc._active = None

    def test_register_with_no_active_coordinator_is_noop(self):
        proc = _make_proc()
        register_process(proc)  # must not raise

    def test_deregister_with_no_active_coordinator_is_noop(self):
        proc = _make_proc()
        deregister_process(proc)  # must not raise

    def test_register_adds_to_active_coordinator(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")
        try:
            proc = _make_proc()
            register_process(proc)
            assert proc in coord._procs
        finally:
            coord.deactivate()

    def test_deregister_removes_from_active_coordinator(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")
        try:
            proc = _make_proc()
            register_process(proc)
            deregister_process(proc)
            assert proc not in coord._procs
        finally:
            coord.deactivate()

    def test_multiple_processes_all_terminated_on_shutdown(self):
        coord = _make_coordinator()
        coord._lock_backend = None
        coord._lock_handle = None

        procs = [_make_proc() for _ in range(3)]
        for p in procs:
            coord._register(p)

        with pytest.raises(SystemExit):
            coord.shutdown("test")

        for p in procs:
            p.send_signal.assert_called_once_with(signal.SIGTERM)


# ===========================================================================
# Signal handler integration
# ===========================================================================


class TestSignalHandlerIntegration:
    @pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not supported on Windows")
    def test_sigterm_handler_installed_and_restored(self):
        original = signal.getsignal(signal.SIGTERM)
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")

        # Handler should now differ from the original
        current = signal.getsignal(signal.SIGTERM)
        assert current != original

        coord.deactivate()
        # Should be restored
        assert signal.getsignal(signal.SIGTERM) == original

    def test_sigint_handler_installed_and_restored(self):
        original = signal.getsignal(signal.SIGINT)
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")

        current = signal.getsignal(signal.SIGINT)
        assert current != original

        coord.deactivate()
        assert signal.getsignal(signal.SIGINT) == original

    def test_sigint_triggers_shutdown(self):
        """Simulates Ctrl-C: SIGINT → coordinator.shutdown()."""
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")

        with pytest.raises(SystemExit) as exc:
            signal.raise_signal(signal.SIGINT)

        assert exc.value.code == 1
        backend.release.assert_called_once_with(handle)


# ===========================================================================
# atexit safety net
# ===========================================================================


class TestAtexitSafetyNet:
    def test_atexit_handler_releases_lock_when_not_deactivated(self):
        """Simulates process exit without deactivate() — atexit must release lock."""
        backend = MagicMock()
        handle = MagicMock()
        coord = _make_coordinator()
        coord._lock_backend = backend
        coord._lock_handle = handle
        # Don't call deactivate() — simulate unexpected exit
        coord._atexit_handler()
        backend.release.assert_called_once_with(handle)

    def test_atexit_handler_noop_after_deactivate(self):
        backend = MagicMock()
        handle = MagicMock()
        backend2 = MagicMock()
        coord = ShutdownCoordinator.activate(backend, handle, "test")
        coord.deactivate()  # marks _done = True

        coord._lock_backend = backend2
        coord._atexit_handler()  # must be a no-op
        backend2.release.assert_not_called()
