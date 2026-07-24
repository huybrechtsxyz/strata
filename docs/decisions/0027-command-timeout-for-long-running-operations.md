# Command Timeout for Long-Running Operations

- Status: implemented
- Date: 2026-07-11
- Implemented: 2026-07-24

## Context and Problem Statement

`strata build run`, `strata deploy run`, and `strata deploy destroy` invoke external
provisioners (Terraform, Ansible) that can run for minutes or hours. CI/CD pipelines
have hard job time limits; if strata never exits on its own, the pipeline runner kills
the process with `SIGKILL` — which skips cleanup, leaves the deployment lock held, and
produces no useful exit code for the pipeline to act on.

Operators and CI systems need a way to bound execution time so that:

1. The pipeline gets a clean exit code (exit 1) rather than a SIGKILL.
2. The deployment lock is released before the process exits (see ADR-0028 for the
   shutdown sequence that achieves this).
3. The CI system can distinguish a timeout from a provisioner failure and alert
   accordingly.

`--timeout SECONDS` was documented in ADR-0020 (CLI Parameter Consistency Standard) and
added to the command specs. This ADR decides how to implement it.

## Decision Drivers

- **Cross-platform** — strata runs on Linux, macOS, and Windows. The mechanism must
  work on all three without conditional code paths in every command.
- **Interruptible** — The timeout must interrupt a running subprocess, not just abandon
  it. A Terraform process that continues running after strata exits will corrupt state.
- **Clean exit** — On expiry: release the deployment lock, emit a clear log message,
  exit 1. The same shutdown sequence used for SIGTERM (ADR-0028).
- **No new dependencies** — Must work with the Python standard library only.
- **Default safe** — The default (3600 seconds) must be high enough that legitimate
  long deployments never time out unexpectedly.

## Considered Options

### Option A: No timeout (current state)
- strata runs until the provisioner exits or the process is killed externally.
- **Rejected:** CI pipelines get SIGKILL, locks are abandoned, exit codes are useless.

### Option B: `signal.alarm()` (Unix SIGALRM)
- Set `signal.alarm(timeout)` before the provisioner call; catch `SIGALRM` to abort.
- Pro: Zero overhead, standard Python.
- Con: **Windows does not support `signal.SIGALRM`**. Cannot be the primary mechanism.
- Con: `signal.alarm()` only works on the main thread.

### Option C: `concurrent.futures.ThreadPoolExecutor` with `result(timeout=)`
- Run the provisioner call in a `ThreadPoolExecutor` worker thread.
- Main thread calls `future.result(timeout=N)` which raises `concurrent.futures.TimeoutError`
  after N seconds.
- On timeout: trigger shutdown sequence (send SIGTERM to subprocess, wait, SIGKILL, release lock, exit 1).
- Pro: Works on all platforms; no conditional code.
- Con: Thread-based — if the worker thread holds a non-interruptible lock, it may not
  exit cleanly. Mitigated by ensuring subprocess calls are the only blocking operation.

### Option D: Subprocess-level `timeout=` parameter
- Pass `timeout=N` directly to `subprocess.run()` / `subprocess.Popen().wait()`.
- Pro: Simplest — one line change per subprocess call.
- Con: Each provisioner may invoke multiple subprocess calls; the timeout would apply
  per-call, not to the total pipeline. A deploy with 10 stages could run for
  `10 × timeout` seconds.
- Con: Does not apply to non-subprocess work (model loading, artifact writing).
- **Rejected:** Per-call timeout does not bound total pipeline duration.

## Decision Outcome

Chosen: **Option C — `ThreadPoolExecutor` with `result(timeout=)`** for the primary
mechanism. The SIGALRM backstop from Option B is **dropped** (see revised design below).

### Revised design (post ADR-0028)

ADR-0028 introduced `ShutdownCoordinator` with signal handlers registered on the main
thread. The `ThreadPoolExecutor` approach must keep that invariant: coordinator activation
stays on the main thread; only the stage execution body moves to the worker thread.

**Thread split:**

```
Main thread                                    Worker thread
─────────────────────────────────────────      ──────────────────────────────────────
coordinator = ShutdownCoordinator.activate()   _execute_provisioning_body()
                                                 ├─ acquire lock
future = executor.submit(body)                   ├─ stage 1: terraform apply
                                                 ├─ stage 2: ansible-playbook
future.result(timeout=N)  ←── blocks ────────    └─ release lock
    │
    ├─ completes normally → coordinator.deactivate()
    └─ TimeoutError       → coordinator.shutdown("timeout")
                              ├─ terminates all active subprocesses
                              ├─ releases deployment lock
                              └─ sys.exit(1)
```

**Why no SIGALRM backstop:**

The original ADR proposed `signal.alarm(timeout + 30)` as a hard-kill backstop on Unix.
This is dropped because:

1. `SIGALRM` would kill the process before `coordinator.shutdown()` can release the lock —
   defeating the entire purpose of ADR-0028.
2. If the worker thread genuinely won't die after 30 seconds of subprocess termination
   (e.g., a hung network socket), the CI runner's external job timeout / SIGKILL is the
   correct last resort — strata cannot reliably recover from this state anyway.
3. The main thread waits for `future.result()` with the timeout, then calls
   `coordinator.shutdown()` which terminates subprocesses. The worker thread will exit
   once its subprocess is dead. No additional backstop is needed for the common case.

**Implementation:**

```python
# In RunDeployCommand / DestroyDeployCommand / BuildRunCommand
import concurrent.futures

def _execute_with_timeout(self, timeout_seconds: int) -> bool:
    """Wrap _execute_provisioning_body() with an optional wall-clock timeout."""
    coordinator = ShutdownCoordinator.activate(
        lock_backend=None,     # set inside body after lock acquire
        lock_handle=None,
        deployment_name=self._deployment_name(),
    )
    try:
        if timeout_seconds <= 0:
            # No timeout — run directly on the main thread
            return self._execute_provisioning_body(coordinator)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._execute_provisioning_body, coordinator)
            try:
                return future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                elapsed = timeout_seconds  # lower bound
                self.logger.error(
                    "deploy_timeout",
                    timeout_seconds=timeout_seconds,
                    deployment=self._deployment_name(),
                )
                coordinator.shutdown(f"timeout after {elapsed}s")
                return False  # unreachable — shutdown() calls sys.exit(1)
    finally:
        coordinator.deactivate()
```

**Lock/coordinator handshake in the worker thread:**

The worker thread acquires the lock and registers it with the coordinator so signal
handlers on the main thread can release it:

```python
def _execute_provisioning_body(self, coordinator: ShutdownCoordinator) -> bool:
    lock_backend = self._resolve_lock_backend(stages)
    lock_handle = self._acquire_lock(lock_backend)
    if lock_handle is None:
        return False

    # Update coordinator so it can release this lock on shutdown
    coordinator.update_lock(lock_backend, lock_handle)

    try:
        for stage in stages:
            ...
        return True
    finally:
        coordinator.clear_lock()
        self._release_lock(lock_backend, lock_handle)
```

`ShutdownCoordinator` needs two new methods: `update_lock(backend, handle)` and
`clear_lock()` — both thread-safe via the existing mutex.

### Click decorator

```python
@click.option(
    "--timeout",
    "timeout",
    type=int,
    default=0,
    metavar="SECONDS",
    help=(
        "Abort if command does not complete within N seconds. "
        "0 = no timeout (default). Recommended CI value: 3600."
    ),
)
```

Default changed from `3600` to `0` (no timeout) — safer default since operators
who have not configured a timeout should not be surprised by unexpected terminations.
CI pipelines that need a bound should set `--timeout 3600` explicitly.

Applied to: `strata build run`, `strata deploy run`, `strata deploy destroy`.

### Exit behaviour

Timeout exits with code 1 (system failure — alert, do not auto-retry). Identical to
the exit code produced by SIGTERM — both represent an interrupted deployment in an
unknown state requiring operator inspection before retry.

## Consequences

- **Good:** CI pipelines always get a clean exit code within a bounded time.
- **Good:** Deployment lock is released on timeout (via ADR-0028 coordinator).
- **Good:** Cross-platform — ThreadPoolExecutor works on Windows; no SIGALRM needed.
- **Good:** No new dependencies.
- **Good:** No timeout by default — operators opt in, removing surprise terminations.
- **Bad:** Thread-based isolation means a truly stuck subprocess (hung network socket)
  keeps the worker thread alive after coordinator.shutdown(). The CI runner's external
  job timeout is the final backstop — strata cannot recover from this case.
- **Bad:** The lock/coordinator handshake (update_lock / clear_lock) adds a small amount
  of complexity to the coordinator API introduced in ADR-0028.
- **Neutral:** `timeout=0` default requires CI teams to explicitly set a value — this
  is intentional (opt-in rather than surprise opt-out).

## Related

- ADR-0020 — CLI Parameter Consistency Standard (documents `--timeout SECONDS` flag)
- ADR-0028 — SIGTERM Graceful Shutdown (defines the shutdown sequence invoked on timeout)
- ADR-0029 — Real-Time Progress Streaming (on timeout, shutdown emits `run_complete` with `exit_code: 1` to the stream before releasing lock)
- ADR-0004 — Exit Code Convention (exit 1 = system failure)

### Integration touchpoints

- ADR-0018 — Deployment Audit Traceability: a timeout termination is a deployment lifecycle event and MUST be recorded in the audit trail with the elapsed duration and the `exit_code: 1` outcome, identical to a provisioner failure entry.
- ADR-0022 — SIEM Integration: the audit entry written on timeout will be forwarded to configured SIEM sinks (Splunk HEC, syslog/CEF) via the existing audit forwarding path — no additional SIEM-specific code needed in the timeout handler.
- ADR-0025 — AI Agent Integration: AI agents that invoke `build run` or `deploy run` via the MCP server must treat exit 1 from timeout as a non-retryable system failure (not a validation error). The agent integration layer should surface the elapsed duration and the timeout threshold to the LLM so it can advise the operator whether the timeout is misconfigured or the deployment is genuinely slow.
