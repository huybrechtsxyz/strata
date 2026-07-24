"""ShutdownCoordinator — ordered SIGTERM/SIGINT/timeout graceful shutdown.

Implements ADR-0028 (SIGTERM Graceful Shutdown) with the registry model
discussed for future rollout/parallel-deploy support.

Usage (in a deploy/destroy command)::

    coordinator = ShutdownCoordinator.activate(
        lock_backend=backend,
        lock_handle=handle,
        deployment_name=name,
    )
    try:
        run_provisioning_stages(...)
    finally:
        coordinator.deactivate()

When SIGTERM, SIGINT, or an explicit ``coordinator.shutdown(reason)`` call
arrives, the coordinator:

1. Terminates all currently active subprocesses (SIGTERM → wait 30s → SIGKILL).
2. Releases the deployment lock.
3. Exits with code 1.

Process tracking
----------------
Deployers do not need to register subprocesses manually.  ``run_command()``
in ``strata.utils.system`` automatically calls
:func:`register_process` / :func:`deregister_process` for every Popen
instance when a coordinator is active, so subprocess termination is
transparent to callers.

Rollout / parallel-deploy extension
-------------------------------------
A future ``RolloutCoordinator`` can add child coordinators via
``coordinator.add_child(child_coordinator)``.  On shutdown the parent fans
out to all children before releasing its own lock.
"""

from __future__ import annotations

import atexit
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any, List, Optional

from strata.logger import get_logger

if TYPE_CHECKING:
    from strata.integrations.lock.base_lock_backend import BaseLockBackend, LockHandle

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level active coordinator — set by activate(), cleared by deactivate()
# ---------------------------------------------------------------------------

_active: Optional["ShutdownCoordinator"] = None
_active_lock = threading.Lock()


def get_active() -> Optional["ShutdownCoordinator"]:
    """Return the currently active coordinator, or None."""
    return _active


def register_process(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Register a subprocess with the active coordinator (no-op if none)."""
    coord = _active
    if coord is not None:
        coord._register(proc)


def deregister_process(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Deregister a subprocess from the active coordinator (no-op if none)."""
    coord = _active
    if coord is not None:
        coord._deregister(proc)


# ---------------------------------------------------------------------------
# ShutdownCoordinator
# ---------------------------------------------------------------------------


class ShutdownCoordinator:
    """Coordinates ordered graceful shutdown for a single deploy/destroy run.

    Lifecycle::

        coordinator = ShutdownCoordinator.activate(
            lock_backend=backend, lock_handle=handle, deployment_name=name
        )
        try:
            ...  # run stages — subprocesses auto-registered via run_command()
        finally:
            coordinator.deactivate()  # restores signal handlers, suppresses atexit

    Calling ``coordinator.shutdown(reason)`` at any point (from a signal
    handler, timeout, or test) triggers the full ordered shutdown sequence and
    calls ``sys.exit(1)``.
    """

    def __init__(
        self,
        lock_backend: "Optional[BaseLockBackend]",
        lock_handle: "Optional[LockHandle]",
        deployment_name: str,
    ) -> None:
        self._lock_backend = lock_backend
        self._lock_handle = lock_handle
        self._name = deployment_name
        self._done = threading.Event()
        self._procs: set = set()  # type: ignore[type-arg]
        self._procs_mutex = threading.Lock()
        # Future: child coordinators for rollout/parallel-deploy
        self._children: List["ShutdownCoordinator"] = []
        # Saved signal handlers — restored by deactivate()
        self._prev_sigterm: Any = None
        self._prev_sigint: Any = None
        self._atexit_registered = False

    # ------------------------------------------------------------------
    # Factory / lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def activate(
        cls,
        lock_backend: "Optional[BaseLockBackend]",
        lock_handle: "Optional[LockHandle]",
        deployment_name: str,
    ) -> "ShutdownCoordinator":
        """Create a coordinator, install signal handlers, and set it as active.

        Must be paired with a ``deactivate()`` call (use ``try/finally``).
        """
        global _active
        coord = cls(lock_backend, lock_handle, deployment_name)

        with _active_lock:
            _active = coord

        # SIGTERM — Unix only (Windows does not support it reliably)
        if sys.platform != "win32":
            coord._prev_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda _s, _f: coord.shutdown("SIGTERM"))

        # SIGINT — all platforms (Ctrl-C / interactive cancel)
        coord._prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda _s, _f: coord.shutdown("SIGINT"))

        # atexit safety net: catches unhandled exceptions and unexpected exits
        atexit.register(coord._atexit_handler)
        coord._atexit_registered = True

        logger.debug("shutdown_coordinator_activated", deployment=deployment_name)
        return coord

    def deactivate(self) -> None:
        """Restore original signal handlers and suppress the atexit hook.

        Call this in the ``finally`` block after normal completion.
        """
        global _active
        with _active_lock:
            if _active is self:
                _active = None

        # Restore saved handlers
        if sys.platform != "win32" and self._prev_sigterm is not None:
            signal.signal(signal.SIGTERM, self._prev_sigterm)
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)

        # Mark done so atexit handler is a no-op
        self._done.set()
        logger.debug("shutdown_coordinator_deactivated", deployment=self._name)

    # ------------------------------------------------------------------
    # Process registry (called by run_command() automatically)
    # ------------------------------------------------------------------

    def _register(self, proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
        with self._procs_mutex:
            self._procs.add(proc)

    def _deregister(self, proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
        with self._procs_mutex:
            self._procs.discard(proc)

    # ------------------------------------------------------------------
    # Rollout / parallel-deploy extension point
    # ------------------------------------------------------------------

    def add_child(self, child: "ShutdownCoordinator") -> None:
        """Register a child coordinator (future rollout support)."""
        self._children.append(child)

    # ------------------------------------------------------------------
    # Shutdown sequence
    # ------------------------------------------------------------------

    def shutdown(self, reason: str = "signal") -> None:
        """Ordered shutdown: kill subprocesses → fan-out children → release lock → exit 1.

        Re-entrant safe: subsequent calls after the first are no-ops.
        """
        if self._done.is_set():
            return
        self._done.set()

        logger.warning(
            "graceful_shutdown_triggered",
            reason=reason,
            deployment=self._name,
        )
        print(  # use print so it's visible even if logging is suppressed
            f"\n[strata] Graceful shutdown ({reason}) for '{self._name}' — stopping subprocesses and releasing lock...",
            file=sys.stderr,
            flush=True,
        )

        # Step 1: terminate all active subprocesses
        self._terminate_all_processes()

        # Step 2: fan out to child coordinators (rollout use case)
        for child in self._children:
            child.shutdown(reason=f"parent:{reason}")

        # Step 3: release the deployment lock
        self._release_lock()

        # Step 4: exit
        sys.exit(1)

    def _terminate_all_processes(self) -> None:
        """Send SIGTERM to all active processes, wait, force-kill stragglers."""
        with self._procs_mutex:
            procs = list(self._procs)

        if not procs:
            return

        logger.info("shutdown_terminating_subprocesses", count=len(procs))

        # Phase 1: send SIGTERM to all
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass  # already gone

        # Phase 2: wait up to 30 seconds for all to exit
        import time as _time

        deadline = _time.monotonic() + 30
        for proc in procs:
            remaining = max(0.0, deadline - _time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                logger.warning("shutdown_force_killing_subprocess", pid=proc.pid)
                try:
                    proc.kill()
                    proc.wait()
                except (ProcessLookupError, OSError):
                    pass

        logger.info("shutdown_subprocesses_terminated", count=len(procs))

    def _release_lock(self) -> None:
        """Release the deployment lock — never raises."""
        if self._lock_backend is None or self._lock_handle is None:
            return
        try:
            self._lock_backend.release(self._lock_handle)
            logger.info("shutdown_lock_released", deployment=self._name)
            print(
                f"[strata] Deployment lock released for '{self._name}'.",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            logger.error("shutdown_lock_release_failed", error=str(exc))

    def _atexit_handler(self) -> None:
        """atexit safety net — called if process exits without deactivate()."""
        if not self._done.is_set():
            logger.warning("shutdown_atexit_triggered", deployment=self._name)
            self._terminate_all_processes()
            self._release_lock()
