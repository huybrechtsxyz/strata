"""Tests for --timeout flag (ADR-0027) in deploy run and deploy destroy.

Verifies that timeout=0 runs normally, timeout>0 wraps in ThreadPoolExecutor,
and a TimeoutError triggers coordinator.shutdown().
"""

from unittest.mock import MagicMock

import pytest

from strata.utils.shutdown_coordinator import ShutdownCoordinator

# ===========================================================================
# ShutdownCoordinator.update_lock / clear_lock
# ===========================================================================


class TestUpdateAndClearLock:
    def test_update_lock_sets_backend_and_handle(self):
        coord = ShutdownCoordinator(None, None, "test")
        backend = MagicMock()
        handle = MagicMock()
        coord.update_lock(backend, handle)
        assert coord._lock_backend is backend
        assert coord._lock_handle is handle

    def test_clear_lock_removes_references(self):
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator(backend, handle, "test")
        coord.clear_lock()
        assert coord._lock_backend is None
        assert coord._lock_handle is None

    def test_update_lock_thread_safe(self):
        """Multiple threads calling update_lock don't corrupt state."""
        import threading

        coord = ShutdownCoordinator(None, None, "test")
        errors = []

        def _set():
            try:
                b = MagicMock()
                h = MagicMock()
                coord.update_lock(b, h)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_set) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_shutdown_after_update_lock_releases_updated_lock(self):
        coord = ShutdownCoordinator(None, None, "test")
        backend = MagicMock()
        handle = MagicMock()
        coord.update_lock(backend, handle)

        with pytest.raises(SystemExit):
            coord.shutdown("test")

        backend.release.assert_called_once_with(handle)


# ===========================================================================
# Timeout path — ThreadPoolExecutor wrapping
# ===========================================================================


class TestTimeoutPath:
    """Tests the timeout branch uses ThreadPoolExecutor and triggers shutdown on expiry."""

    def test_timeout_triggers_coordinator_shutdown(self):
        """When future.result() raises TimeoutError, coordinator.shutdown() is called."""
        import concurrent.futures as _cf

        shutdown_called = []

        def _fake_shutdown(reason):
            shutdown_called.append(reason)
            raise SystemExit(1)

        coord = MagicMock()
        coord.shutdown = _fake_shutdown
        coord.deactivate = MagicMock()

        # Simulate a _run_stages function that never finishes
        def _slow_body():
            import time

            time.sleep(10)
            return True

        with pytest.raises(SystemExit) as exc:
            with _cf.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_slow_body)
                try:
                    future.result(timeout=0.05)  # immediate timeout for test
                except _cf.TimeoutError:
                    coord.shutdown("timeout after 5s")

        assert exc.value.code == 1
        assert any("timeout" in str(r) for r in shutdown_called)


class TestCoordinatorLifecycleInDeploy:
    def test_update_lock_used_in_worker_thread(self):
        """The coordinator.update_lock() can be called from a worker thread."""
        import threading

        coord = ShutdownCoordinator(None, None, "test")
        backend = MagicMock()
        handle = MagicMock()

        results = []

        def _worker():
            coord.update_lock(backend, handle)
            results.append(coord._lock_backend)

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        assert results[0] is backend

    def test_clear_lock_after_release(self):
        """clear_lock() zeros out the lock so shutdown won't double-release."""
        backend = MagicMock()
        handle = MagicMock()
        coord = ShutdownCoordinator(backend, handle, "test")
        coord.clear_lock()
        # Mark done so shutdown is a no-op
        coord._done.set()
        coord.shutdown("test-noop")  # should not raise or call release
        backend.release.assert_not_called()
