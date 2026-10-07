# `--output ndjson` — Work

- Status: draft
- Last updated: 2026-10-07

## Overview

Split out from [cli-output-mode-interactions.md](cli-output-mode-interactions.md)'s
Option 5, per that doc's own recommendation not to fold a new output
format's full design into the `--follow`/`STRATA_OUTPUT` bug doc. v1 had
`--output ndjson` for streaming deploy progress; v2 never ported it
(`OUTPUT_FORMATS = ("console", "json")` only). This doc designs what
porting it would actually look like, grounded directly in v1's real
source (`git show main:...`, read directly rather than assumed from
memory) rather than guessing at the shape.

The real motivating case is still the cfgint report that started this:
their CI pipeline wants machine-parseable output (`STRATA_OUTPUT=json`)
**and** live progress during a long `deploy run` apply, and today's
`--output json`/`--follow` combination cannot give them both at once
(`--follow` goes silent under `json`). The finding below that actually
settles this cleanly: **v1 never tried to give both at once through the
same format either** — it solved this with a genuinely separate format
(`ndjson`), not by making `--follow` cooperate with `json`. That is likely
the right shape for v2 too, not a `--follow`/`json` compatibility fix.

**Trigger mechanism: decided (Phase 2, 2026-10-07).** Not a new
`--output ndjson` value — `--follow` itself became an optional-value flag
(`--follow` bare → `"console"`, today's unchanged behaviour; `--follow
ndjson` → NDJSON streaming), scoped to `deploy run` only, requiring
`--output json`. See Implementation Plan below for what shipped.

## Real evidence motivating this

v1's real implementation, read directly from `main` (`src/strata/commands/
base_command.py`, `src/strata/commands/deploy/run_deploy_command.py`),
not reconstructed from this repo's own docstrings about it:

```python
# cli_common.py
OUTPUT_FORMATS = ["console", "text", "json", "ndjson"]
```

```python
# base_command.py
def _is_structured_output(self) -> bool:
    """Return True when --output <format> is active: emit machine-readable
    data (json, text, etc.)."""
    return bool(self._output_format) and self._output_format not in ("console", "ndjson")

def _is_ndjson_output(self) -> bool:
    """Return True when --output ndjson is active: emit events as
    newline-delimited JSON."""
    return self._output_format == "ndjson"

def _is_console_output(self) -> bool:
    """Return True for default human-readable console output (no --quiet,
    no explicit --output format)."""
    return not self._output_quiet and (not bool(self._output_format) or self._output_format == "console")

def emit_ndjson(self, event: Dict[str, Any]) -> None:
    """Emit one NDJSON event line immediately to stdout.

    Each call writes exactly one "\\n"-terminated JSON object.
    """
    click.echo(json.dumps(event, default=str), nl=True)

def make_ndjson_line_callback(self, step: str, stage: Optional[str] = None) -> "Callable[[str, str], None]":
    """Return a line_callback that emits NDJSON line events for every
    subprocess output line."""
    def _cb(stream: str, text: str) -> None:
        event = {"event": "line", "step": step, "stream": stream, "text": text, "ts": ...}
        if stage is not None:
            event["stage"] = stage
        self.emit_ndjson(event)
    return _cb
```

`run_deploy_command.py`'s actual per-step wiring — the two live-streaming
tiers are **mutually exclusive, selected by a plain `if`/`elif`, with no
separate flag involved at all**:

```python
line_cb: Optional[Callable[[str, str], None]] = None
if self._is_ndjson_output():
    # Tier 2: stream each subprocess output line as an NDJSON event.
    self.emit_ndjson({"event": "step_start", "step": step_name, "stage": stage.name, "ts": ...})
    line_cb = self.make_ndjson_line_callback(step=step_name, stage=stage.name)
elif self._is_verbose():
    # Tier 1: print subprocess lines live to the console as they arrive.
    def _make_verbose_cb(tool: str) -> Callable[[str, str], None]:
        def _cb(stream: str, text: str) -> None:
            if stream == "stderr":
                click.secho(f"      {tool} │ {text}", fg="yellow", err=True)
            else:
                click.secho(f"      {tool} │ {text}", fg="cyan")
        return _cb
    line_cb = _make_verbose_cb(deployer.get_deployer_name())
```

Plus `stage_start`/`step_start`/`step_end`/`stage_end` lifecycle events
around each step, and one final `complete` event (`success`, `command`,
`execution_id`, `timestamp`, `data`) emitted from the command's own
teardown path, after everything else.

**Three findings this directly settles, none of them guessable from this
repo's own docstrings alone:**

1. **v1 has no `--follow`-equivalent flag at all.** Console-mode live
   streaming is driven directly by `--verbose` (Tier 1) — there was never
   a separate boolean for "stream live." v2 diverged here by inventing a
   new, dedicated `--follow`/`-f` flag instead of reusing `--verbose` as
   the trigger — a real, deliberate design difference worth naming
   explicitly rather than treating as an oversight (see "Design
   questions" below).
2. **v1 never combines live streaming with the single-envelope `json`
   format at all.** The two live-progress tiers are keyed off
   `ndjson`/`--verbose` specifically; `json` (v1's `_is_structured_output()`
   format) is buffered, same as v2's `json` today. The cfgint team's own
   ask — parseable **and** live, at the same time — was never something
   `--output json` could do in v1 either. `ndjson` is the feature that
   answers it, not a `--follow`+`json` compatibility fix.
3. **Every NDJSON event, including one tagged `"stream": "stderr"`, is
   written to v1's own real stdout** (`emit_ndjson()`'s `click.echo(...)`,
   no `err=True` anywhere in it). The `stream` field is metadata
   describing which stream the *subprocess line* came from, not an
   instruction for which of *v1's own* OS streams to write to — NDJSON
   mode funnels everything through one single, parseable channel. Only
   Tier 1 (console + `--verbose`) actually splits across real OS streams
   (`err=True` for `stream == "stderr"`) — and that's exactly the logic
   v2's current `--follow` already ported faithfully into
   `deploy_command.py`, confirming v2 got Tier 1's mechanics right; only
   the trigger condition (a new flag vs. reusing `--verbose`) differs.

Every console-only `click.echo` call in v1's `run_deploy_command.py` (and
there are dozens — AI risk gates, scheduled-window gates, work-item
pause/resume, policy results, stage/step progress, output collection) is
gated behind `self._is_console_output()`, with **zero warning emitted**
when a mode other than plain console is active and that line is skipped
— the same silent-gate shape this doc's sibling
(`cli-output-mode-interactions.md`) found for `--follow`. v2 isn't
introducing a new kind of silence; it's continuing v1's own, long-
standing convention of "only the active mode's own code path runs, no
announcement when another tier's behavior doesn't apply."

## Current Design / Progress

What v2 already has, reusable as-is:

- `on_line: Callable[[str, str, str], None]` — `deploy_run()`'s existing
  per-line callback (`(tool, stream, text)`), explicitly designed
  (ADR-0030) to be exactly what an NDJSON writer would consume. One
  difference from v1's own callback signature: v2's carries `tool` as a
  third argument (v1's `line_cb` is `(stream, text)`, with `tool` closed
  over at construction time instead) — a minor shape difference, not a
  blocker; an NDJSON writer can just include `tool` as an extra per-line
  field the way v1's own `step`/`stage` are already added.
- `format_json()`'s `indent: int | None = None` branch — already present,
  already documented as "which is what a future NDJSON writer needs"
  (one JSON object per line, no pretty-printing).
- `JsonReporter.step()` — currently a documented no-op ("per-step
  reporting is what `--output ndjson` will be for"); an NDJSON reporter
  would override this to actually emit something, rather than needing a
  new interface.
- `OUTPUT_FORMATS` (`options.py`) — currently `("console", "json")`;
  adding `"ndjson"` is additive, same shape v1's own four-entry list used
  (v2 already deliberately dropped v1's `"text"` — see `options.py`'s own
  comment — so v2 would land at three, not four).

What's genuinely new, not yet designed in detail:

- ~~An `NdjsonReporter` (or equivalent) implementing the same `Reporter`
  protocol...~~ **Not needed, confirmed during Phase 2 implementation.**
  Since the trigger is `--follow ndjson` (not a new `--output` value),
  `--output` stays `"json"` the whole time — the existing `JsonReporter`/
  `build_envelope()`/`format_json()` machinery already produces the
  correct final line automatically, completely unchanged. Live events are
  written directly via `click.echo(json.dumps(...))` inside `on_line`/
  `on_step`, exactly how console mode already bypasses the `Reporter`
  abstraction via `click.secho` — no new reporter class, no new
  `OUTPUT_FORMATS` member.
- A real per-line event writer reusing `on_line`'s existing plumbing,
  modeled on v1's `make_ndjson_line_callback()`. **Shipped** — see
  Implementation Plan.
- A decision on the final line's shape (see Design questions). **Decided
  and shipped**: reuses `build_envelope()`/`JsonReporter` unchanged, with
  one real gap found and fixed along the way — `JsonReporter`'s default
  `indent=2` pretty-prints the envelope across multiple lines, which
  broke NDJSON's one-object-per-line contract entirely. Fixed by adding a
  `compact: bool` parameter to `make_reporter()`/`command_run()`, forcing
  `indent=None` when `--follow ndjson` is active — a small, additive,
  layering-clean change (both functions already live in the `commands`
  layer), not a new mechanism.

## Design questions

- **Does `--output ndjson` imply live streaming automatically, or does
  `--follow` gate it? DECIDED (Phase 2, 2026-10-07) — neither, exactly.**
  Not a new `--output` value at all: `--follow` itself became an
  optional-value flag (`--follow` bare → `console`; `--follow ndjson` →
  NDJSON streaming), requiring `--output json`. This was chosen over
  matching v1's "`--output ndjson` always streams" shape specifically
  because `--follow` already shipped (Phase 1) as the one flag this whole
  investigation is about — reusing it, rather than inventing a parallel
  `--output` value, keeps exactly one trigger mechanism instead of two.
- **Does `--follow` get reused as `--verbose`'s own trigger for console-
  mode Tier 1 instead, matching v1 exactly** (collapsing two flags into
  one), **or does v2 keep `--follow` as its own separate, deliberate
  flag? DECIDED (Phase 2) — kept separate.** `--follow` is now the single
  trigger for *both* console streaming and NDJSON streaming (via its own
  value) — not folded into `--verbose`, which stays logging-only
  (`cli-output-mode-interactions.md`'s own counter-finding: v2's
  `--verbose`/`--follow` split is a deliberate improvement on v1's
  dual-purpose `--verbose`, not worth undoing).
- **Final event shape.** v1 emits a bespoke `{"event": "complete", ...}"`
  object, structurally different from `build_envelope()`'s own
  `{ok, command, version, context, summary, diagnostics, data}` shape
  that plain `--output json` already uses. Recommendation: make NDJSON's
  **final** line be the exact same `build_envelope()` output v2's `json`
  mode already produces (one more line, tagged however the per-line
  events are, e.g. `{"event": "complete", ...envelope fields unpacked...}`
  or simply the envelope itself as the last line with no extra
  wrapping) — gives a consumer one single, already-documented shape to
  parse for the final result, regardless of which format was used,
  rather than inventing a second "the real result" shape just for
  ndjson. **Confirmed directly against v1's own source what that final
  event should (and should not) contain**: v1's `complete` event's `data`
  field is sourced from `self._output_data`, commented in v1's own source
  as `# Structured result data` (accumulated facts like collected stage
  outputs) — it is **not** a transcript of the per-line `line` events
  already streamed earlier; those are emitted once, live, and never
  re-appear in `complete`. v2's own `deploy run` already matches this
  split today, incidentally: `JsonReporter.data` is left empty for this
  command (no assignment anywhere in `deploy_command.py`, unlike `values
  get`'s explicit `run.reporter.data = {...}`), so there's nothing today
  that would pull per-line text toward the final envelope in the first
  place. The final line should stay exactly as lean as plain `--output
  json` already is — streamed lines are transient progress, not part of
  the durable result.
- **Per-line event shape.** Start from v1's own proven shape
  (`event`/`step`/`stage`/`stream`/`text`/`ts`), adding `tool` (v2's
  `on_line` already carries it) as a new field v1 didn't have, and
  dropping `stage` — v2 has no concept distinct from `step` (see below).
  **Lifecycle events (`step_start`/`step_end`): resolved, not an open
  question anymore.** Checked directly against `deploy_run()`'s real loop
  structure (`deploy_controller.py`) — `on_step` (the existing callback
  `_step()` wraps) already fires at exactly these two boundaries for
  *every* step, uniformly across both the Terraform path and the Helm/
  Compose path: step-start (`"running step '{name}' via {tool}"`) and
  step-end (`"deployed step..."` / `"planned step... (dry-run)"`). An
  NDJSON writer can hook `on_step` directly, the same way it hooks
  `on_line` — zero new instrumentation needed in `deploy_controller.py`.
  One real simplification this also surfaced: v2 has no "stage" grouping
  distinct from "step" at all — unlike v1's nested stage→step loop,
  `workspace.spec.execution` is a flat list of steps — so the event shape
  should drop v1's `stage` field entirely rather than reconstruct a
  grouping v2 doesn't have. No finer, sub-step granularity exists today
  (e.g. no separate "init started"/"plan finished" events) — only
  whole-step boundaries — which is a real, deliberate scope limit for a
  first cut, not a gap: it matches the granularity `--follow`'s own
  console output already has today (prefixed lines, no per-phase
  headers).
- **Scope: `deploy run` only, or every command? Corrected — broader than
  previously stated here, checked directly against real v1 usage, not
  assumed.** Grepped v1's real `emit_ndjson`/`make_ndjson_line_callback`/
  `_is_ndjson_output` call sites directly (`git grep`, not the docstring-
  only read this doc's Overview was originally based on) — NDJSON is
  wired into **seven** v1 command files, not one: `builders/
  base_build_command.py`, `builders/plan_build_command.py`, `builders/
  run_build_command.py`, `builders/sbom_build_command.py`, `deploy/
  destroy_deploy_command.py`, `deploy/health_deploy_command.py`, `deploy/
  output_deploy_command.py`, plus `repo/sync_repo_solution_command.py` —
  eight, including `run_deploy_command.py` itself. Two genuinely different
  usage *patterns*, not one: (1) raw subprocess-line streaming
  (`deploy run`/`destroy`/`health`/`output`, `build run`/`plan` — the
  pattern this doc has focused on so far), and (2) **structured per-item
  progress events with no subprocess involved at all** — confirmed
  directly in `sync_repo_solution_command.py`, which emits one
  `{"event": "data", "type": "repo_sync_result", "repo": ..., "action":
  ..., "status": ..., "path": ..., "error": ...}` event per repo synced,
  wrapped in `stage_start`/`stage_end` events carrying a `count` — a
  command that processes a *list of independent items*, not a streaming
  subprocess, benefiting from NDJSON for the exact same reason: seeing
  each result as it completes rather than waiting for one buffered array.
  **This means NDJSON is a genuinely general-purpose format in v1** —
  "stream of independent, self-contained result events for any command
  doing multiple discrete units of work" — not a `deploy run`-specific or
  even subprocess-streaming-specific mechanism. The previous
  recommendation to scope the first cut to `deploy run` only undersold
  this; replacing it below.

  **Revised recommendation**: still build the *first* cut against
  `deploy run` only (it remains the single real, evidenced motivating
  case — the cfgint report), but **don't design the event/reporter shape
  in a `deploy run`-specific way** that would need reworking for a
  `repo sync`-style, non-subprocess, per-item command later. Concretely:
  keep the `line`/`step_start`/`step_end` event *shape* (above) specific
  to subprocess-streaming commands, but don't assume every future NDJSON
  consumer has subprocess lines at all — a `data`-type event (matching
  v1's own `repo_sync_result` shape) for structured per-item results
  should be considered a second, equally first-class event shape from the
  start, not a later bolt-on.

  **This also reopens the trigger-mechanism question** (the first Design
  question above, and [cli-output-mode-interactions.md](cli-output-mode-interactions.md)'s
  own Phase 1 decision to defer `ndjson` entirely and keep `--follow` a
  plain boolean for now): `--follow ndjson` as the trigger only makes
  sense for commands that already have a `--follow` flag at all — today,
  only `deploy run` does. A `repo sync`-style command with no subprocess
  to "follow" would need its own trigger (plausibly a real, standalone
  `--output ndjson` after all, specifically for that class of command) —
  meaning the two v1 usage patterns found here might end up with two
  *different* v2 trigger mechanisms, not one unified one. Not resolved
  here — flagged as a real open question for whenever this doc's own
  design moves past `deploy run`'s first cut.
- **stdout-only, matching v1 exactly, or split real OS streams?**
  Recommendation: match v1 exactly (Finding 3 above) — every NDJSON
  event, lifecycle and per-line alike, goes to actual stdout; `stream`
  stays metadata describing the *subprocess's* own stream, never v2's.
  Simpler contract for a consumer ("parse newline-delimited JSON from
  stdout, nothing else is on that fd") than deciding which event types
  go where.

## Related Decisions

- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — the
  real-CI-usage finding behind `STRATA_OUTPUT` existing as an env var at
  all; the same real pipelines this doc's motivating report comes from.
- [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md) —
  built `--follow`'s `on_line` plumbing with this exact future reuse in
  mind; this doc is that reuse, designed.
- [cli-output-mode-interactions.md](cli-output-mode-interactions.md) —
  the sibling doc tracking the concrete, near-term `--follow`/
  `STRATA_OUTPUT` bug fix this doc was split out of; that doc's own
  Option 5 points here.

## Remaining Work / Open Questions

- **Phase 2 (below) covers `deploy run` only — shipped.** Every other
  "Design questions" item above is now decided and shipped alongside it;
  nothing from that section remains open for `deploy run`'s own first
  cut.
- The `data`-type event (matching v1's `repo_sync_result` shape, for
  commands with no subprocess at all — `repo sync`-style) remains
  explicitly deferred — not designed or built, no trigger mechanism
  decided for that class of command. Revisit only if a second real need
  shows up (same discipline as everywhere else in this repo).
- Whether `build run` ever gains an analogous need is still out of scope
  — not a yes or no, just not being designed now.

## Implementation Plan

### Phase 2 — ✅ DONE (2026-10-07) — NDJSON streaming for `deploy run` (first cut)

- Upgrade `deploy_command.py`'s `--follow` from a plain boolean
  (`is_flag=True`) to an optional-value flag (`is_flag=False,
  flag_value="console", type=click.Choice(["console", "ndjson"])`) —
  bare `--follow` unchanged (`"console"`); `--follow ndjson` new.
- Guard: `if follow == "ndjson" and output != "json": raise
  UsageError("--follow ndjson requires --output json.")`.
- `on_line` gains a third branch (`elif follow == "ndjson":`) emitting
  `{"event": "line", "tool", "stream", "text", "ts"}` via
  `click.echo(json.dumps(...))` to stdout — `stream` stays metadata, per
  the stdout-only decision.
- `on_step` gets a matching ndjson-mode override emitting
  `{"event": "progress", "message", "ts"}` per call — the deliberate,
  documented scope-down from v1's structured `step_start`/`step_end`
  (v2's `on_step` only ever carries a flat message string).
- Final envelope: unchanged `build_envelope()`/`JsonReporter`, **plus** a
  new `compact: bool` parameter threaded through `make_reporter()`/
  `command_run()` forcing `indent=None` — found necessary during
  implementation (not anticipated in the design above): `JsonReporter`'s
  default `indent=2` pretty-prints the envelope across multiple lines,
  which silently broke NDJSON's one-object-per-line contract. `deploy
  run` passes `compact=(follow == "ndjson")` into `command_run(...)`.
- No `OUTPUT_FORMATS` change, no new Reporter class, no
  `deploy_controller.py` change — confirmed, the whole fix is contained
  to `deploy_command.py` plus the small additive `compact` parameter on
  two already-shared command-layer functions.
- **Tests** (`tests/strata/commands/test_commands_deploy.py`):
  `test_follow_ndjson_requires_output_json` (the guard) and
  `test_follow_ndjson_streams_line_and_progress_events_to_stdout`
  (asserts every stdout line parses as its own JSON object, both `line`
  and `progress` events appear, the final line is the normal, unmodified
  `build_envelope()` result with no `event` key).
- **Shipped as**: exactly the above. Full check suite clean: `mypy`
  (146 files), `ruff check`/`ruff format` clean, `lint-imports` kept,
  full suite **2179 passed** (23 in `test_commands_deploy.py`).
- **Code review (2026-10-07) found and fixed one real bug**: the new
  `line`/`progress` events' `json.dumps(...)` calls omitted
  `ensure_ascii=False`, unlike `format_json()`'s own call for the final
  envelope — non-ASCII subprocess text would have been `\uXXXX`-escaped
  in live events but rendered literally in the final line, an
  inconsistent contract within one stream. Fixed by matching
  `format_json()`'s exact policy (`ensure_ascii=False`) and dropping the
  unneeded `default=str` (every field is already a plain `str`). Added
  `test_follow_ndjson_does_not_escape_non_ascii_text` as a real
  regression test (not just a diff-level claim) — full suite
  **2180 passed** after the fix.

## Changelog

- 2026-10-06: Created, split out from
  [cli-output-mode-interactions.md](cli-output-mode-interactions.md)'s
  Option 5 per direct request to prepare NDJSON's own doc. Investigated
  v1's real implementation directly (`git show main:src/strata/commands/
  base_command.py` and `.../deploy/run_deploy_command.py` — not assumed
  from memory or this repo's own docstrings about it) and found three
  things not previously documented anywhere in this repo: (1) v1 has no
  `--follow`-equivalent flag at all — console-mode live streaming is
  triggered directly by `--verbose`; (2) v1 never combines live streaming
  with its own single-envelope `json` format — `ndjson` is a genuinely
  separate format, not a `json`-compatibility feature, which directly
  settles that the cfgint team's actual ask (parseable *and* live) was
  never answerable through `json`+`--follow` even in v1; (3) v1's NDJSON
  events (including ones tagged `stream: "stderr"`) all go to v1's own
  real stdout — the `stream` field is metadata about the *subprocess*
  line's origin, not a routing instruction for v1's own output. Laid out
  the concrete reusable pieces already in v2 (`on_line`, `format_json`'s
  `indent=None`, `JsonReporter.step()`'s documented no-op) and the open
  design questions (streaming trigger, final-event shape, per-line event
  shape, scope, stdout-only). No code written — design only.
- 2026-10-07: Answered a direct question (raised while reviewing
  `cli-output-mode-interactions.md`'s Option 1) about whether streamed
  lines get duplicated into the final result object. Checked v1's real
  `_output_data` directly: its own source comments it as "Structured
  result data" and it holds accumulated facts (e.g. collected stage
  outputs), not the raw per-line text already emitted via separate
  `line` events — v1 never re-included streamed lines in its final
  `complete` event. Also confirmed v2's own `deploy run` already leaves
  `JsonReporter.data` empty today. Updated the "Final event shape"
  question to state this directly: the final line should stay exactly as
  lean as plain `--output json` already is — streamed lines are
  transient progress, not part of the durable result.
- 2026-10-07: Corrected the "Scope" question with broader real evidence —
  grepped v1's actual `emit_ndjson`/`make_ndjson_line_callback` call
  sites directly (not just the two files read for the Overview) and
  found NDJSON wired into eight v1 command files, not one, across two
  genuinely different patterns: raw subprocess-line streaming (`deploy
  run`/`destroy`/`health`/`output`, `build run`/`plan`) and structured
  per-item progress events with no subprocess at all (`repo sync`'s own
  `repo_sync_result` "data" events, one per repo synced). NDJSON is a
  genuinely general-purpose format in v1, not `deploy run`-specific.
  Kept the recommendation to build the first cut against `deploy run`
  only, but added that the event-shape design shouldn't assume every
  future consumer has subprocess lines — a `data`-type event should be a
  first-class second shape from the start, not a bolt-on. Also surfaced
  a new, unresolved question this raises: `--follow ndjson` as a trigger
  only makes sense for commands that already have `--follow` (today,
  only `deploy run`) — a `repo sync`-style command would need its own
  trigger, possibly a real standalone `--output ndjson` after all for
  that class of command. Not resolved — flagged for whenever this doc's
  design moves past `deploy run`'s first cut.
- 2026-10-07: **Phase 2 implemented and shipped** — NDJSON streaming for
  `deploy run`, first cut. Decided the trigger question directly:
  `--follow` became the optional-value flag (`console`/`ndjson`), not a
  new `--output` value — no new `OUTPUT_FORMATS` member, no new Reporter
  class needed, since `--output` stays `"json"` throughout and the
  existing `JsonReporter`/`build_envelope()` machinery already produces
  the correct final line. Found and fixed one real gap not anticipated in
  the design: `JsonReporter`'s default `indent=2` pretty-prints the
  envelope across multiple lines, breaking NDJSON's one-object-per-line
  contract — fixed with a small, additive `compact: bool` parameter on
  `make_reporter()`/`command_run()`. `on_step`'s richer v1 lifecycle
  events were deliberately scoped down to a single generic `"progress"`
  event, matching v2's flatter `on_step` signature. Full check suite
  clean, full suite 2179 passed. The `data`-type event (repo-sync-style,
  non-subprocess commands) remains explicitly deferred, not built.
- 2026-10-07: Code review of Phases 1+2 together. Found and fixed one
  real bug: the new ndjson `line`/`progress` events' `json.dumps(...)`
  calls didn't pass `ensure_ascii=False`, unlike `format_json()`'s own
  call for the final envelope — non-ASCII text would render differently
  (escaped vs. literal) depending on which line of the same stream it
  appeared in. Fixed to match `format_json()`'s exact policy, dropped the
  now-unneeded `default=str`, and added a real regression test
  (`test_follow_ndjson_does_not_escape_non_ascii_text`) rather than
  trusting the diff alone. Everything else reviewed clean: secret
  redaction already happens upstream of both new `on_line` branches
  (`deploy_controller.py`'s `_redact()` runs before either callback sees
  text, confirmed unchanged); `compact` only affects the `json` branch of
  `make_reporter()` and is fully backward compatible for every other
  command (defaults to `False`, `indent=2` unchanged); the `UsageError`
  guard's placement after `command_run()` already opens is harmless
  (`compact` is computed from `follow` regardless, but only takes effect
  once `output == "json"`, and the guard still fires correctly before any
  real work happens). Full suite **2180 passed** after the fix.
- 2026-10-07: Processed this doc's shipped Phase 2 into the permanent
  living design doc, [deploy-plan-preview.md](../design/deploy-plan-preview.md)
  — its "Streaming" section now summarizes `--follow ndjson`'s shipped
  shape directly, its "Not Built Here" section lists the still-deferred
  `data`-type event and `build run` extension, and a new History entry
  records the real v1 grep evidence and the `compact`/`ensure_ascii` bugs
  found along the way. This doc itself stays in `docs/work/` as the full
  design record (v1 evidence, every option considered, every design
  question's resolution) — not merged or deleted.
