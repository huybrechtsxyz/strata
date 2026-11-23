# Verify checks answer pass, warn, fail or unknown, and react as the gates do

- Status: accepted
- Date: 2026-11-22
- Related: [ADR-0066](0066-post-deploy-verification-is-a-deploy-phase.md) (verification is a phase of the deploy),
  [security-gates.md](../design/security-gates.md) (the gates' `enforcement` and `on_missing_data`),
  [post-deploy-verification.md](../design/post-deploy-verification.md) (the design)

## Context and Problem Statement

ADR-0066 makes post-deploy checks a phase of the deploy. Each check has to give an answer, and the run has to do
something with it. Two needs come up as soon as a check is real: running a new check in "report, don't block" mode
for its first weeks, and telling "the workload is broken" apart from "the check itself could not run" (its script
is missing, or it timed out). strata's pre-deploy gates met both needs already, with `enforcement: deny | warn`
and `on_missing_data`.

What does a check answer, and what does the run do with it?

## Considered Options

- **Pass or fail, a failure always fails the run**, like a Kubernetes probe.
- **The gates' vocabulary.** A check answers pass, warn, fail or unknown, as a Monitoring Plugins (Nagios,
  Icinga) check does. The block's `enforcement: deny | warn` decides what a failure does, and `on_unknown: block |
  warn | skip` what an unknown does, as `on_missing_data` does for a gate.
- **A vocabulary of its own** for verification.

## Decision Outcome

Chosen: **the gates' vocabulary**, because it already covers both needs, and a user who knows `cve_policy` reads a
`verify` block without learning anything new.

| Answer | What the run does |
| ------ | ----------------- |
| pass | an info line |
| warn | a warning, never a failure: degraded, but up |
| fail | `enforcement: deny` (default): an error, the run fails; `warn`: a warning |
| unknown | `on_unknown: block` (default): an error; `warn`: a warning; `skip`: an info line |

- **The finding codes** are `verify_check_failed`, `verify_check_unknown` and `verify_check_warning`. Each error
  carries a hint: fix the workload, or use `enforcement: warn` / `on_unknown: warn` while a check is introduced.
- **A timeout, and a check that cannot start, are unknown**: the check did not answer, so `on_unknown` applies. A
  timeout is retried; a check that cannot start is not, since retrying will not make its executable appear.
- **A warn ends the attempts at once**, as in Monitoring Plugins: a degraded answer is an answer.
- **The words are shared, the record is not.** A check's answer is a check result in the manifest, not a
  `policy_results` entry: a gate judges documents before a change, a check judges the running deployment after it.
- **How a script's exit code becomes an answer** is the check's `exit_codes`: `binary` by default (0 passes,
  anything else fails, which is what `curl -f`, `test` and most scripts already do), or `monitoring` for the four
  Monitoring Plugins codes (0 pass, 1 warn, 2 fail, 3 unknown).

The pass-or-fail option cannot say "report, don't block" or "the check is broken", the two things the gates needed
and solved. A vocabulary of its own would make users learn a second set of words for the same three decisions.

### Consequences

- Good, because a new check can run with `enforcement: warn` until it has proven itself, then be switched to
  `deny`, without changing the check.
- Good, because a broken check (missing executable, undeclared token, too short a timeout) is reported as unknown,
  not as a broken workload, and `on_unknown` decides whether that blocks.
- Bad, because only a script with `exit_codes: monitoring` can answer warn or unknown on its own. A `binary` script
  can only pass or fail, apart from a timeout.
