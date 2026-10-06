# CLI output-mode interactions (`--follow`, `STRATA_OUTPUT`) — Work

- Status: draft
- Last updated: 2026-10-06

## Overview

A real bug report from the cfgint team (`xyz-strata` 2.0.0a8, Azure DevOps/
Ubuntu 24.04): `strata deploy run --follow` silently has no effect whenever
output is JSON — whether via the explicit `--output json` flag or the
`STRATA_OUTPUT=json` env var — and nothing is emitted to say so. An
11-minute real `terraform apply` produced zero console output and looked
hung; it took real debugging effort to trace back to the global
`STRATA_OUTPUT=json` env var silently neutering `--follow`.

This doc exists for two reasons, not one: (1) track the concrete `--follow`
fix, and (2) — per direct request — use this as the occasion to look at the
*general mechanism* a console-only flag needs when combined with JSON
output, since `--follow` is very likely not going to be the last flag of
this shape, and the fix should be the right one generally, not just a
patch for this one flag. A second, separate claim in the same report
(`build run` allegedly not honoring `STRATA_OUTPUT` at all, unlike
`deploy run`) is tracked here too, currently unconfirmed against the real
source.

## Real evidence motivating this

Verbatim from the cfgint-reported issue:

> **Title:** `deploy run --follow` silently has no effect under JSON output
> (incl. via `STRATA_OUTPUT` env var) — no warning emitted
>
> `strata deploy run --follow` (streams live subprocess output,
> tool-prefixed e.g. `terraform │ ...`) silently does nothing whenever
> output is JSON mode — whether set via `--output json` or the
> `STRATA_OUTPUT=json` env var. This is documented in `--follow`'s own
> `--help` text ("Mutually exclusive with --output json... silently inert
> with --output json"), but nothing is actually **emitted** to tell the
> caller their flag had no effect.
>
> Our CI pipeline sets a solution-wide `STRATA_OUTPUT=json` env var,
> consumed by other commands (e.g. `strata values get`) for
> machine-parseable output. We assumed `deploy run --verbose --follow`
> would still show live terraform output during a real apply. Instead, an
> 11-minute real `terraform apply` (which got all the way to the
> second-to-last resource before failing) produced **zero** console
> output — only a single JSON envelope at the very end. The run looked
> hung for 11 minutes with no progress signal, and it took real debugging
> effort to trace this back to the global env var silently neutering
> `--follow`.
>
> Secondary inconsistency noticed along the way: `build run` does **not**
> honor `STRATA_OUTPUT` at all (confirmed against actual behavior), while
> `deploy run` does. This asymmetry between sibling subcommands is easy to
> misconfigure — we now have to explicitly override `STRATA_OUTPUT=console`
> just for our `deploy run` step while leaving it `json` everywhere else in
> the same pipeline.
>
> Environment: `xyz-strata` 2.0.0a8, Azure DevOps, Ubuntu 24.04 hosted
> agent. Invocation: `strata deploy run DEPLOYMENT --force --scope infra
> --verbose` (no explicit `--output`; `STRATA_OUTPUT=json` set
> pipeline-wide). Workaround: override `STRATA_OUTPUT=console` specifically
> for the `deploy run` step, add `--follow` alongside `--verbose`.

## Current Design / Progress

### Finding 1 — `--follow` silent no-op: confirmed, real bug

`deploy_command.py`'s `deploy_run_command()`:

```python
on_line: Callable[[str, str, str], None] | None = None
if follow and output == "console":
    def on_line(tool: str, stream: str, text: str) -> None:
        ...
```

When `follow=True` but `output != "console"`, `on_line` simply stays
`None` — nothing downstream ever reports that `--follow` was requested
and discarded. `output` is bound by the shared `output_option()`
(`envvar="STRATA_OUTPUT"`, see `options.py`), so the check `output ==
"console"` is identical regardless of whether JSON mode came from
`--output json` or the env var — the report's claim that both trigger it
is accurate, and the `--help` text's own "silently inert with `--output
json`" line never mentions `STRATA_OUTPUT` at all, despite having the
identical effect. Both the missing-warning behavior and the
documentation gap are confirmed directly from source, not just the bug
report's own description.

### Finding 2 — the `--verbose` analogy `--follow`'s own help text cites is not actually faithful

`--follow`'s help text says it's "matching `--verbose`'s own 'never make
JSON unparseable' precedent" — checked `verbose_option()` directly
(`options.py`): `--verbose` raises the log level, and **logs always go to
stderr, unconditionally, regardless of `--output`** (`command_run()`'s own
`configure_logging()` call, same for every command). `--verbose` is never
neutered under JSON — it keeps working exactly the same way, just on a
different stream than stdout.

`--follow` does not actually follow that precedent: it goes **completely
silent** under JSON, not merely rerouted to a different stream. The
analogy in its own help text describes a design `--follow` doesn't
actually implement — worth deciding whether to fix the implementation to
match the precedent it already claims (reroute to stderr, keep working),
or fix the claim (stop citing `--verbose`, since "has no effect" and
"redirected but still has an effect" are different behaviors).

### Finding 3 — the `build run`/`STRATA_OUTPUT` asymmetry: not reproduced from source

`build_command.py` and `deploy_command.py` decorate with the exact same
`@output_option` (`options.py`) — identical function, identical
`envvar="STRATA_OUTPUT"`, identical `--output`/`-o` flag and default.
Grepped every raw `click.echo`/`print` call in `src/strata/commands/`:
none exist in `build_command.py` or `build_controller.py` at all —
`build run`'s own progress reporting goes entirely through `run.step()`,
which is a documented no-op under `JsonReporter`, same as every other
command (`audit`, `graph`, `promote`, `path` all gate their own extra
console rendering behind an identical `if output == "console":` check).

No code-level basis for the asymmetry was found. Two real possibilities,
not mutually exclusive:

- An Azure DevOps pipeline-scoping artifact (a pipeline-wide `env:` block
  not propagating into a specific step/task the way the team expected) —
  not a strata bug.
- A real difference not yet identified without the team's actual
  side-by-side invocation + output.

**Not closing this as "not a bug" yet** — tracked as a genuinely open item
below, pending a confirmed repro. The report is being taken at face value
for now; "could still be a pipeline bug" is the working hypothesis, not a
conclusion.

### The general mechanism — is `--follow` the only flag of this shape?

Surveyed every command for a flag whose *entire effect* only exists in
console mode (as opposed to a command simply rendering *something*
differently per mode, which every command with a table/list output does
intentionally and is not the same concern):

- `--verbose` — logs to stderr unconditionally; never neutered (see
  Finding 2).
- `--quiet` — suppresses chrome, never findings; already documented as
  "console only — JSON has no decoration to suppress" in
  `make_reporter()`'s own docstring, but notably this is a **silent, by-
  design no-op too**, with no warning either. Arguably the same class of
  gap as `--follow`, just lower-stakes (nothing is lost — JSON was always
  undecorated anyway, there is no missing *information*, only a
  redundant flag).
- Every other command-specific flag (`--resolve`, `--dry-run`,
  `--smoke-test`, `--pin`, the `--change-*` family, `--stage`/`--scope`)
  affects what the command actually *does*, not merely how it's
  *rendered* — none of them have a console-only dependency.

**`--follow` is the only flag in the whole CLI today whose entire,
explicit purpose silently stops working under JSON.** `--quiet` is a
close cousin but categorically lower-risk (no information is lost by
definition). This means the "general mechanism" question is really: build
one for `--follow` now, in a way a second flag like it could reuse later,
rather than conclude a generic mechanism is unjustified by zero other
current users — `--follow` alone, given the real-world impact already
reported (an 11-minute silent run), is enough justification.

## Examples — combinations and today's actual behavior

Concrete invocations, to make the interaction (and Click's own
flag-beats-env-var precedence, which is what the reporter's own
workaround relies on) unambiguous. All examples assume `terraform` is the
underlying tool, matching the real report.

| #   | `STRATA_OUTPUT` env var | `--output`/`-o` flag      | `--follow`/`-f`       | What happens today                                                                                                                                                                                                                                                                                                                                                  |
| --- | ----------------------- | ------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | unset                   | unset (default `console`) | yes                   | **Works as expected** — live `terraform │ ...` lines stream to stdout as they arrive.                                                                                                                                                                                                                                                                               |
| 2   | unset                   | unset (default `console`) | no                    | Normal console output only (header/steps/summary) — no live subprocess lines, same as always; `--follow` was never requested.                                                                                                                                                                                                                                       |
| 3   | `json`                  | unset                     | yes                   | **The reported bug.** `output` resolves to `json` via the env var; `on_line` stays `None`; zero live output, zero warning — only the final JSON envelope on stdout once the whole run finishes.                                                                                                                                                                     |
| 4   | unset                   | `json`                    | yes                   | Same silent result as #3 — the explicit flag produces an identical `output == "json"`, so the same `if follow and output == "console":` gate fails the same way. Confirms the bug is keyed on *effective* output mode, not on *how* it was set.                                                                                                                     |
| 5   | `json`                  | `console` (explicit)      | yes                   | **Works** — this is the reporter's own documented workaround. Click's own precedence (explicit flag beats `envvar` fallback) means the command-line `--output console` wins over the pipeline-wide `STRATA_OUTPUT=json`, so `output == "console"` is true and `--follow` streams normally.                                                                          |
| 6   | `json`                  | unset                     | no                    | Clean, single JSON document on stdout, nothing on stderr beyond normal logs — the common, intended CI shape every other command (`values get`, etc.) already relies on. No bug here; included only to show the contrast with #3.                                                                                                                                    |
| 7   | `json`                  | unset                     | yes, plus `--verbose` | Still the bug from #3 for the streamed lines — but `--verbose`'s own INFO-level logs keep appearing on stderr throughout the run regardless (Finding 2's point: logging never goes silent under JSON, only `--follow`'s stream does). An operator watching stderr sees *some* progress signal (log lines) but not the live `terraform │ ...` output they asked for. |

**If fix option 2 or 4 (reroute to stderr) is adopted**, row #3/#4/#7 would
change to: live `terraform │ ...` lines appear on stderr instead of
nowhere, while stdout still emits exactly one clean JSON document at the
end — the same shape `--verbose` already has, and the shape `--follow`'s
own current help text incorrectly implies already exists.

## Options considered for the fix — not decided yet

1. **Emit a one-line stderr warning, keep dropping the stream itself**
   (report's own suggested fix #1). Minimal change, matches the existing
   `click.secho(..., err=True)` pattern `--follow`'s own error-stream
   lines already use. Does not actually solve the "looked hung for 11
   minutes" problem — the operator still gets no live signal, only a
   one-time notice at the start that they won't get one.
2. **Reroute the streamed lines to stderr under JSON instead of dropping
   them** — makes `--follow` actually match the `--verbose` precedent its
   own help text already claims, instead of just saying so. Keeps
   stdout's single-JSON-document guarantee fully intact (`--verbose`
   already proves this is safe), and actually fixes the real-world
   problem reported (a real, long-running `terraform apply` would show
   live progress on stderr even under `STRATA_OUTPUT=json`). Slightly
   more change than option 1, but solves the actual complaint instead of
   only acknowledging it.
3. **Reject the combination at argument-parsing time** (report's own
   suggested fix #2). Simplest to reason about, but means a CI pipeline
   that sets `STRATA_OUTPUT=json` globally and conditionally adds
   `--follow` for one step (exactly this reporter's own workaround) would
   have to unset/override the env var for that step regardless — doesn't
   remove the friction the report is actually about, just converts a
   silent failure into a loud one.
4. **Combine 2 and a (lighter) version of 1** — reroute to stderr
   (actually fixing the problem), plus a one-line notice the *first* time
   a line is about to be rerouted, so an operator watching stderr knows
   *why* `terraform │ ...` lines are appearing there instead of stdout
   under this mode. Most complete, most moving parts.

No option selected yet — needs a decision before implementation.

## Related Decisions

- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — the
  real-CI-usage finding (`STRATA_OUTPUT: json` set once, pipeline-wide,
  in both reference repos) that `output_option()`'s own docstring already
  cites as the reason `--output`/`-o` falls back to an env var at all.
  This doc's whole premise — a global `STRATA_OUTPUT=json` interacting
  badly with a per-step flag — is exactly the real usage pattern that
  ADR already predicted and designed for; this is the first confirmed
  case of that interaction actually causing a real problem.

## Remaining Work / Open Questions

- **Decide which fix option (1-4 above) to implement for `--follow`.**
  Leaning toward option 2 (or 4) over the report's own suggested option 1,
  since 1 only acknowledges the problem rather than solving it — but not
  decided yet, flagging for discussion before writing any code.
- **Confirm or refute the `build run`/`STRATA_OUTPUT` asymmetry (Finding
  3) with a real repro.** Needs the cfgint team's actual pipeline YAML
  (or an exact local repro) showing `build run` genuinely ignoring the
  env var — not yet reproduced by reading the source, where both commands
  share identical plumbing. Do not assume it's "just a pipeline config
  issue" without checking; do not assume it's a real code bug without a
  repro either — currently a genuine open question, not a conclusion in
  either direction.
- **Decide whether `--follow`'s `--help` text should keep citing
  `--verbose`'s precedent, or whether that reference should be removed/
  corrected regardless of which fix option is chosen** — today it's
  inaccurate (Finding 2) independent of the fix decision.
- **Consider whether `--quiet`'s own silent no-op under JSON is worth any
  change at all**, now that this doc exists — current read is no (lower
  stakes, no information lost, nothing to warn about), but worth a
  conscious decision rather than leaving it unexamined by omission now
  that a sibling flag's identical shape is being fixed.
- Not yet decided: does the chosen fix belong as a one-off inline check
  in `deploy_command.py` (matches today's scale — one flag, one command),
  or does it warrant a small shared helper (e.g. in `options.py`) now,
  anticipating a second console-only flag later? Revisit once the fix
  option itself is chosen — the shape of the fix determines whether
  extraction is worth it.

## Changelog

- 2026-10-06: Created, from a real cfgint bug report. Confirmed Finding 1
  (`--follow` silently drops the stream under JSON/`STRATA_OUTPUT=json`,
  no warning emitted — accurate to the report) and Finding 2 (the
  `--verbose` precedent `--follow`'s own help text cites is not actually
  faithful — `--verbose` reroutes to stderr and keeps working; `--follow`
  goes fully silent instead) directly against source. Could not reproduce
  Finding 3 (`build run` allegedly ignoring `STRATA_OUTPUT`) from source —
  `build_command.py`/`deploy_command.py` share byte-identical
  `output_option()` plumbing; left open pending a real repro, not closed
  either way. Surveyed every other CLI flag for the same console-only
  shape — `--follow` is the only one whose entire purpose currently
  disappears silently under JSON; `--quiet` is a lower-stakes cousin
  (no information lost). Laid out four candidate fix options, not yet
  decided between. No code changed yet — design/investigation only, per
  explicit request.
- 2026-10-06: Added a worked "Examples" table — 7 concrete `STRATA_OUTPUT`/
  `--output`/`--follow` (and one `--verbose`) combinations and today's
  actual behavior for each, including the reporter's own documented
  workaround (row 5: an explicit `--output console` beats a pipeline-wide
  `STRATA_OUTPUT=json` per Click's own precedence) and what changes under
  the leading fix options (rows 3/4/7, if streamed lines reroute to
  stderr instead of disappearing).
