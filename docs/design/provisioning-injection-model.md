# Provisioning Injection Model — Interface, Injection, Grant, Translation, Context

- Status: proposed — fully designed conceptually; zero implementation, no
  provisioner/build layer exists yet
- Last updated: 2026-09-24

## Overview

Records the current conceptual design for how a provisioner instance
receives values at build/deploy time — worked out in
[ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
(Interface/Injection/Grant/Translation) and
[ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) (Context).
None of it is implemented: no provisioner/build layer exists in v2 yet —
[ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) built
`ProvisionerModel`/`ProvisioningStepModel` as declarations only, not an
execution engine.

## Current Design

Five concepts (a sixth, Requirement, was considered and rejected — no kind
has a `references` field):

- **Interface** — a provisioner instance's real, introspected inputs (e.g.
  Terraform's `variables.tf`, parsed by a capability lookup: `terraform`
  always capable, `helm` conditionally (needs `values.schema.json`), others
  never). Build-time only — requires the provisioner's source to already be
  fetched.
- **Injection** — `Interface ∩ Environment` (or `needs ∩ Environment` when
  Interface can't be derived). A required-but-missing Interface variable is
  a hard build error, not a silent omission.
- **Grant** — secrets-only, invocation-scoped (`stages[]`), derived-by-
  default from stage kind (`plan` → deny, `apply`/`destroy` → allow), with a
  narrow allow/deny override for exceptions. Confirmed against v1's real
  `ResolvedValues.for_stage()`/`DeploymentStageModel.secrets`.
- **Translation** — a provisioner/topology-scoped alias table
  (`input_aliases: {canonical_key: local_name}`) bridging a platform's
  canonical variable name to a pre-existing, team-owned module's own naming.
  Must run *before* the `Interface ∩ Environment` intersection, or a name
  mismatch incorrectly triggers Injection's required-but-missing hard-fail.
- **Context** — the runtime object (v1: `ResolvedValues`) holding resolved
  variables/secrets/features plus `stage_outputs` from prior stages.
  `${step:step_name.output_key}` is the token that reads from it (not yet
  added to `VALUE_TOKEN_KINDS` — see
  [value-token-resolution.md](value-token-resolution.md)).

**Rejected**: Requirement (`spec.references` as a schema field) — both of
its v1 jobs (provisioner scoping, typo-catching) are better solved without
it: scoping is derived (Injection), typo-catching is a direct Phase 2
`Environment` cross-check.

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — the full Requirement/Interface/Injection/Grant/Value/Translation analysis
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, `${step:}` token
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — `ProvisionerModel`/`ProvisioningStepModel`, the declarations this design will eventually execute against

## Remaining Work / Open Questions

- None of Interface/Injection/Grant/Translation/Context is built. All wait
  on the provisioner/build layer, which doesn't exist yet.
- Provisioner capability lookup (`always`/`conditional`/`never` per
  `ProvisionerType`) — not built.
- Concrete shape of Context as a Pydantic model — not designed in detail,
  only sketched (mirrors v1's `ResolvedValues` dataclass).
- "Outputs declaration" (a ground truth to validate `${step:...}`'s key
  against, symmetric to Interface) — open, unscheduled.
- Exact schema for Grant's override (`grant.allow`/`grant.deny` on a stage)
  — conceptually decided, fields not named yet.

## Changelog

- 2026-09-24: Created, consolidating ADR-0002's and ADR-0006's scattered
  remaining-work items into one tracker.
