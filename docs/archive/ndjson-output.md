# `--follow ndjson` — NDJSON Streaming

- Status: **Archived — shipped** (Phase 2, `deploy run` only). Current
  state lives in the living design doc,
  [deploy-plan-preview.md](../design/deploy-plan-preview.md)'s own
  "Streaming" section; the decision itself is
  [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md). Kept
  here as the full investigation/evidence record (v1 source evidence,
  every option considered) rather than deleted — not duplicated into the
  design doc or the ADR, which each keep only what they need.
- Last updated: 2026-10-08
- Related: [deploy-plan-preview.md](../design/deploy-plan-preview.md)
  (living doc, current-state reference), [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md)
  (the trigger-mechanism decision this doc's evidence grounds),
  [cli-output-mode-interactions.md](cli-output-mode-interactions.md)
  (sibling archived doc this was split out of — the concrete `--follow`/
  `STRATA_OUTPUT` interaction bug), [diagnostic-guidance.md](../work/diagnostic-guidance.md)
  (an unrelated gap surfaced while reviewing this doc, still open — see
  `docs/work/`)

## Overview

Split out from [cli-output-mode-interactions.md](cli-output-mode-interactions.md)'s
Option 5. v1 had `--output ndjson` for streaming deploy progress; v2
never ported it. The real motivating case: a CI pipeline wants
machine-parseable output (`--output json`) **and** live progress during a
long `deploy run` apply at the same time — something `--output json`/
`--follow` alone cannot give, since `--follow` goes silent under `json`.

**v1 never solved this by making `--follow` cooperate with `json`
either** — it used a genuinely separate format (`ndjson`). That's the
shape v2 ported.

## Shipped design (quick reference)

- **Invoke**: `strata deploy run DEPLOYMENT --output json --follow
  ndjson`. Requires `--output json` explicitly (`UsageError` otherwise)
  — `--follow ndjson` does **not** imply `--output json` on its own.
- **Per-line event** (one per subprocess output line):
  `{"event": "line", "tool": "terraform", "stream": "stdout"|"stderr",
  "text": "...", "ts": "2026-10-07T12:00:00+00:00"}`. `stream` is
  metadata about which stream the *subprocess* line came from — every
  event goes to v2's own stdout regardless (stdout-only, matching v1).
- **Progress event** (one per step-boundary message):
  `{"event": "progress", "message": "...", "ts": "..."}`. A deliberate
  first-cut scope-down from v1's richer `step_start`/`step_end` (with
  `step`/`stage` fields) — v2's `on_step` callback only ever carries one
  flat message string.
- **Final line**: the exact same `build_envelope()` result plain
  `--output json` already produces — no `event` key, no transcript of
  the streamed lines (confirmed directly against v1's own source: its
  `complete` event's `data` is accumulated structured facts, never a
  replay of already-streamed `line` events). Forced single-line
  (`indent=None`) via a new `compact` parameter on `make_reporter()`/
  `command_run()`.
- **Scope**: `deploy run` only. No new `OUTPUT_FORMATS` member, no new
  `Reporter` class, no `deploy_controller.py` change — the whole feature
  lives in `deploy_command.py` plus the small additive `compact` param.
- **Not built**: a `data`-type event for commands with no subprocess at
  all (`repo sync`-style, per v1's own real usage — see Finding 4 below)
  — no target command exists in v2 yet with that shape.

## Key findings from v1 (grounded directly in v1's real source, not memory)

v1's real implementation (`src/strata/commands/base_command.py`, `.../
deploy/run_deploy_command.py`) settles several things no amount of
reasoning from this repo's own docstrings alone would have found:

1. **v1 has no `--follow`-equivalent flag at all.** Console-mode live
   streaming is driven directly by `--verbose` — there was never a
   separate boolean for "stream live." v2 deliberately diverged by
   inventing a dedicated `--follow`/`-f` flag instead
   (`cli-output-mode-interactions.md`'s own finding: a real improvement
   over v1's dual-purpose `--verbose`, not an oversight).
2. **v1 never combines live streaming with its own single-envelope
   `json` format.** `ndjson` is a genuinely separate format in v1, not a
   `json`-compatibility feature — confirming the motivating ask
   (parseable *and* live, at once) was never answerable through
   `json`+`--follow` even in v1.
3. **Every NDJSON event — including ones tagged `"stream": "stderr"` —
   is written to v1's own real stdout.** `stream` describes the
   *subprocess* line's origin, never an instruction for which of v1's
   own OS streams to use. Only console-mode `--verbose` actually splits
   across real OS streams — the exact logic v2's `--follow` already
   ported faithfully.
4. **NDJSON is wired into eight v1 command files, not one** — `build
   run`/`plan`/`sbom`, `deploy run`/`destroy`/`health`/`output`, and
   `repo sync`. Two genuinely different usage patterns: (a) raw
   subprocess-line streaming (the pattern this doc built against), and
   (b) **structured per-item progress events with no subprocess at all**
   — `repo sync`'s own `{"event": "data", "type": "repo_sync_result",
   ...}` per repo synced. NDJSON is a general-purpose "stream of
   independent result events for any command doing multiple discrete
   units of work" in v1, not a `deploy run`-specific mechanism — the
   first v2 cut intentionally undersells this (see "Deliberately Out of
   Scope").
5. Every console-only `click.echo` in v1's `run_deploy_command.py` is
   gated behind a console-mode check with **zero warning** when another
   mode is active and that line is silently skipped — the same
   long-standing v1 convention `cli-output-mode-interactions.md` found
   for `--follow` itself. v2 isn't introducing new silence here, just
   continuing v1's own.

## Settled design decisions

- **Trigger mechanism**: not a new `--output` value. `--follow` itself
  became an optional-value flag (`--follow` bare → `"console"`,
  unchanged; `--follow ndjson` → NDJSON streaming), requiring `--output
  json`. Full rationale and rejected alternatives recorded in
  [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md).
- **`--follow` stays its own flag**, not folded into `--verbose` (v1's
  real shape) — `--verbose` stays logging-only, matching
  `cli-output-mode-interactions.md`'s own "deliberate improvement, not
  worth undoing" finding.
- **Final event shape**: the plain `build_envelope()` envelope, nothing
  bespoke — one single, already-documented shape to parse regardless of
  format, and streamed lines are never replayed into it (Finding above).
- **Per-line/step event shape**: v1's own proven fields
  (`event`/`stream`/`text`/`ts`), plus `tool` (v2's `on_line` already
  carries it) and minus `stage` — v2 has no stage/step distinction
  (`workspace.spec.execution` is a flat list), so there's no grouping to
  reconstruct. `on_step` already fires at exactly step-start/step-end
  for every step uniformly (Terraform and Helm/Compose alike) — zero new
  instrumentation needed in `deploy_controller.py`.
- **stdout-only**, matching v1 exactly (Finding 3) — `stream` stays
  metadata, nothing is ever written to real stderr.

## Deliberately Out of Scope

- The `data`-type event (matching v1's `repo_sync_result` shape, for
  commands with no subprocess at all) — no v2 command with that shape
  exists yet to need it. Revisit only if a second real need shows up.
- A trigger mechanism for a future non-`deploy run` command: `--follow
  ndjson` only makes sense for a command that already has a `--follow`
  flag. A `repo sync`-style command would need its own trigger
  (plausibly a real, standalone `--output ndjson`, specifically for that
  class of command) — the two real v1 usage patterns found here may end
  up needing two *different* v2 trigger mechanisms, not one unified one.
  Flagged, not designed.
- Extending `--follow`/NDJSON to `build run` — confirmed `build run` has
  no subprocess/line-callback plumbing at all today, so there is nothing
  to stream.

## Implementation (shipped)

- `deploy_command.py`'s `--follow` upgraded from a plain boolean to an
  optional-value flag (`is_flag=False, flag_value="console",
  type=click.Choice(["console", "ndjson"])`).
- Guard: `if follow == "ndjson" and output != "json": raise
  UsageError("--follow ndjson requires --output json.")`.
- `on_line` gains an ndjson branch emitting `{"event": "line", "tool",
  "stream", "text", "ts"}`; `on_step` gets a matching override emitting
  `{"event": "progress", "message", "ts"}` — both via
  `click.echo(json.dumps(..., ensure_ascii=False))` directly to stdout.
- New `compact: bool` parameter on `make_reporter()`/`command_run()`,
  forcing `indent=None` — found necessary during implementation:
  `JsonReporter`'s default `indent=2` pretty-prints the envelope across
  multiple lines, silently breaking NDJSON's one-object-per-line
  contract. `deploy run` passes `compact=(follow == "ndjson")`.
- No `OUTPUT_FORMATS` change, no new `Reporter` class, no
  `deploy_controller.py` change — contained entirely to
  `deploy_command.py` plus the small additive `compact` parameter.
- **Tests** (`tests/strata/commands/test_commands_deploy.py`):
  `test_follow_ndjson_requires_output_json`,
  `test_follow_ndjson_streams_line_and_progress_events_to_stdout`,
  `test_follow_ndjson_does_not_escape_non_ascii_text` (added in code
  review — see below).
- **Code review found and fixed one real bug**: the new events'
  `json.dumps(...)` calls initially omitted `ensure_ascii=False`, unlike
  `format_json()`'s own call for the final envelope — non-ASCII text
  would have rendered inconsistently (escaped in live events, literal in
  the final line) within one stream. Fixed to match `format_json()`'s
  exact policy.
- Full check suite clean throughout: mypy, ruff, import-linter, pytest
  **2180 passed**.

## Related Decisions

- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — the
  real-CI-usage finding behind `STRATA_OUTPUT` existing at all.
- [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md)
  — built `--follow`'s `on_line` plumbing with this exact reuse in mind.
- [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md) — the
  trigger-mechanism decision this doc's evidence directly grounds.
- [cli-output-mode-interactions.md](cli-output-mode-interactions.md) —
  the sibling doc this was split out of; its own Option 5 points here.

## History

- v1's real NDJSON implementation settled every open design question
  here with evidence instead of guesswork: no `--follow`-equivalent flag
  (streaming was implied by `--verbose`); `ndjson` always a genuinely
  separate format, never combined with single-envelope `json`; every
  event (even `stderr`-tagged ones) written to v1's own real stdout; and
  NDJSON used across eight v1 command files in two distinct shapes (raw
  subprocess-line streaming vs. structured per-item `data` events with
  no subprocess at all) — the first v2 cut deliberately covers only the
  former.
- Considered decoupling `--follow` (timing) from `--output` (format)
  completely, so `--output json --follow` alone would emit per-line JSON
  events. Rejected: it would make `--output json`'s own documented
  single-envelope contract depend conditionally on whether `--follow`
  also happened to be set — the same class of surprise this whole
  investigation exists to eliminate, just relocated. Full reasoning and
  the rejected alternatives are in ADR-0033.
- Checked whether the final JSON envelope should replay every streamed
  line — v1's own `_output_data` (its real final-event payload) is
  commented plainly as "Structured result data," never a transcript of
  already-emitted `line` events. v2's own `deploy run` already leaves
  `JsonReporter.data` empty today, confirming there was nothing pulling
  per-line text toward the final document to begin with — kept that
  split: stream lines once, live; leave the final envelope exactly as
  lean as it already was.
