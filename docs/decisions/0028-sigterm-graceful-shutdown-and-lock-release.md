# SIGTERM Graceful Shutdown and Deployment Lock Release

- Status: implemented
- Date: 2026-07-11
- Implemented: 2026-07-24

## Context and Problem Statement

When `strata deploy run` or `strata deploy destroy` is running and the process receives
a SIGTERM — from a container orchestrator stopping the pod, a CI runner hitting its job
time limit, or an operator running `kill` — the current code exits immediately without
releasing the deployment lock. The lock remains held until it expires or an operator
manually runs `strata deploy lock release`.

This creates a blocking failure for every subsequent deployment attempt against that
target. In a CI/CD pipeline this typically manifests as every downstream job failing
with a lock conflict error (exit 4), requiring manual operator intervention before work
can resume.

The same shutdown sequence is also invoked by the timeout mechanism (ADR-0027) on
expiry and should be invoked on CTRL-C (SIGINT) during interactive use.

## Decision Drivers

- **Lock release must be guaranteed** — The lock release must happen even when the
  process is interrupted mid-provisioning.
- **Subprocess must be stopped first** — If strata releases the lock and exits while
  Terraform is still running, Terraform will continue modifying infrastructure in an
  unlocked state. The provisioner subprocess must be terminated before the lock is released.
- **Ordered shutdown** — Stop subprocess → wait → force-kill → release lock → exit.
  This order must not be changed.
- **Re-entrant safe** — If a second signal arrives during shutdown, the handler must
  not start a second shutdown sequence.
- **Windows compatibility** — `SIGTERM` is not reliably delivered on Windows
  (Python maps it to `SIGABRT` behavior). The mechanism must degrade gracefully.
- **No new dependencies** — stdlib only.

## Considered Options

### Option A: No signal handling (current state)
- Process exits immediately on SIGTERM. Lock is not released.
- **Rejected:** Causes lock abandonment and downstream pipeline failures.

### Option B: Global `atexit` handler
- Register an `atexit` handler that releases the lock when the interpreter exits for
  any reason.
- Pro: Simple; catches SIGTERM, normal exit, uncaught exceptions.
- Con: `atexit` handlers do not run on SIGKILL or `os._exit()`.
- Con: `atexit` handlers run after the interpreter starts teardown — subprocess
  references may already be garbage-collected, making subprocess termination unreliable.
- Con: Does not stop the running subprocess before releasing the lock.
- **Rejected as sole mechanism:** subprocess must be stopped before lock release; atexit
  cannot guarantee ordering relative to subprocess lifecycle.

### Option C: `signal.signal(SIGTERM, handler)` with subprocess tracking
- Register a SIGTERM handler that tracks the active provisioner subprocess and performs
  an ordered shutdown.
- Pro: Explicit ordering (stop subprocess first, then release lock).
- Pro: Handler can be set and cleared per-provisioner-invocation, scoping it to the
  window when a lock is actually held.
- Con: Signal handlers in Python have restrictions: they run in the main thread only;
  they should do minimal work and set a flag rather than doing blocking I/O.
- Con: Windows does not support SIGTERM; `signal.signal(SIGTERM, ...)` raises
  `OSError` on Windows.

### Option D: Separate watchdog process
- Spawn a lightweight watchdog process that holds the lock and monitors the parent PID.
  If the parent dies, the watchdog releases the lock and exits.
- Pro: Works even on SIGKILL.
- Con: Significant complexity; IPC between parent and watchdog; two processes to manage.
- Con: Watchdog itself could be killed, leaving the same problem.
- **Rejected:** Complexity not justified; SIGKILL is out of scope (nothing can handle it).

## Decision Outcome

Chosen: **Option C — `signal.signal(SIGTERM, handler)` with a shutdown coordinator**,
combined with `atexit` (Option B) as a secondary safety net for normal and exception-driven
exits.

### Shutdown coordinator

A `ShutdownCoordinator` object is created at the start of each long-running command
invocation. It holds a reference to the active subprocess and the lock. It exposes a
`shutdown(reason)` method that performs the ordered sequence. It is re-entrant safe
via a threading `Event` flag.

```python
class ShutdownCoordinator:
    def __init__(self, subprocess_handle, lock_manager, deployment_name: str):
        self._proc = subprocess_handle
        self._lock = lock_manager
        self._name = deployment_name
        self._done = threading.Event()

    def shutdown(self, reason: str = "signal") -> None:
        if self._done.is_set():
            return  # re-entrant guard
        self._done.set()

        logger.warning(f"Shutdown triggered ({reason}) for '{self._name}'")

        # Step 1: stop the subprocess
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("Subprocess did not exit within 30s; sending SIGKILL")
                self._proc.kill()
                self._proc.wait()

        # Step 2: release the deployment lock
        try:
            self._lock.release()
            logger.info(f"Deployment lock released for '{self._name}'")
        except Exception as e:
            logger.error(f"Failed to release lock: {e}")

        # Step 3: exit
        sys.exit(1)
```

### Signal handler registration

The handler is registered **per-invocation** (not globally at import time) so it is
only active while a lock is held. This avoids interfering with non-locking commands.

```python
coordinator = ShutdownCoordinator(proc, lock_manager, deployment_name)

# SIGTERM handler
_original_sigterm = signal.getsignal(signal.SIGTERM)
if sys.platform != "win32":
    signal.signal(signal.SIGTERM, lambda _sig, _frame: coordinator.shutdown("SIGTERM"))

# SIGINT handler (Ctrl-C during interactive use)
_original_sigint = signal.getsignal(signal.SIGINT)
signal.signal(signal.SIGINT, lambda _sig, _frame: coordinator.shutdown("SIGINT"))

# atexit safety net (catches unhandled exceptions, normal exits)
atexit.register(coordinator.shutdown, "atexit")

try:
    run_provisioner(...)
finally:
    # Restore original handlers; mark coordinator done to suppress atexit
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _original_sigterm)
    signal.signal(signal.SIGINT, _original_sigint)
    coordinator._done.set()  # suppress atexit if we got here normally
```

### Windows behaviour

On Windows, `SIGTERM` is not delivered by the OS. The `atexit` handler and the
`SIGINT` handler (which Windows does support) provide the best available coverage.
A console application receiving Ctrl-C on Windows will invoke the SIGINT handler
and perform the ordered shutdown. Programmatic termination via `TerminateProcess`
(what `taskkill /F` uses) bypasses all handlers — equivalent to SIGKILL on Unix —
and is explicitly out of scope.

### Shutdown trigger points

| Trigger                           | Handler                                                           |
| --------------------------------- | ----------------------------------------------------------------- |
| `SIGTERM` (Unix)                  | Signal handler → `coordinator.shutdown("SIGTERM")`                |
| `SIGINT` / Ctrl-C (all platforms) | Signal handler → `coordinator.shutdown("SIGINT")`                 |
| Timeout expiry (ADR-0027)         | Timeout raises `TimeoutError` → `coordinator.shutdown("timeout")` |
| Unhandled exception               | `atexit` handler → `coordinator.shutdown("atexit")`               |
| `TerminateProcess` / SIGKILL      | Not handleable — explicitly out of scope                          |

### Help text epilog addition

The exit code epilog (ADR-0020 Phase 5) for `deploy run` and `deploy destroy` will
include: `"SIGTERM and SIGINT trigger graceful shutdown: subprocess is stopped, lock is
released, then exit 1."`

## Consequences

- **Good:** Deployment lock is released on SIGTERM, SIGINT, timeout, and unhandled
  exception — the four common interrupt scenarios in CI/CD.
- **Good:** Subprocess is guaranteed to stop before lock release, preventing an unlocked
  Terraform from continuing to run.
- **Good:** Re-entrant guard prevents double-shutdown if multiple signals arrive.
- **Good:** Handler is scoped to the lock-holding window; non-locking commands are
  unaffected.
- **Bad:** SIGKILL and Windows `TerminateProcess` cannot be handled. Lock will be
  abandoned in these cases. Operators must use `strata deploy lock release` to recover.
- **Bad:** The 30-second subprocess grace period means the minimum time-to-clean-exit
  after SIGTERM is up to 30 seconds. This is intentional (give Terraform time to write
  its state file) but may surprise operators who expect an immediate exit.

## Related

- ADR-0020 — CLI Parameter Consistency Standard (documents signal behaviour in epilog)
- ADR-0027 — Command Timeout (calls `coordinator.shutdown("timeout")` on expiry)
- ADR-0029 — Real-Time Progress Streaming (`coordinator.shutdown()` emits `run_complete` with `exit_code: 1` to the stream as the first step, before stopping the subprocess)
- ADR-0004 — Exit Code Convention (exit 1 = system failure)
- ADR-0007 — Deployment State Locking (lock manager that coordinator releases)

### Integration touchpoints

- ADR-0018 — Deployment Audit Traceability: every clean shutdown (SIGTERM, SIGINT, timeout) MUST write an audit entry recording the trigger reason, the stage that was in progress at interrupt time, and the lock release outcome. Interrupted deployments that do not appear in the audit trail fail compliance controls.
- ADR-0022 — SIEM Integration: the audit entry written on shutdown is forwarded to SIEM sinks via the normal audit forwarding path. No SIEM-specific code in the signal handler — the handler writes the audit entry; the audit subsystem handles forwarding.
- ADR-0025 — AI Agent Integration: if an agent-initiated deployment is interrupted by SIGTERM (e.g., the container hosting the MCP server is stopped), the agent will receive no further stream events. The MCP server should detect the broken stream and report a `deployment_interrupted` tool result to the agent rather than leaving the agent in an indefinite wait.
