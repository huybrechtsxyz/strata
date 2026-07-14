# Real-Time Progress Streaming via ndjson (`--stream`)

- Status: proposed
- Date: 2026-07-11

## Context and Problem Statement

`strata build run`, `strata deploy run`, and `strata deploy destroy` invoke external
provisioners that can run for many minutes. During this time strata produces no output
until the provisioner exits. This creates three problems:

1. **CI dashboards** show a blank job for the duration. Operators cannot tell if the
   job is progressing or hung.
2. **IDE integrations** (VS Code extension, MCP server) have no way to surface live
   status without polling `strata deploy status`, which requires a separate process
   and returns point-in-time snapshots rather than a live event stream.
3. **Log aggregators** (Splunk, Datadog, CloudWatch) receive a single large batch of
   output at the end rather than a continuous, timestamped event stream. Correlation
   with external events is difficult.

`--stream` was documented in ADR-0020 and marked `[TODO: not yet implemented]`. This
ADR decides the event schema, transport, and integration points.

## Decision Drivers

- **Machine-readable** — The stream must be parseable by CI scripts, log aggregators,
  and the VS Code extension without fragile text parsing.
- **Composable** — Streaming output must not break existing `--output json` consumers.
  A CI script that pipes `strata deploy run ... --output json` to `jq` must continue
  to work whether or not `--stream` is also set.
- **No new transport** — stdout is the only transport. No sockets, no named pipes, no
  HTTP endpoints. Existing CI log capture works unchanged.
- **Backward compatible** — Without `--stream`, behaviour is identical to today.
- **Low overhead** — Each event write must not block the provisioner. Emitting an event
  must be fire-and-forget from the provisioner's perspective.
- **No new dependencies** — stdlib only for the core emitter; the event schema is plain
  JSON.

## Considered Options

### Option A: Plain text line-by-line output to stderr
- Route provisioner stdout/stderr directly to the process's stderr in real time.
- Pro: Trivial to implement; familiar to operators.
- Con: Not machine-readable.
- Con: Mixes structured strata events with raw Terraform output — parsers cannot
  distinguish them.
- Con: Does not include timestamps, stage context, or event type.
- **Rejected:** Does not meet the machine-readable or composable requirements.

### Option B: ndjson structured events to stdout
- Emit one JSON object per line to stdout when `--stream` is set.
- Each line is a self-contained event with a type, timestamp, and payload.
- Pro: Machine-readable, composable, timestamped.
- Pro: Log aggregators ingest ndjson natively (Splunk, Loki, Datadog all support it).
- Pro: VS Code extension can parse events in real time without polling.
- Con: Stdout carries both the stream events and (at the end) the normal strata result
  envelope — consumers must handle both formats on the same stream.
- Mitigation: Use a terminal `{"event": "result", ...}` event to carry the final
  strata result envelope, so the entire stream is uniform ndjson.

### Option C: Separate output channel (file descriptor 3, named pipe, etc.)
- Stream events on a non-stdout channel; keep stdout for the normal result envelope.
- Pro: Clean separation; no format mixing on stdout.
- Con: Requires the caller to open the extra channel before invoking strata (complex
  in shell scripts and CI YAML).
- Con: Named pipes are platform-specific; fd 3 is not guaranteed to be open.
- **Rejected:** Complexity burden on every caller is not justified.

### Option D: Server-sent events or WebSocket
- strata opens a local HTTP server; callers subscribe to events.
- **Rejected:** Completely out of scope for a CLI tool.

## Decision Outcome

Chosen: **Option B — ndjson structured events to stdout**.

When `--stream` is set, stdout carries a sequence of ndjson lines followed by a final
`result` event. When `--stream` is not set, stdout carries only the normal strata result
envelope (unchanged behaviour).

### Event schema

All events share a common envelope:

```text
{"event": "<type>", "ts": "<ISO8601_UTC>", ...type-specific fields...}
```

**Timestamp format:** `YYYY-MM-DDTHH:MM:SSZ` (UTC with Z suffix, second precision).
Sub-second precision is not used — provisioner operations are measured in seconds, not
milliseconds.

#### Event types

| Event type       | When emitted                                         | Key fields                                   |
| ---------------- | ---------------------------------------------------- | -------------------------------------------- |
| `run_start`      | Before any stage begins                              | `deployment`, `stages` (list of stage names) |
| `stage_start`    | When a stage begins                                  | `stage`, `provisioner`                       |
| `stage_log`      | Each log line from the provisioner subprocess        | `stage`, `level`, `msg`                      |
| `stage_complete` | When a stage finishes                                | `stage`, `exit_code`, `duration_s`           |
| `stage_skipped`  | When a stage is skipped (scope filter, dry-run)      | `stage`, `reason`                            |
| `run_complete`   | After all stages finish                              | `exit_code`, `duration_s`                    |
| `result`         | Last line; carries the normal strata result envelope | `success`, `data`, `errors`, `messages`      |

#### Example stream

```text
{"event": "run_start", "ts": "2026-07-11T14:30:00Z", "deployment": "haven-prd", "stages": ["networking", "compute", "dns"]}
{"event": "stage_start", "ts": "2026-07-11T14:30:00Z", "stage": "networking", "provisioner": "terraform"}
{"event": "stage_log", "ts": "2026-07-11T14:30:02Z", "stage": "networking", "level": "INFO", "msg": "Terraform 1.9.0"}
{"event": "stage_log", "ts": "2026-07-11T14:30:05Z", "stage": "networking", "level": "INFO", "msg": "Plan: 3 to add, 0 to change, 0 to destroy."}
{"event": "stage_complete", "ts": "2026-07-11T14:31:10Z", "stage": "networking", "exit_code": 0, "duration_s": 70}
{"event": "stage_start", "ts": "2026-07-11T14:31:10Z", "stage": "compute", "provisioner": "terraform"}
{"event": "stage_complete", "ts": "2026-07-11T14:34:45Z", "stage": "compute", "exit_code": 0, "duration_s": 215}
{"event": "stage_skipped", "ts": "2026-07-11T14:34:45Z", "stage": "dns", "reason": "--scope filter did not match"}
{"event": "run_complete", "ts": "2026-07-11T14:34:45Z", "exit_code": 0, "duration_s": 285}
{"event": "result", "ts": "2026-07-11T14:34:45Z", "success": true, "data": {...}, "errors": [], "messages": []}
```

### Implementation

An `EventEmitter` class wraps `sys.stdout` writes. It is instantiated once per
command invocation when `--stream` is set; when not set, a no-op `NullEmitter` is used
so the provisioner code does not need to check the flag.

```python
class EventEmitter:
    def emit(self, event_type: str, **kwargs) -> None:
        payload = {"event": event_type, "ts": _now_utc(), **kwargs}
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()  # ensure each event is written immediately

class NullEmitter:
    def emit(self, event_type: str, **kwargs) -> None:
        pass  # no-op when --stream is not set
```

The provisioner subprocess wrapper reads from the subprocess's stdout/stderr line by
line (non-blocking via `iter(proc.stdout.readline, b"")`) and emits `stage_log` events.
This replaces the current behaviour of buffering all output and printing it at the end.

### Composability with `--output`

When `--stream` is set, the final strata result envelope is emitted as the last ndjson
line (`{"event": "result", ...}`) rather than printed as the sole stdout content.
This means:

- A consumer that reads only the last line gets the result envelope it expects (if it
  uses a `tail -1 | jq` pattern).
- A consumer that reads all lines and filters for `"event": "result"` gets the same.
- A consumer that reads the full stream gets all progress events plus the final result.

`--stream` and `--output json` may be combined. `--stream` and `--output console` will
produce ndjson events interspersed with human-readable text — this combination is
discouraged in non-interactive contexts (document in help text).

### `stage_log` volume management

Raw Terraform output can be thousands of lines. When `--stream` is set, all lines are
emitted as `stage_log` events. This is intentional — the consumer (CI log aggregator,
VS Code extension) decides what to display. A future option `--stream-filter LEVEL`
(e.g., `--stream-filter WARNING`) could suppress below-threshold `stage_log` events;
this is out of scope for the initial implementation.

### Removal of `[TODO]` markers

When this ADR is accepted and implementation begins, remove the `[TODO: not yet
implemented]` annotations from the `--stream` entries in ADR-0020's command specs.

## Consequences

- **Good:** CI dashboards get live progress instead of a blank job.
- **Good:** VS Code extension and MCP server can surface live stage progress without
  polling.
- **Good:** Log aggregators receive a timestamped, structured event stream.
- **Good:** Backward compatible — `--stream` is opt-in; existing consumers unaffected.
- **Good:** No new dependencies.
- **Bad:** High-verbosity provisioners (Terraform with DEBUG logging) can produce very
  large event streams. Operators should use `--verbose` judiciously when also using
  `--stream`.
- **Bad:** The `stage_log` events contain raw provisioner output which may include
  sensitive values (secret names, resource IDs). Callers routing `--stream` output to
  external systems must ensure those systems have appropriate access controls.
- **Bad:** Combining `--stream` and `--output console` produces mixed output that is
  not human-friendly. This is a documentation / UX issue, not a correctness issue.

## Related

- ADR-0020 — CLI Parameter Consistency Standard (documents `--stream` flag; contains `[TODO]` markers to remove)
- ADR-0027 — Command Timeout (shutdown emits `stage_log` events before exiting)
- ADR-0028 — SIGTERM Graceful Shutdown (shutdown emits a `run_complete` event with `exit_code: 1` before releasing lock)

### Integration touchpoints

- ADR-0018 — Deployment Audit Traceability: `run_start`, `stage_complete`, and `run_complete` events map directly to audit trail entries. The audit subsystem MAY consume these events from the emitter rather than writing audit entries separately — this eliminates duplication and ensures the audit timestamp matches the stream timestamp. Whether to couple the emitter to the audit writer or keep them independent is an implementation decision deferred to the Phase 8 implementation spike.
- ADR-0022 — SIEM Integration: `stage_complete` and `run_complete` events are high-value SIEM data (stage name, exit code, duration). The SIEM forwarding path should subscribe to the emitter and forward these event types to configured sinks in real time, rather than waiting for the deploy-log batch that currently drives SIEM forwarding. This enables sub-minute SIEM visibility on long deployments.
- ADR-0025 — AI Agent Integration: the stream is the primary real-time data source for the AI agent layer. Agents should subscribe to the ndjson stream when available and use `stage_log` events to provide live narration and early anomaly detection (e.g., flag a Terraform `"Plan: N to destroy"` line in a `stage_log` event before the stage completes). When `--stream` is not set, the agent falls back to polling `strata deploy status` — the stream is an enhancement, not a requirement.
