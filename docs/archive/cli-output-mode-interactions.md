# CLI Output-Mode Interactions (`--follow`, `STRATA_OUTPUT`)

- Status: **Archived — shipped**. Current state lives in
  [deploy-plan-preview.md](../design/deploy-plan-preview.md)'s "Streaming"
  section. The `--follow` bug fix needed no ADR (a correction of
  ADR-0030's own "silently inert" line, left unedited per this repo's
  immutable-ADR convention — the fix is documented in the design doc
  instead); the NDJSON trigger mechanism this doc's Option 5 led to has
  its own decision, [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md).
  Kept here as the full investigation record (real bug report, every
  finding, every option considered) rather than deleted.
- Last updated: 2026-10-08
- Related: [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md)
  (the real-CI-usage finding behind `STRATA_OUTPUT` existing at all —
  this doc's whole premise is the first confirmed case of that
  interaction causing a real problem), [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md)
  (built `--follow` itself; its "silently inert with `--output json`"
  line is what this doc found and fixed), [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md)
  (the NDJSON trigger decision this doc's Option 5 led to),
  [ndjson-output.md](ndjson-output.md) (sibling archived doc, split out
  of this one's Option 5), [diagnostic-guidance.md](../work/diagnostic-guidance.md)
  (an unrelated gap surfaced while reviewing the sibling doc, still open)

## Overview

A real bug report from the cfgint team (`xyz-strata` 2.0.0a8, Azure
DevOps/Ubuntu 24.04): `strata deploy run --follow` silently had no effect
whenever output was JSON — whether via `--output json` or the
`STRATA_OUTPUT=json` env var — with nothing emitted to say so. An
11-minute real `terraform apply` produced zero console output and looked
hung; it took real debugging effort to trace back to the global
`STRATA_OUTPUT=json` env var silently neutering `--follow`. A second,
separate claim in the same report (`build run` allegedly not honoring
`STRATA_OUTPUT` at all, unlike `deploy run`) was tracked here too.

This doc served two purposes: fixing the concrete `--follow` bug, and —
per direct request — using the occasion to look at the *general
mechanism* a console-only flag needs when combined with JSON output,
since `--follow` was unlikely to be the last flag of this shape.

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

## Findings

1. **`--follow` silent no-op — confirmed, real bug.** `deploy_command.py`
   gated `on_line` construction behind `if follow and output ==
   "console":` — under any JSON mode (flag or env var), `on_line` stayed
   `None` with no warning at all. Confirmed directly from source, not
   just the report's own description.
2. **The `--verbose` analogy `--follow`'s own help text cited was not
   faithful.** `--verbose` raises the log level and logs always go to
   stderr unconditionally — never neutered under JSON, just on a
   different stream. `--follow` went fully silent instead of rerouting —
   a design that claimed a precedent it didn't actually implement.
3. **The `build run`/`STRATA_OUTPUT` asymmetry — refuted by a real repro,
   not just source-reading.** `build_command.py`/`deploy_command.py`
   share byte-identical `@output_option` plumbing; no raw `click.echo`
   bypasses `run.step()` in either. Ran both commands directly against
   this repo's own `config/` dogfooding solution with `STRATA_OUTPUT=json`
   set (`xyz-strata` 2.0.0a9) — both produced exactly one clean, correctly
   shaped JSON document, no asymmetry observed. Git history confirms
   `build_command.py` has had `@output_option` since the commit that
   first introduced `build run`, and the `STRATA_OUTPUT` envvar mechanism
   predates the `2.0.0a8` release this bug was reported against by 46
   commits. No point in this repo's history shows the claimed asymmetry —
   most likely an Azure DevOps pipeline-scoping artifact on the
   reporter's own side, not a strata defect.
4. **`--follow`'s plumbing was always meant to feed a future NDJSON
   writer, not stay console-only.** v1 had `--output ndjson` for
   streaming deploy progress; v2 never ported it, but `on_line`'s
   `(tool, stream, text)` shape and `deploy_run()`'s own secret-redaction
   wrapper around it were explicitly designed, from `on_line`'s
   introduction (ADR-0030), to protect "today's `--follow` writer, a
   future NDJSON writer" alike — the two were never separate mechanisms.
   This led directly to [ndjson-output.md](ndjson-output.md) and
   [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md).
5. **`--follow` was the only CLI flag whose entire purpose silently
   stopped working under JSON.** Surveyed every other flag:
   `--verbose` logs to stderr unconditionally; `--quiet` suppresses
   chrome only (a lower-stakes, by-design no-op under JSON — no
   information is lost, since JSON was always undecorated); every other
   flag changes what a command *does*, not merely how it renders.

## Examples — combinations and the behavior at the time of the report

| #   | `STRATA_OUTPUT` | `--output`           | `--follow`        | Behavior (pre-fix)                                                                                      |
| --- | --------------- | -------------------- | ----------------- | ------------------------------------------------------------------------------------------------------- |
| 1   | unset           | unset (`console`)    | yes               | Live `terraform │ ...` lines stream to stdout.                                                          |
| 2   | unset           | unset (`console`)    | no                | Normal console output only — no live lines (none requested).                                            |
| 3   | `json`          | unset                | yes               | **The reported bug** — `on_line` stays `None`; zero live output, zero warning.                          |
| 4   | unset           | `json`               | yes               | Same silent result as #3 — keyed on *effective* output mode, not on how it was set.                     |
| 5   | `json`          | `console` (explicit) | yes               | Works — Click's own precedence (explicit flag beats `envvar`) was the reporter's documented workaround. |
| 6   | `json`          | unset                | no                | Clean single JSON document — the common, intended CI shape; no bug.                                     |
| 7   | `json`          | unset                | yes + `--verbose` | Still the bug for streamed lines, but `--verbose`'s own stderr logs kept appearing regardless.          |

Post-fix, rows 3/4/7 changed: live lines reroute to **stderr** instead of
disappearing, while stdout still emits exactly one clean JSON document —
the shape `--verbose` already had, and the shape `--follow`'s own help
text had incorrectly implied already existed.

## Shipped fix

`deploy_command.py`'s gate changed from `if follow and output ==
"console":` to `if follow:` — `on_line` is now always constructed when
`--follow` is passed. Inside it, lines reroute to **stderr** whenever
`stream == "stderr" or output == "json"` (one `to_stderr` bool, same
`click.secho(..., err=to_stderr)` call for both origins) — console mode's
dual-stream colour-coding is unchanged, and stdout under JSON still emits
exactly one clean document. `--follow`'s `--help` text was rewritten to
state the real behavior directly instead of citing `--verbose`'s
precedent inaccurately. `--quiet`/`--verbose` were both confirmed to
already sit entirely outside this mechanism (`--quiet` only ever touches
`ConsoleReporter`'s own header/step/footer chrome; `--verbose` only ever
raises the log level) — neither needed any change.

Tests: `test_follow_streams_tool_prefixed_lines_on_console_output`
(unchanged); `test_follow_is_silently_inert_with_json_output` renamed to
`test_follow_reroutes_to_stderr_with_json_output` and rewritten to assert
the fix via `CliRunner`'s separate `result.stdout`/`result.stderr`
capture, plus `json.loads(result.stdout)` confirming stdout still parses
as exactly one document. Full check suite clean throughout: mypy, ruff,
lint-imports, pytest (2177 passed at ship time).

## The timing-vs-format question, and its resolution

The real underlying tension this doc surfaced: `--output` describes
*what shape* a command's output takes (a **format** concern); `--follow`
describes *when* subprocess output becomes visible (a **timing**
concern), logically independent of format — yet the original code
conflated them (`--follow` only worked when `output == "console"`).

Three mental models were considered for resolving this cleanly:

1. **Decouple `--follow` from `--output` fully** — `--follow` always
   streams, for any `--output` value; under `json` each line becomes its
   own per-line JSON event. Rejected (see
   [ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md)): it
   would make `--output json`'s own documented single-envelope contract
   depend conditionally on whether `--follow` was also set — the same
   class of surprise this whole investigation exists to eliminate, just
   relocated onto `--output json` instead of `--follow`.
2. **Keep them coupled, but document the coupling honestly** instead of
   hiding it behind a silent no-op — console streams, json doesn't (by
   the format's own contract), ndjson (once built) always streams.
3. **Fold `--follow` into `--verbose`/`ndjson`, matching v1's own
   precedent** (v1 never had a `--follow`-equivalent flag at all).
   Rejected — v2's `--verbose` is already single-purpose (logging only);
   folding `--follow` back in would undo an already-cleaner separation of
   concerns v2 made deliberately, not accidentally.

**Resolved in practice** by what shipped
([ADR-0033](../decisions/0033-ndjson-streaming-follow-trigger.md)):
Option 1's concrete shape, with one simplification — instead of a second
`--stream-format` option, `--follow` itself absorbed the stream-format
question as an optional-value flag (`--follow` bare → console, `--follow
ndjson` → NDJSON, requiring `--output json`). `--output` stayed a pure
format concern, unchanged; `--follow`'s *value* carries the timing +
stream-format question together.

## History

- The `--verbose` analogy `--follow`'s original help text cited was
  grounded in the same premise as the original silent-no-op design
  itself ("matching `--verbose`'s own behaviour") — disproving that
  premise (Finding 2) is what made the stderr-reroute fix the obvious
  choice over merely emitting a warning (the bug report's own suggested
  fix #1), which would have acknowledged the problem without solving the
  "looked hung for 11 minutes" complaint.
- The `build run`/`STRATA_OUTPUT` asymmetry claim could not be
  reproduced from source on first read, and was deliberately left open
  rather than dismissed — closing a reported defect on "I couldn't find
  it in the code" alone would have been premature. It was only genuinely
  closed once an actual repro against this repo's own dogfooding
  solution, plus a git-history check, directly contradicted the claim at
  every point in this project's history.
- Considered building `--output ndjson` as a sibling fix to `--follow`
  inline in this same doc; split it out once the scope became clear
  (new format member vs. flag-value decision, event shapes, v1 parity
  evidence) — kept as [ndjson-output.md](ndjson-output.md), its own
  sibling archived doc.
