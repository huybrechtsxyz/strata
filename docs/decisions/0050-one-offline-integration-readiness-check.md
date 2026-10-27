# One offline integration readiness check, enforced before build and deploy

- Status: accepted
- Date: 2026-10-27
- Related: [ADR-0051](0051-local-tools-as-declarable-integration-types.md),
  [docs/work/integration-readiness.md](../work/integration-readiness.md) D1-D24, D29-D31,
  [ADR-0021](0021-integration-layer.md) (integration layer)

## Context and Problem Statement

An Integration document carries three fields that promise behaviour: `spec.enabled`, `spec.version`
("checked at preflight") and `spec.required` ("required for platform operation"). Before this
decision strata honoured none of them consistently: `ensure_version()` had no caller, so a
`version: ">= 1.7"` was never enforced; `spec.required` was set in real documents (`config/`,
`.v2-cfg`, `.v2-haven`) but read by no code; a provisioner naming a disabled document passed
`strata validate` and ran at deploy; and `build run` checked nothing up front, so a missing tool
failed wherever it was first used. v1 answered "is this machine ready?" with
`strata tools status|check`, a separate command with its own rules.

Two different questions are involved. Whether an integration is usable *here* (type registered,
enabled, tool on PATH, installed version) needs nothing but the machine. Whether it is reachable
*now* (credentials resolve, the platform accepts them) needs a deployment and the network.

## Considered Options

- **Keep the checks where each tool is first used** - no new concept, but each caller (deploy
  preflight, `integration check`, a future build check) grows its own partial rules, and
  `spec.version`/`spec.required` stay unenforced.
- **Check readiness in `strata validate`** - early, but validate must run on any machine (a laptop
  without Terraform, a docs build); PATH and installed versions are properties of the machine, not
  of the documents.
- **One offline readiness check per Integration document, called by build, deploy and the
  `integration` commands**, with one severity rule; `strata validate` keeps only the checks that
  depend on the documents alone.

## Decision Outcome

Chosen: **one offline readiness check**, because the same answer then drives every caller and the
three promised fields finally mean something.

- `assess_integration()` returns a frozen record per integration: registered, enabled, local tool or
  not, command, available, required and installed version, `problems` with stable codes
  (`integration_disabled`, `integration_unavailable`, `integration_version_mismatch`, ...). It never
  resolves credentials or touches the network; the connection check stays a separate, explicit step
  of `strata integration check`.
- `spec.version` uses PEP 440 specifiers plus Terraform's `~>`; an unparseable constraint fails at
  load, and a version on an integration strata cannot read locally (networked transport) is a
  validate error rather than a silently ignored promise.
- One severity rule: a problem is an **error** when the integration is `required` or used by the run,
  a **warning** otherwise; disabled, unused documents are silent.
- `build run` runs the preflight before cleaning or rendering, and every `deploy` command runs it
  after the per-step check; a required integration that is not ready stops the run (exit 3).
  `strata integration list` is the stand-alone "is this machine ready?" check; `strata integration
  check NAME` reports local readiness before any connection check.
- `strata validate` does not run it. Validate does report the contradictions visible in the
  documents alone: a step's provisioner bound to a disabled integration (error) and a disabled
  integration marked `required: true` (warning).

### Consequences

- Good: `spec.enabled`, `spec.version` and `spec.required` behave as documented, in build and deploy
  alike, and a misconfigured machine fails before any work is done.
- Good: deploy preflight, `integration check` and `integration list` cannot disagree; a new caller
  reuses the record.
- Bad: reading a version runs each local tool once per run (one `--version` call per integration);
  `--smoke-test` skips it to keep its "no tool contact" contract.
- Bad: solutions that set `required: true` or `version:` without meaning it now fail a build that
  used to pass.
