# Diagnostic Guidance — Corrective Hints for Errors/Warnings (Gap)

- Status: draft — gap identified and evidenced, not designed
- Last updated: 2026-10-07
- Related: [ndjson-output.md](ndjson-output.md) (where this was first
  surfaced, as an unrelated tangent while reviewing that doc's own
  "Shipped design" section — NDJSON is purely a streaming/transport
  format concern, orthogonal to this), [cli-output-mode-interactions.md](cli-output-mode-interactions.md)

## Overview

Raised directly while reviewing `ndjson-output.md`: does the CLI have any
mechanism for telling a user *what to do* about an error or warning, not
just *what went wrong*? Checked directly, not assumed — the answer today
is effectively no, anywhere, for any command.

This is a genuinely separate concern from output *format* (console/json/
ndjson, `ndjson-output.md`'s own scope): it's about the *content* of a
`Diagnostic`, independent of which format eventually renders it.

## Real evidence (checked directly, not assumed)

- **`Severity.INFO` (`utils/diagnostics.py`) is used in exactly 5 call
  sites in the entire codebase**, concentrated in 2 files: `controllers/
  audit_read.py` (4×, all "no audit records found"/"no records match the
  given filters" empty-result notices for `strata audit status`/
  `changes`) and `commands/sln_command.py` (1×, echoing `sln init`/
  `update`'s own scaffold step messages back as diagnostics). Every other
  command — `build`, `deploy`, `validate` — only ever emits `ERROR`/
  `WARNING`, never `INFO`.
- **Zero matches anywhere in `commands/`** for any of `Hint:`/`Tip:`/
  `Try running`/`Did you mean`/`Suggestion:` — there is no established
  convention in this codebase for attaching corrective/remediation text
  to a diagnostic at all, not just an underused one.
- **Concrete example — the knowledge already exists, it just never
  reaches the user.** `sbom_controller.py`'s `_report_missing_data()`
  (the real function backing `cve_policy`'s `on_missing_data` gate)
  produces exactly `"cve_policy: cannot evaluate — {reason}."` — nothing
  more. But the surrounding docstring (and `CveMaxSeverityPolicyModel`'s
  own docstring) already states the exact recommended fix verbatim: *"set
  `on_missing_data: block`"*, with reasoning. That knowledge lives in a
  code comment and a design doc; a user who actually hits this in a real
  `build run` gets none of it.
- **Partial, inconsistent precedent already exists one layer down.**
  `CveScannerIntegration.ensure_available()` *does* include actionable
  guidance directly in its error text ("Install trivy (https://trivy.dev)
  or grype (https://github.com/anchore/grype)") — so the instinct that
  "an error should say what to do" already exists in this codebase, just
  not as a deliberate, reusable `Diagnostics`-level convention; it's one
  integration author's own ad hoc choice, not a pattern applied
  consistently anywhere else.

## Design questions (open — none decided yet)

1. **Shape.** A new `hint: str | None` field on `Diagnostic` itself
   (travels with the triggering error/warning as one unit), or a
   conventionally-paired `Severity.INFO` diagnostic emitted immediately
   after (reuses the one mechanism that already exists, but separates
   the "what" from the "what to do" into two list entries a renderer has
   to associate itself)?
2. **Rendering.** If a `hint` field is chosen: does it affect `.ok`? (No
   — orthogonal to severity, same reasoning `Diagnostics.ok`'s own
   docstring already applies elsewhere.) How does console mode render it
   (a second, indented/dimmed line under the finding)? What key does it
   get in the JSON/NDJSON diagnostic object (`Diagnostic.to_dict()`)?
3. **Where does hint text live?** Inline at each call site (today's
   style for `message=`), or a centralized lookup keyed by `code` (every
   `Diagnostic` already carries a stable `code` field) — so hint text
   can be authored, tested, and updated independently of the triggering
   logic, and the same `code`'s hint isn't duplicated across multiple
   call sites that can raise it?
4. **Scope.** Retrofit every existing error/warning with a hint
   eventually, or add hints opportunistically going forward, starting
   with the highest-value, already-identified cases (`cve_policy`'s own
   missing-data gate; generalizing `ensure_available()`'s ad hoc style
   into the real convention)?
5. **Real-usage evidence — not yet gathered.** Is this a felt need by a
   real consumer (a support request, a confused CI run, something in
   `haven`/`cfg-int-deployment`), or an internal nice-to-have noticed in
   passing while reviewing an unrelated doc? Same "evidence over
   assumption" discipline this repo applies everywhere else — worth
   checking before investing in a design, not assumed just because the
   gap is real.

## Not yet decided / explicitly out of scope

Nothing designed or built yet — this is a pure gap-flagging doc, written
immediately after the gap was found, before any design work started.

## Changelog

- 2026-10-07: Created — gap surfaced while reviewing
  [ndjson-output.md](ndjson-output.md)'s own "Shipped design" section,
  prompted by a direct question about whether any command emits
  corrective guidance. Confirmed via direct grep (not assumed) that
  `Severity.INFO` has only 5 real call sites codebase-wide and that no
  `Hint:`/`Tip:`/`Suggestion:`-style convention exists anywhere in
  `commands/`. Found one concrete example of the gap's real cost
  (`cve_policy`'s own missing-data message omits a fix the surrounding
  docstring already states) and one partial, inconsistent precedent
  (`CveScannerIntegration.ensure_available()`'s own install-guidance
  text). No design decisions made — design questions and real-usage
  evidence gathering are both still open.
