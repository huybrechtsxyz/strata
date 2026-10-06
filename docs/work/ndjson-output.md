# `--output ndjson` — Work

- Status: draft
- Last updated: 2026-10-06

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

- An `NdjsonReporter` (or equivalent) implementing the same `Reporter`
  protocol `ConsoleReporter`/`JsonReporter` already share, so a command
  body still never branches on format itself.
- A real per-line event writer reusing `on_line`'s existing plumbing,
  modeled on v1's `make_ndjson_line_callback()`.
- A decision on the final line's shape (see Design questions).

## Design questions — not settled yet

- **Does `--output ndjson` imply live streaming automatically (matching
  v1 exactly — Tier 2 is simply "the active format," no separate flag
  needed), or does `--follow` still gate it in v2?** Recommendation
  leaning toward matching v1: `--output ndjson` always streams, full
  stop — simpler mental model, and avoids yet another two-flag
  interaction needing its own row in `cli-output-mode-interactions.md`'s
  own examples table. If adopted, `--follow --output ndjson` together
  would just make `--follow` a harmless, redundant no-op (safe
  redundancy, not the silent-failure shape this whole investigation
  started from) rather than needing its own special-cased warning.
- **Does `--follow` get reused as `--verbose`'s own trigger for console-
  mode Tier 1 instead, matching v1 exactly** (collapsing two flags into
  one, removing `--follow` entirely), **or does v2 keep `--follow` as its
  own separate, deliberate flag** (today's actual shape, diverged from
  v1 on purpose or by accident — not yet established which)? This is the
  one open question worth a direct decision rather than a default,
  since it changes a already-shipped, documented flag's meaning.
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
  ndjson.
- **Per-line event shape.** Start from v1's own proven shape
  (`event`/`step`/`stage`/`stream`/`text`/`ts`), adding `tool` (v2's
  `on_line` already carries it) as a new field v1 didn't have. Lifecycle
  events (`stage_start`/`step_start`/`step_end`/`stage_end`) are a
  separate, real design question: v2's `deploy_run()` doesn't currently
  have an obvious hook at exactly those boundaries the way v1's own
  stage/step loop did — needs checking against `deploy_run()`'s actual
  current loop structure before deciding whether to add them or start
  with only `line` events.
- **Scope: `deploy run` only, or every command?** v1's own
  `emit_ndjson()`/`make_ndjson_line_callback()` machinery lives on the
  generic `base_command.py`, available to any command, but the real,
  evidenced need (both in v1's own usage and in the cfgint report) is
  specifically long-running, incrementally-progressing provisioner work
  — `deploy run` (and arguably `build run`'s own provisioner/workload
  materialisation, though that doesn't stream subprocess output today
  even in console mode). Recommendation: scope the first cut to
  `deploy run` only, matching both v1's own real usage and the actual
  motivating complaint — extend later only if a second real need shows
  up, same "evidence over assumption" discipline this repo already
  applies everywhere else.
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

- All of "Design questions" above — none settled yet.
- Not yet checked against v2's *current* `deploy_run()` loop structure in
  detail: where exactly would stage/step lifecycle events hook in, and
  does v2 even have an equivalent "stage" concept at the same granularity
  v1's own `DeploymentStageModel` loop did (v2's own stage/scope
  filtering exists — `--stage`/`--scope` on `deploy run` — but whether
  its internal loop shape matches v1's stage/step nesting closely enough
  to reuse the same event granularity needs a direct read, not assumed
  from this doc alone).
- Whether `build run` ever gains an analogous need is explicitly out of
  scope for the first cut (see "Scope" above) — not a yes or no, just
  not being designed now.
- This doc does not resolve `cli-output-mode-interactions.md`'s own near-
  term `--follow` fix decision (Options 1-4 there) — that can proceed
  independently of whether/when this doc's design gets built, per that
  doc's own "not mutually exclusive" framing of Option 5.

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
