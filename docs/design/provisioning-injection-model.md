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
  fetched (confirmed sufficient: `sync_source()` itself does zero network
  I/O, `build_run()` already calls it before any tfvars are written — no
  `terraform init` needed at all, since `variable` blocks are pure static
  HCL with no dynamic generation, unlike reading an actual applied
  `output` value). A parse failure on a capability-says-yes provisioner is
  its own decision — see "Interface parse-failure policy" below.
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

## Interface parse-failure policy

Confirmed against v1's real `parse_variables_tf()`: it silently skips a
`.tf` file that fails to parse (`except Exception: continue`, logged at
debug level only). That is a real reliability gap, not a hypothetical one
— a skipped file's variable declarations become invisible, which makes
Injection produce **false "undeclared"** errors for variables that
genuinely exist, for no reason the platform author can see without
digging into debug logs.

**Decided: default to a hard build error, not a silent skip**, when a
capability-says-yes provisioner's source fails to parse. This matches this
doc's own established "required logic" precedent — Injection's
required-but-missing rule (above) is *always* a hard fail with no toggle,
specifically because a silent gap here is worse than a loud one. The same
reasoning applies symmetrically: a parse failure could just as easily be
masking a real, Terraform-rejected syntax error as it could a harmless
`python-hcl2` library-lag limitation (the library not yet supporting a
newer HCL feature) — defaulting to loud is the only choice that can't
silently hide the first case to accommodate the second.

**But an unconditional block has a real cost the "required variable
missing" case doesn't**: a genuinely valid `.tf` file the parsing library
doesn't yet support would block a team indefinitely, with no workaround
except waiting for an upstream library release — unlike a missing
variable (always fixable by the platform author, immediately, in their
own document), a parser gap is not fixable by the person hitting it.
That asymmetry is why this case, unlike Injection's required-but-missing
rule, earns an escape hatch rather than being unconditional.

**The escape hatch reuses `needs:` — no new schema field.** `needs:`
already exists for the structurally-never-capable case (Injection's
`needs ∩ Environment` fallback). Extending its meaning to *also* cover an
empirically-failed parse on a capability-says-yes provisioner reuses a
mechanism already designed and named, rather than inventing a second,
parallel opt-out: if parsing `variables.tf` fails and the provisioner's
`needs:` is non-empty, treat that instance as if capability were `never`
for this build (`needs ∩ Environment`, same formula, same narrowing);
if parsing fails and `needs:` is unset or empty, hard-fail the build with
a message naming the parse error and telling the author to either fix the
`.tf` source or author `needs:` to explicitly accept the narrower check.

**Why the fallback must require a non-empty `needs:`, not just react to
the failure itself**: falling back to `needs ∩ Environment` when `needs:`
was never authored would silently narrow injection to the **empty set**
(`∅ ∩ Environment`) — injecting *nothing* into Terraform, a far worse
silent failure than v1's original one-file-skip bug, which only hides
*some* declarations, not all of them. Requiring `needs:` to be non-empty
first means accepting the degraded check is a deliberate, visible action
the platform author takes (writing out what this provisioner actually
needs), not an accidental side effect of a parser limitation they may not
even have noticed.

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
  `ProvisionerType`) — not built. Its `terraform` = always-capable branch
  needs "Interface parse-failure policy" above (hard-fail by default,
  `needs:` as the deliberate opt-out) built alongside it, not as a
  follow-on — a capability lookup that can silently produce zero
  injection on a parser hiccup would be worse than not having the check
  at all.
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
- 2026-09-30: **Designed the Interface parse-failure policy**, per direct
  request, prompted by a real question about gap #15's
  (`docs/design/gap_fit_v1.md`) `variables.tf` check: confirmed against
  v1's real `parse_variables_tf()` that it silently skips an unparseable
  `.tf` file, a real reliability gap (invisible declarations → false
  "undeclared" errors). Also confirmed, against v2's real `build_run()`/
  `sync_source()` code, that this check needs nothing beyond the git
  clone/pull `sync_source()`'s caller already does — no `terraform init`,
  since `variable` blocks are static HCL with no dynamic generation.
  Decided: default to a hard build error (matching Injection's own
  required-but-missing precedent — a silent gap is worse than a loud one),
  with `needs:` reused as the deliberate escape hatch (no new schema
  field) — but only when `needs:` is non-empty, since falling back on an
  unauthored empty `needs:` would silently zero out injection entirely, a
  worse failure than the one being escaped. Design only — nothing
  implemented yet.
