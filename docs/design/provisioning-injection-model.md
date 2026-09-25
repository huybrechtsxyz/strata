# Provisioning Injection Model — Interface, Injection, Grant, Translation, Context

- Status: proposed — fully designed conceptually; Context's build-time analog
  (`ValueResolution`) is implemented and threaded through the whole
  `build_run()` pipeline today, but Context itself (the deploy-time,
  multi-stage, `stage_outputs`-accumulating object ADR-0006 actually
  describes) is still unbuilt — blocked on `deploy run`, which does not
  exist yet. Interface/Injection/Grant/Translation remain fully unbuilt too.
- Last updated: 2026-09-25

## Overview

Records the current conceptual design for how a provisioner instance
receives values at build/deploy time — worked out in
[ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
(Interface/Injection/Grant/Translation) and
[ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) (Context).
ADR-0006's own "proposed" status line was written when *no* provisioner/build
layer existed at all. That premise is now half-false: `build_run()`
(ADR-0022) exists and is end-to-end tested, and it already threads a
Context-shaped object (`ValueResolution`) through every provisioner and
workload module in one invocation. What still doesn't exist is `deploy run`
itself — and Context's actual point (accumulating *real, applied*
provisioner outputs across multiple deploy **stages**, feeding
`${step:step_name.output_key}`) has no stages to accumulate from yet. See
"Build-time vs. deploy-time Context" below — conflating the two would
overstate how much of ADR-0006 is actually done.

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

## Build-time vs. deploy-time Context — what exists today, grounded against `build_run()`

Asked directly (paraphrased): *"when strata starts, build a Context; every
step can add to it; a stage's outputs update it; vars/secrets/features live
in it; Terraform output gets added, so do other provisioners'; only cleared
at the end of the CLI invocation, which stops by default on failure."* That
is ADR-0006's Context, correctly restated — and checking it against the
now-real `build_run()` code confirms the shape but splits it into two
things that were one sentence above:

- **Build-time (exists today, under a narrower name).**
  `build_controller.build_run()` builds exactly one `ValueResolution`
  (`resolved`) per invocation — `resolve_values()`, called once, at the
  top — and threads the *same instance* through every provisioner's
  `integration.prepare()` and every namespace's `prepare_namespace()` for
  the rest of that one CLI invocation. It is never rebuilt mid-run, never
  persisted to disk, and goes out of scope (garbage-collected) the moment
  the process exits — exactly the lifetime described above, already true,
  already tested. What it does **not** do yet: get *added to* as a step
  runs. `build_run()` only renders (ADR-0022 D4 — never `plan`/`apply`/
  `destroy`), so there is no real provisioner *output* (an applied
  Terraform output, say) for a later step to consume within `build_run()`
  — rendering doesn't produce runtime facts, it produces files. This is why
  `TerraformIntegration.default_output()` currently does `del resolved` and
  never writes `resolved.values` anywhere (found while investigating the
  `OutputProfileModel` revisit, see below) — not a missing Context, `resolved`
  is right there; a separate, narrower, already-actionable gap in one method.
- **Deploy-time (ADR-0006's actual point, still fully unbuilt).** The real
  motivating cases — DNS's `output_key`, `HealthCheckModel.output_key`,
  Ansible's `ip_output_key` — all read a *previous stage's real applied
  output* (an actual VM IP, an actual Terraform output value), which only
  exists after `apply` runs. That requires `deploy run` to exist at all
  (stages executed in sequence, each contributing real outputs back into
  the same Context instance for the next stage to read via
  `${step:step_name.output_key}`) — and `deploy run` is not designed, let
  alone built. Confirms the lifetime/clearing question the same way: one
  Context per `deploy run` invocation, mutated stage-by-stage, discarded at
  process exit; a stage failure stops the whole invocation by default,
  matching every command's existing fail-fast convention
  (`command_run()`'s `except StrataError` propagates immediately, no
  partial-continue exists anywhere in v2 today). **Still genuinely open**
  (not decided by this note, flagged for whoever designs `deploy run`):
  whether a partially-completed multi-stage deploy can ever be *resumed*
  across separate CLI invocations (would need Context, or enough of it,
  persisted to disk — `.strata/`-scoped, matching `layout.py`'s existing
  runtime-state convention) — v1 had per-stage locking
  (`DeploymentStageModel`'s lock fields) that may be adjacent evidence, not
  yet checked for this specific question.

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — the full Requirement/Interface/Injection/Grant/Value/Translation analysis
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, `${step:}` token
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — `ProvisionerModel`/`ProvisioningStepModel`, the declarations this design will eventually execute against

## Remaining Work / Open Questions

- **Deploy-time Context (ADR-0006's real target) waits on `deploy run`**,
  which does not exist — no stages, no sequencing, nothing to accumulate
  outputs from. Interface/Injection/Grant/Translation are unbuilt for the
  same reason: all four presuppose a provisioner is actually being
  planned/applied, not just rendered.
- **Narrow, separately-actionable, ready now:**
  `TerraformIntegration.default_output()` discards `resolved.values`
  entirely (`del resolved`) — the build-time `ValueResolution` Context
  analog already exists and is already threaded through `build_run()`; it
  simply isn't written to a `.tfvars.json` file. Fixing this does not
  require deploy-time Context at all — tracked as its own item in
  [build-command.md](build-command.md), not blocked on anything above.
- Provisioner capability lookup (`always`/`conditional`/`never` per
  `ProvisionerType`) — not built.
- Concrete shape of Context as a Pydantic model — not designed in detail,
  only sketched (mirrors v1's `ResolvedValues` dataclass). The build-time
  half now has a real, working analog (`ValueResolution`,
  `strata/integrations/resolved_context.py`) worth checking directly before
  designing Context's Pydantic shape from scratch — they may be the same
  object with a `stage_outputs` field added, not two separate types.
- "Outputs declaration" (a ground truth to validate `${step:...}`'s key
  against, symmetric to Interface) — open, unscheduled.
- Exact schema for Grant's override (`grant.allow`/`grant.deny` on a stage)
  — conceptually decided, fields not named yet.
- Resumability of a partially-completed multi-stage deploy across separate
  CLI invocations — raised while confirming Context's lifetime (see above),
  genuinely undecided; check v1's per-stage locking fields for adjacent
  evidence before designing.

## Changelog

- 2026-09-24: Created, consolidating ADR-0002's and ADR-0006's scattered
  remaining-work items into one tracker.
- 2026-09-25: Unblocked partially — `build_run()` (ADR-0022) now exists and
  already threads a Context-shaped object (`ValueResolution`) through one
  full build invocation, confirming the lifetime/accumulation shape ADR-0006
  describes (one object per CLI invocation, mutated in place, discarded at
  exit, fail-fast by default) for the build-time half. Split "Context" into
  build-time (exists, narrower, already working) vs. deploy-time (ADR-0006's
  actual target, still fully blocked on `deploy run` not existing) so the
  doc doesn't overstate progress. Found and flagged a separate, narrower,
  ready-to-implement gap while grounding this: `TerraformIntegration
  .default_output()` silently discards `resolved.values` (`del resolved`)
  instead of writing it — not a missing Context, a one-method fix, tracked
  in [build-command.md](build-command.md) instead of here. No code changed
  in this update.
