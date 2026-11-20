# Diagnostic Guidance — Corrective Hints — Design

- Status: current
- Last updated: 2026-11-20

## Overview

A finding says what is wrong. When strata also knows what to do about it, the finding carries that too, as a
**hint**: one short imperative sentence, kept apart from the message so every output format can show it as advice
rather than as part of the problem.

## The `hint` field

`Diagnostic.hint: str | None` ([diagnostics.py](../../src/strata/utils/diagnostics.py)), passed through
`Diagnostics.add()` and its `error`, `warning` and `info` helpers. The message and the hint travel as one finding, so
no renderer has to pair two list entries.

- **Severity:** a hint never changes it, and never affects `Diagnostics.ok`.
- **Console:** a second line under the finding, aligned with its text and dimmed (`hint: ...`).
- **JSON and NDJSON:** `diagnostics[].hint`, left out when absent, like every other optional field.
- **VS Code:** appended to the problem's message (`Hint: ...`).

## Writing a hint

- **One short imperative sentence.** The message ends at what is wrong; "Do X" goes in the hint. A finding whose
  message used to end in "... do X" is split when it is touched.
- **At the call site.** A hint often names the document, field or flag to change, which only the call site knows.
- **For a config reference's findings**, the reference lists each code with what to do (for example
  [version.md](../config/version.md), "Findings").

### "Cannot evaluate" findings

Every policy gate that can be left without data has an `on_missing_data` setting (`block`, `warn`, `skip`), and its
missing-data finding follows one hint rule, so the trade-off is stated wherever it applies:

| `on_missing_data` | Severity | Hint                                                                                         |
| ----------------- | -------- | -------------------------------------------------------------------------------------------- |
| `block`           | error    | fix the cause; to let the run continue without the check, set `on_missing_data` to `warn`    |
| `warn`            | warning  | fix the cause; keep `on_missing_data` at `block` where a run must never pass unchecked       |
| `skip`            | info     | none: the trace that the check did not run is the point                                      |

The cause is gate-specific where strata knows it (`cve_policy`: install trivy or grype and let the build write
`sbom.json`; `checkov_policy`: install checkov and let the build materialise what it scans). `release_gate` and
`image_gate` name their field (`release_gate_policy.on_missing_data`) and leave the cause to the message, which says
what failed.

## Scope

Opportunistic. A new finding gets a hint when strata knows the fix; an existing one gets it when it is touched. The
version composition findings were the first, the missing-data gates followed.

## Not covered

- **A lookup of hints by `code`**, for a hint that never varies. Possible later; every hint so far names something
  only its call site knows.
- **Listing findings without a hint** (in `strata validate` or a test). Many findings need none (an `info` trace, a
  message that already is the fix), so a list would be noise rather than a to-do.

## Related Decisions

- [version-composition.md](version-composition.md) — the first findings with hints.
- [ndjson-output.md](../archive/ndjson-output.md) and
  [cli-output-mode-interactions.md](../archive/cli-output-mode-interactions.md) — the output formats a hint is
  rendered in.

## History

- The gap surfaced while reviewing the NDJSON output: no command told a user what to do, only what went wrong. A
  search found no `Hint:`/`Tip:`/`Suggestion:` convention anywhere, `Severity.INFO` used at five call sites, and a
  telling example: `cve_policy`'s missing-data message left out the fix its own docstring stated
  (`on_missing_data: block`). One integration already put install advice in its error text, an ad hoc precedent.
- Real evidence came with the 2.6.3 report of `version_workspace_mismatch` from config-repo: a user who hit a
  finding and had to ask what to change. The field was built with the version composition work, as a field on the
  finding rather than a paired `info` finding, which every renderer would have had to pair.
- The missing-data gates were brought to one hint rule: `cve_policy` first, then `checkov_policy`, `release_gate`
  and `image_gate`. With that, and listing hint-less findings set aside as noise, nothing was open and the doc moved
  to design.
