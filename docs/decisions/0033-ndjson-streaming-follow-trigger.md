# NDJSON Streaming Triggered by `--follow`'s Value, Not a New Flag

- Status: accepted
- Date: 2026-10-07
- Related: [ADR-0030](0030-deploy-run-plan-preview-and-streaming.md) (added
  `--follow` itself, console-only, with `on_line` plumbing deliberately
  shaped for later NDJSON reuse — this decision is that reuse),
  [ADR-0020](0020-v1-consumer-feature-priority.md) (the real-CI-usage
  finding behind `STRATA_OUTPUT` existing at all — the motivating case
  here is that same env var combined with a need for live progress),
  [docs/design/deploy-plan-preview.md](../design/deploy-plan-preview.md)
  (current-state reference), [docs/archive/ndjson-output.md](../archive/ndjson-output.md)
  (full evidence record — v1's real implementation, every option
  considered — this ADR records only the decision itself)

## Context and Problem Statement

A real CI pipeline wants machine-parseable output (`--output json`,
consumed by `jq`) **and** live progress during a long `deploy run` apply at
the same time. `--output json`/`--follow` alone cannot give this:
`--follow` streams lines live but only under console output (ADR-0030);
`JsonReporter` is architecturally built around producing exactly one
document, printed once, at the end — real pipelines depend on this
("capture stdout, branch on the exit code, pipe the same captured text to
`jq`"). v1 had solved the equivalent problem with a genuinely separate
`--output ndjson` format; v2 had not ported it.

A trigger mechanism was needed: something has to tell `deploy run` "emit
one JSON event per subprocess line and per progress message, instead of
buffering to one final document."

## Considered Options

- **A new `--output ndjson` value**, alongside `console`/`json`. Rejected
  as the sole/primary mechanism — `--follow` already exists as the
  "stream subprocess output live" flag; adding a second, independent
  trigger for the same underlying behavior (streaming) would give two
  ways to ask for overlapping things, with no clear rule for which wins
  if both were somehow given different intents.
- **Decouple `--follow` (timing) from `--output` (format) completely**:
  `--follow` becomes a pure boolean, independent of `--output`'s value;
  under `--output json` specifically, each line becomes its own per-line
  JSON event instead of being silently dropped or rerouted. Rejected —
  this makes `--output json`'s own documented single-envelope contract
  depend conditionally on a *different* flag (`--follow`) being set: a
  user who already trusts and pipes `--output json` to `jq` would
  silently get a different (multi-line) stdout shape the moment they
  also added `--follow`, with no warning — the exact class of surprise
  this whole investigation exists to eliminate, just relocated onto
  `--output json` instead of `--follow`. It also does not avoid the
  scope of building a real per-line JSON writer; it only avoids naming
  the multi-line contract, which is strictly riskier for no real saving.
- **A second, stream-specific option** (e.g. `--stream-format console|
  ndjson`, not reusing `--format` — already two unrelated meanings
  elsewhere in this CLI, on `values get`/`values generate`). Considered
  as the cleanest separation of concerns (format vs. timing stay two
  independent axes), enforced with a plain `UsageError` guard (this
  codebase's own established idiom for flag-combination rules — no
  declarative "option X requires option Y" exists in Click). Rejected in
  favor of the next option once verified feasible, to avoid introducing
  a second flag name for what `--follow` can carry itself.
- **`--follow` becomes an optional-value flag** (`is_flag=False,
  flag_value="console"`): bare `--follow` → `"console"` (today's
  behavior, unchanged); `--follow ndjson` → NDJSON streaming. Verified
  directly against the installed Click 8.5.0, not assumed from docs.
  Chosen — one flag name, not two; `--follow`'s own meaning ("stream
  subprocess output live, in this shape") already naturally extends to
  cover the shape question too, and the ordering footgun this introduces
  (Click's optional-value parsing greedily consumes the next token,
  so `--follow` before a positional argument can misparse) is a real but
  minor tradeoff given this CLI's invocations already put the
  `DEPLOYMENT` argument first.
- **Fold `--follow` into `--verbose`/`ndjson`, matching v1's own
  precedent** (v1 never had a `--follow`-equivalent at all — console
  streaming was simply implied by `--verbose`). Rejected — v2's
  `--verbose` is already single-purpose (logging only, confirmed via
  `verbose_option()`'s own docstring and its one real consumer,
  `configure_logging()`); v1's `--verbose` was dual-purpose (log level
  **and** streaming trigger). v2's split into two single-purpose flags
  is a deliberate improvement already made, not an accidental divergence
  worth reverting.

## Decision Outcome

Chosen: **`--follow` becomes an optional-value flag** —
`type=click.Choice(["console", "ndjson"])`, `flag_value="console"` for a
bare `--follow`, explicit `--follow ndjson` for the new mode. `--follow
ndjson` requires `--output json` (`UsageError` otherwise, this codebase's
established inline-guard idiom) — it does not imply `--output json` on
its own, keeping exactly one way to opt into JSON-flavored output.

Per the shipped design (full detail in
[docs/archive/ndjson-output.md](../archive/ndjson-output.md)):

- One JSON event per subprocess line (`{"event": "line", "tool",
  "stream", "text", "ts"}`) and per step-boundary message (`{"event":
  "progress", "message", "ts"}`), both written to stdout — `stream` is
  metadata about the *subprocess* line's origin, matching v1's real
  behavior; strata's own stdout is never split across real OS streams
  under this mode (only console-mode `--follow` does that, per ADR-0030).
- The run ends with the same `build_envelope()` result plain `--output
  json` already produces, forced to a single line (`indent=None`, a new
  `compact` parameter on `make_reporter()`) so it stays consistent with
  the newline-delimited events ahead of it — never a replay of the
  already-streamed lines (confirmed against v1's own real `_output_data`:
  a separate, structured accumulator, not a transcript).
- Scoped to `deploy run` only — no new `OUTPUT_FORMATS` member, no new
  `Reporter` class, no `deploy_controller.py` change.

## Consequences

- Good: gives the originally-reported need (live progress **and**
  machine-parseable output, simultaneously) a real, structurally-correct
  answer, not a narrower patch to `--follow`'s console-only behavior.
- Good: exactly one trigger mechanism (`--follow`'s value), not two
  independent flags that could disagree.
- Good: `--output json`'s own single-envelope contract is never
  conditionally altered by another flag — `ndjson` is its own explicitly
  named, opted-into shape instead.
- Neutral: `--follow ndjson` only makes sense for a command that already
  has a `--follow` flag — today, only `deploy run`. A future command
  needing the same structured-event shape with no subprocess at all
  (matching v1's real `repo sync`-style `data` events) would need its own
  trigger, since it would have no `--follow` to extend — left for that
  command to decide when it exists, not designed speculatively now.
- Bad: the Click optional-value ordering footgun (`--follow` before a
  positional argument can misparse) is a real, user-visible edge case a
  separate second flag would not have had.
