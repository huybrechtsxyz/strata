# `strata deploy run` — Real Plan Preview, Smoke Test, and Output Streaming

- Status: accepted
- Date: 2026-10-06
- Related: [ADR-0027](0027-strata-deploy-run.md) (the original `deploy run`
  orchestrator this extends — that ADR's CLI signature listed `--dry-run`
  without specifying its preview semantics within this command; this ADR
  fills that gap and adds `--smoke-test`/`--follow`. It does not reverse
  anything ADR-0027 decided), [docs/design/deploy-plan-preview.md](../design/deploy-plan-preview.md)
  (full evidence record, naming survey, and how it works today — this ADR
  records only the decision itself)

## Context and Problem Statement

A real CI pipeline (`cfg-int-deployment`, xyz-strata 2.0.0a6) mapped its
`mode: plan` stage to `strata deploy run --force --dry-run --verbose`,
expecting a real Terraform/Helm/Compose preview. It got none: `--dry-run`
short-circuited the whole step loop before `init`/`validate`/`plan` were
ever called — report-only, zero tool contact — exactly as its own help
text said, but not what any real consumer actually wanted from a "plan"
stage. Separately, subprocess output streaming (`line_callback`) was
accepted by the low-level transport but never wired to anything that
printed it — `--verbose` only ever raised the log level.

## Considered Options

- **Add a new `--plan-only` flag, leave `--dry-run` as-is.** Rejected —
  "plan-only" and "dry-run" read as near-synonyms; a caller would have no
  intuition for which does what.
- **A single value-taking flag** (`--dry-run=client|server`, kubectl's own
  precedent). Rejected — no existing value-taking flag convention
  elsewhere in this CLI to match; two independent boolean flags read more
  clearly for this command's existing style.
- **Redefine `--dry-run` to run a real preview, add `--smoke-test` for
  today's zero-contact behaviour** (chosen) — matches what real consumers
  already expect from "dry run" in every comparable tool (`terraform
  plan`, `rsync --dry-run`, `kubectl --dry-run=server`), and the reporting
  CI's own `mode: smoke` stage name maps to `--smoke-test` with zero
  translation needed. `--check`/`--noop` (Ansible/Puppet's own terms for
  the real-contact tier) were considered and rejected for the no-contact
  flag specifically because reusing either would mislead anyone familiar
  with those tools.
- **A missing plan-preview capability always degrades gracefully (warn +
  continue), regardless of mode.** Rejected as a blanket rule — correct
  for `--dry-run` (nothing real was going to happen anyway), wrong for a
  real apply (silently skipping `deploy()` would report success having
  never actually deployed the step). Decision: gate the graceful skip on
  `dry_run`; a real apply hard-fails instead.
- **Stream raw subprocess output unmodified.** Rejected after confirming
  some tools (`helm upgrade --dry-run`) echo their own rendered input
  verbatim, which can include a resolved secret value. Decision: redact
  every resolved secret value out of streamed lines centrally, in the
  orchestrator (where the values are actually known), before any
  consumer (console, a future NDJSON writer) ever sees a line.
- **Widen `--verbose` to also mean streaming.** Rejected — `--verbose` is
  a shared flag with an identical, log-level-only contract across every
  command; widening it just for this one would make the same flag mean
  different things elsewhere. A new flag, `--follow`/`-f`, matches the de
  facto industry verb for this (`tail -f`, `kubectl logs -f`, `docker
  logs -f`) instead.

## Decision Outcome

- `deploy run --smoke-test` *(new)*: zero tool contact, reports which
  steps would run — exactly `--dry-run`'s previous behaviour, renamed.
- `deploy run --dry-run` *(redefined)*: runs `init`/`validate`/`plan` for
  real (saved to `<step>.tfplan`, same convention a real apply uses) and
  stops before `deploy()`. A tool with no meaningful plan-preview support
  is a warning, not a hard failure, here only. Container-capable
  (Helm/Compose) steps get the equivalent treatment in `deploy_namespace()`.
- A real apply (`dry_run=False`) hard-fails instead if a plan preview
  isn't available — `plan()` is a required prerequisite there, not an
  optional preview.
- `deploy run --follow`/`-f` *(new)*: streams subprocess output live,
  tool-prefixed, console output only (silently inert with `--output
  json`, matching `--verbose`'s own precedent).
- Every resolved secret value is redacted out of streamed lines
  centrally in `deploy_run()`, before any `on_line` consumer sees them —
  covers Helm/Compose's `--set-string` secrets for free (same underlying
  resolved values). Not a complete guarantee: plaintext, same-line
  occurrences only (see the design doc's own documented limitations).
- `--smoke-test`/`--dry-run` are mutually exclusive.

## Consequences

- Good: `--dry-run` finally does what every comparable tool's "dry run"
  already means, and real CI pipelines already invoking `--dry-run` for a
  "plan" stage get the expected behaviour with zero change to their own
  invocation.
- Good: the orchestrator's `plan_or_warn()`/streaming plumbing is reused
  identically by Helm/Compose/GitOps — no tool-specific branching added
  to accommodate any of this.
- Bad: **consumer-visible breaking change** — anything that relied on
  `--dry-run`'s previous zero-contact meaning must switch to
  `--smoke-test` instead. Accepted given v2 is still alpha (2.0.0a6); a
  direct release note, not a deprecation cycle.
- Bad: secret redaction is a real mitigation, not a complete guarantee —
  base64/encoded secrets and values split across streamed lines are not
  caught; documented explicitly rather than implied as solved.
- Bad: `--dry-run` is no longer filesystem-read-only for a Helm/Compose
  step (it rewrites that step's rendered build artifact with resolved,
  non-secret values) — a hard requirement of previewing against a real
  file, not an oversight, but a real behaviour change worth knowing about
  for anything that diffs/caches `build run`'s output.
