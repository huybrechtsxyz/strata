# Command Timeout for Long-Running Operations

- Status: proposed
- Date: 2026-07-11

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
mechanism. On Unix, enhance with Option B as a belt-and-suspenders fallback: set
`signal.alarm(timeout + 30)` as a hard kill backstop in case the thread does not
exit within the timeout grace period.

### Implementation

```python
import concurrent.futures
import signal
import sys

def run_with_timeout(fn, timeout_seconds: int):
    """
    Run fn() in a thread pool. Raise TimeoutError if it does not complete
    within timeout_seconds. On Unix, also arm SIGALRM as a hard backstop.
    """
    if hasattr(signal, "SIGALRM"):
        signal.alarm(timeout_seconds + 30)  # hard backstop, Unix only

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"Command exceeded timeout of {timeout_seconds}s. "
                "Initiating graceful shutdown."
            )
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)  # disarm
```

On `TimeoutError`:
1. Log: `"Timeout after {N}s — initiating graceful shutdown"`
2. Invoke the same shutdown sequence used for SIGTERM (ADR-0028):
   - Send SIGTERM to the active provisioner subprocess
   - Wait up to 30 seconds for it to exit cleanly
   - SIGKILL if still running after grace period
   - Release deployment lock
3. Exit 1

### Click decorator

```python
@click.option(
    "--timeout",
    "timeout",
    type=int,
    default=3600,
    metavar="SECONDS",
    help="Abort if command does not complete within N seconds (default: 3600).",
)
```

Applied to: `strata build run`, `strata deploy run`, `strata deploy destroy`.

### Exit behaviour

Timeout exits with code 1 (system failure — alert, do not auto-retry). This is
intentional: a timed-out deployment is in an unknown state and requires operator
inspection before retry. See ADR-0020 exit code table.

## Consequences

- **Good:** CI pipelines always get a clean exit code within a bounded time.
- **Good:** Deployment lock is released on timeout (via ADR-0028 shutdown sequence).
- **Good:** Cross-platform — ThreadPoolExecutor works on Windows.
- **Good:** No new dependencies.
- **Bad:** Thread-based isolation means a truly stuck subprocess (e.g., waiting for
  a network socket that never returns) will keep the thread alive until the OS SIGKILL.
  The Unix SIGALRM backstop mitigates this on Linux/macOS.
- **Bad:** The 30-second grace period means the actual wall-clock time before exit can
  be `timeout + 30`. Operators should account for this in CI job time limits.

## Related

- ADR-0020 — CLI Parameter Consistency Standard (documents `--timeout SECONDS` flag)
- ADR-0028 — SIGTERM Graceful Shutdown (defines the shutdown sequence invoked on timeout)
- ADR-0029 — Real-Time Progress Streaming (on timeout, shutdown emits `run_complete` with `exit_code: 1` to the stream before releasing lock)
- ADR-0004 — Exit Code Convention (exit 1 = system failure)

### Integration touchpoints

- ADR-0018 — Deployment Audit Traceability: a timeout termination is a deployment lifecycle event and MUST be recorded in the audit trail with the elapsed duration and the `exit_code: 1` outcome, identical to a provisioner failure entry.
- ADR-0022 — SIEM Integration: the audit entry written on timeout will be forwarded to configured SIEM sinks (Splunk HEC, syslog/CEF) via the existing audit forwarding path — no additional SIEM-specific code needed in the timeout handler.
- ADR-0025 — AI Agent Integration: AI agents that invoke `build run` or `deploy run` via the MCP server must treat exit 1 from timeout as a non-retryable system failure (not a validation error). The agent integration layer should surface the elapsed duration and the timeout threshold to the LLM so it can advise the operator whether the timeout is misconfigured or the deployment is genuinely slow.
