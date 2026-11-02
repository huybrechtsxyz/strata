# Local tools as declarable integration types; a declared document wins over PATH

- Status: accepted
- Date: 2026-10-27
- Related: [ADR-0050](0050-one-offline-integration-readiness-check.md),
  [docs/work/integration-readiness.md](../work/integration-readiness.md) R1, D25-D28,
  [ADR-0021](0021-integration-layer.md) (integration layer)

## Context and Problem Statement

Several command-line tools strata runs had no registered integration type, so no Integration
document could declare them: `git` (clones, checkouts, audit pushes), `trivy` and `grype` (the CVE
gate) and `checkov` (the Checkov gate). They were found on PATH at the point of use:
`CveScannerIntegration()` picked Trivy, else Grype; `CheckovIntegration()` looked for `checkov`.
`git` and `checkov` were even listed as "v1 types not yet ported". A `type: git` document in a real
solution (`.v2-haven`, with `required: true` and a version) therefore did nothing, and a team could
not require a scanner, pin its version or point strata at a non-default binary.

The readiness check of ADR-0050 gives every registered local tool `spec.version`, `spec.required`
and `spec.command` handling for free, but only for tools that have a type.

## Considered Options

- **Keep PATH auto-detection only** - no change, but scanners and `git` stay outside readiness:
  no required flag, no version pin, no `integration check`.
- **A generic "tool" type** (`type: tool`, `command: trivy`) - one class for everything, but it
  cannot know each tool's version arguments, output format or which gate it serves.
- **Register each tool as its own type** (`git`, `trivy`, `grype`, `checkov`), each declaring its
  capability (`sources` or `scanner`) and the `cli` transport, bound by type the way stores and
  module types already are; PATH detection stays as the fallback when nothing is declared.

## Decision Outcome

Chosen: **register each tool as its own type**, because it reuses the existing registry and binding
rules and makes every local tool subject to ADR-0050's readiness check.

- `git`, `trivy`, `grype` and `checkov` are registered types. A document of one of them is checked
  like any local tool: on PATH, `spec.version`, `spec.required`, `spec.command`.
- **Declared wins, else today's behaviour.** A gate uses the enabled document of its tool type
  (the CVE gate: `trivy`, then `grype`; the Checkov gate: `checkov`); with none declared it keeps
  the PATH auto-detection. A declared scanner that is missing is never silently swapped for another
  one: the policy's `on_missing_data` applies, and the preflight has already reported it.
- Binding is by type through the existing auto-bind rule: several enabled documents of one type is
  the same usage error as for stores.
- No new capability and no new command. A `sources`-only document of an unregistered type remains
  a credential holder (remotes and assets read only its `authentication`), not an error.

### Consequences

- Good: a pipeline can require a scanner and pin its version; `strata integration list` and
  `check` cover the tools that previously had no type.
- Good: solutions that declare nothing behave exactly as before.
- Bad: two documents of the same tool type are now ambiguous where PATH detection never was; the
  fix is to disable one or name it explicitly.
- Bad: each new local tool strata runs needs a registered class to join readiness; nothing picks it
  up automatically.
