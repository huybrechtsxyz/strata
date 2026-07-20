# Real-Time Progress Streaming via ndjson (`--output ndjson`)

- Status: completed
- Date: 2026-07-11
- Implemented: 2026-07-15

## What Still Needs To Be Done

- [x] ~~Extend live NDJSON stage/log streaming to `strata deploy destroy` and `strata build run`~~ — **done**: `destroy_deploy_command._execute_stage_destroy()` now emits `stage_start` → `step_start` → `line` (via `make_ndjson_line_callback`) → `step_end` → `stage_end`, matching the `run_deploy_command` pattern exactly. `run_build_command._execute()` wraps each of the 7 build phases (`platform_build`, `terraform_build`, `ansible_build`, `compose_build`, `helm_build`, `sync_build`, `sbom_build`) with `stage_start`/`stage_end` events — builders have no `line_callback` mechanism so per-line streaming is not applicable at the build layer.
- [x] ~~Align documented event names with actual emitted schema~~ — **resolved**: implementation uses `line` (not `log`) and `stage_end` (not `stage_complete`). References to old names updated below.
- [x] ~~Decide timestamp contract (`Z` vs `+00:00`)~~ — **resolved by ADR-0045 (2026-07-20)**: `datetime.now(timezone.utc).isoformat()` emitting `+00:00` is correct. `Z` suffix references in this ADR are superseded.

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

**Implementation note:** Streaming is implemented as `--output ndjson` (not a separate
`--stream` flag). `OUTPUT_FORMATS = ["console", "text", "json", "ndjson"]` — ndjson is
a first-class output format alongside json and console, not an opt-in overlay.

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

Chosen: **Option B — ndjson structured events to stdout**, implemented as
`--output ndjson`.

When `--output ndjson` is set, stdout carries a sequence of ndjson lines. Each line is
flushed immediately as it is produced — no buffering until completion. When `--output
ndjson` is not set, stdout carries only the normal strata result envelope (unchanged
behaviour).

### Event schema

All events share a common envelope:

```text
{"event": "<type>", "ts": "<ISO8601_UTC>", ...type-specific fields...}
```

**Timestamp format:** `YYYY-MM-DDTHH:MM:SSZ` (UTC with Z suffix, second precision).
Sub-second precision is not used — provisioner operations are measured in seconds, not
milliseconds.

#### Event types implemented

| Event type       | When emitted                                  | Key fields                                              |
| ---------------- | --------------------------------------------- | ------------------------------------------------------- |
| `stage_start`    | When a stage begins                           | `stage`, `ts`                                           |
| `log`            | Each log line from the provisioner subprocess | `stage`, `step`, `stream` (stdout/stderr), `text`       |
| `stage_complete` | When a stage finishes                         | `stage`, `success`, `ts`                                |
| `complete`       | Last event; carries the final result envelope | `success`, `data`, `errors`, `messages`, `execution_id` |

**Note:** The ADR originally specified `run_start`, `stage_log`, `run_complete`, and
`result` as event names. The implementation uses `stage_start`, `log`, `stage_complete`,
and `complete`. The schema is otherwise equivalent.

#### Example stream

```text
{"event": "stage_start", "stage": "provision", "ts": "2026-07-15T10:00:00Z"}
{"event": "log", "stage": "provision", "step": "apply", "stream": "stdout", "text": "azurerm_resource_group.main: Creating..."}
{"event": "log", "stage": "provision", "step": "apply", "stream": "stdout", "text": "azurerm_resource_group.main: Creation complete after 2s"}
{"event": "stage_complete", "stage": "provision", "success": true, "ts": "2026-07-15T10:02:14Z"}
{"event": "complete", "success": true, "command": "deploy.run", "execution_id": "abc123", "data": {...}, "errors": [], "messages": []}
```

### Implementation

**Implemented.** The core emitter lives in `BaseCommand`:

- `_is_ndjson_output()` — returns `True` when `--output ndjson` is active
- `emit_ndjson(event: dict)` — serialises to JSON, writes to stdout, flushes immediately
- `make_ndjson_line_callback(step, stage)` — returns a callback for subprocess line output

The subprocess wrapper in `run_deploy_command.py` uses the line callback to emit one
`log` event per line as the provisioner produces output (non-blocking drain via threads).
The `_finalize()` method in `BaseCommand` emits the terminal `complete` event.

**Remaining work:**
- Rich console progress UI (spinners, stage progress bars) for `--output console`
- VS Code extension live status panel consuming the ndjson stream
- Remove `[TODO: not yet implemented]` from ADR-0020 `--stream` entries

### Composability with `--output`

`--output ndjson` is a first-class output format. It replaces `--output json` for
consumers that want live streaming. A consumer that previously used `--output json`
and read the final envelope can switch to `--output ndjson` and read the terminal
`complete` event instead — the payload is identical.

### `log` event volume management

Raw Terraform output can be thousands of lines. When `--output ndjson` is set, all
lines are emitted as `log` events. This is intentional — the consumer (CI log
aggregator, VS Code extension) decides what to display. A future option
`--stream-filter LEVEL` could suppress below-threshold events; deferred.

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
