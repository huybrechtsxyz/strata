# Cross-deployment dependency gating (layered tenant/landscape/zone hierarchies)

- Status: proposed
- Date: 2026-07-28

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

A partner team (not core maintainers) runs a layered deployment hierarchy — landscape →
zone → zone/tenant → zone/tenant/environment — where **each layer is a separate
`kind: deployment` YAML file**, not stages within one file. They asked whether strata can
prevent deploying a lower layer before an upper layer has successfully deployed (e.g.
refuse to run the zone deployment until the landscape deployment has succeeded).

Confirmed: **no such mechanism exists today.**

- `stages[].depends_on` only orders stages *within a single* deployment file. It has no
  way to reach across separate deployment files.
- The design-draft `spec.inputs.from` sketched in `docs/guides/at-scale.md` (marked
  "Design draft — not yet implemented") is unimplemented, and is a different concern
  anyway — it is about injecting upstream Terraform *output values* into a downstream
  deployment, not about gating whether the downstream deployment is allowed to run at
  all.

This ADR records the recommended direction only. It does not implement anything.

## Decision Drivers

- **No human in the loop.** This is a binary, disk/state-checkable precondition — "did
  the upstream deployment succeed?" — unlike ADR-0057's gate framework, which is built
  for human hand-offs (approve/reject, cost review, security review, CAB, etc.).
  Routing a precondition check through that machinery would mean actively suppressing
  most of what it exists to provide.
- **Must work across separate machines/CI runtimes.** Landscape and zone deployments
  commonly run from separate ephemeral CI jobs/containers with no shared disk. This
  rules out relying purely on local `.strata/logs/` or any other host-local state as
  the sole signal.
- **Reuse existing mechanisms wherever possible.** Strata already has more than one
  building block that could serve as the "did it succeed" signal (deployment manifests,
  live Terraform backend state). Prefer wiring those together over inventing new
  infrastructure.

## Considered Options

### Option 1 — DIY lifecycle script today (interim workaround)

Hook a lifecycle script at the `deploy_run_before` phase — it fires once, before any
stage touches infrastructure, making it the earliest safe interception point. The
script calls a status-check command against the upstream deployment and aborts the
downstream deploy on failure.

- Pro: works today, no code changes to strata.
- Pro: `deploy_run_before` is confirmed to be the correct, earliest hook point.
- Con: fragile at fleet scale — hardcoded paths per script instance, no schema
  validation of what "upstream" even means, easy to get subtly wrong (e.g. checking the
  wrong signal — see rejected candidate signals below).
- Con: not discoverable or enforced — nothing stops a deployment file from omitting the
  hook.

**Rejected as the *only* answer**, but **recommended as the documented interim recipe**
until a first-class fix ships. Of the existing CLI-level signals evaluated as the check
inside this script, `strata deploy status` was found not to exist as a registered
command (a naming error carried over from stale docs), `strata deploy health` silently
passes with `no_checks_defined` when no health checks are configured (a footgun for an
unconfigured downstream layer), and `strata deploy history`'s per-execution `success`
boolean was the most reliable of the CLI-surfaced signals — but it requires CI to
persist/share `.strata/logs/` across ephemeral checkouts between the two layers'
pipeline jobs, which reintroduces the cross-machine problem this ADR is trying to avoid.

### Option 2 — New `spec.requires: Optional[List[str]]` field on `DeploymentModel` (RECOMMENDED)

Add a field that lists the deployment files a given deployment file depends on. This is
a hard precondition check — no human hand-off — evaluated pre-flight in `deploy run`
(before Phase 1 PLAN) and optionally via `strata validate --deep` for CI gating without
attempting a full deploy.

The backing signal for "did the upstream deployment succeed" prefers the deployment
manifest's `spec.status` (`success | partial | failed`, from ADR-0021) via
`manifest: { type: gitops, push_manifest: true }` — already implemented, and genuinely
remote via git push/pull, so no new strata-side remote backend is required — with
`strata env status -f <file>` (already implemented — queries the live Terraform backend
directly, e.g. azurerm/S3/Terraform Cloud, and is inherently cross-machine) as a
live-state fallback/sanity check when the manifest is missing or stale.

- Pro: matches the shape of the actual problem — a deployment-file-layering concern,
  expressed as a field on the deployment file itself.
- Pro: reuses two already-implemented mechanisms (gitops manifest push, `env status`)
  instead of inventing a new remote store.
- Pro: no human decision, no `WorkItem`, no exit code 5 — a plain hard error is the
  correct failure mode.
- Con: needs an explicit `git pull` of the state-repo on the downstream side before the
  manifest can be read (no `pull_from_remote()` exists yet — see Detailed Design).

**This is the winning option.**

### Option 3 — New gate `type: dependency` on the existing ADR-0057 gate/WorkItem framework

Extend ADR-0057's gate framework (`EnvironmentSpecModel.spec.gates`, `WorkItem`, exit
code 5, `--resume`) with a new gate type that checks upstream deployment status instead
of waiting for a human decision.

- Con: **structural mismatch.** ADR-0057 gates are declared on
  `EnvironmentSpecModel.spec.gates` (environment-scoped), but this is fundamentally a
  deployment-file-layering concern, not an environment concern.
  Con: ADR-0057's whole machinery (`WorkItem`, exit code 5, `--resume`) exists for human
  decisions. A binary, disk-checkable precondition has no one to "approve" — building
  this as a gate type would mean actively suppressing most of the framework's value
  (resolver identity, SIEM notification of a pending hand-off, expiry, etc. all become
  meaningless when nothing is actually pending human action).

**Rejected.** (This was Danny's initial proposal in the discussion session, revised
after Linus pointed out the mismatch above.)

### Option 4 — Generalize the drafted `spec.inputs.from` to also serve as a gate

`spec.inputs.from` (drafted, unimplemented) is a data-flow concern — injecting upstream
Terraform output values into a downstream deployment. Generalize it to also block
execution when the upstream hasn't succeeded.

- Con: **out of scope / conflation.** `inputs.from` answers "what values does the
  downstream deployment need from the upstream," a fundamentally different question
  from "is the downstream deployment even allowed to run." Overloading a still-
  unimplemented, differently-scoped field with a second responsibility makes both
  harder to reason about.

**Rejected**, kept explicitly out of scope. If/when `spec.inputs.from` is built, it may
internally reuse the same "resolve upstream deployment status" helper that
`spec.requires` introduces — but the YAML surfaces stay distinct.

## Decision Outcome

**Option 2 — new `spec.requires: Optional[List[str]]` field on `DeploymentModel`.**

The cross-machine verification round ranked the candidate backing-signal sources from
best to weakest:

1. **`strata env status -f <upstream>`** — queries the live Terraform backend directly
   (azurerm/S3/Terraform Cloud/etc.). No shared storage needed at all; this is the best
   ground-truth signal because it asks the actual infrastructure state, not a record of
   a past run.
2. **Deployment manifest with `type: gitops` + `push_manifest: true`** — genuinely
   remote via git (a real commit+push of the manifest, carrying `spec.status`, happens
   after every deploy). Matches the `spec.requires` design well, but needs an explicit
   `git pull` of the state-repo on the downstream side before the manifest can be read.
3. **Shared `deploy_log_path` on a mounted network share** — works, but has zero
   strata-side remote mechanics. Confirmed: `deploy_log_path`
   (`ConfigurationService.get_deploy_log_path()`) is pure filesystem path resolution —
   no S3/Blob/GCS transport, unlike locks (ADR-0007) or work items (ADR-0057), which do
   have pluggable remote backends. All correctness here is on the operator.
4. **Webhook/ndjson audit sinks** — not usable standalone today; there is no query-side
   API to ask "what was the last status."

### Consequences

- Good: `spec.requires` is a small, purpose-built field rather than an overloaded reuse
  of an unrelated mechanism (gates) or an unrelated concern (`inputs.from`).
- Good: the two preferred backing signals (`env status`, gitops manifest push) are
  already implemented — no new remote backend needs to be built for this to work
  cross-machine.
- Good: a hard error with a clear message is the right UX for a precondition with no
  human in the loop — no new exit code, no new CLI verbs to resolve a pending item.
- Neutral: the interim DIY lifecycle-script recipe remains valid and documented for
  teams who need an unblock before `spec.requires` ships.
- Bad: the gitops-manifest path requires the operator to arrange a `git pull` of the
  state-repo downstream before the check runs, since no `pull_from_remote()` exists yet.
- Bad (accepted, tracked separately): this ADR only covers the forward direction
  (don't deploy downstream before upstream succeeds). The reverse direction (don't
  destroy an upstream layer while downstream layers still depend on it) is explicitly
  out of scope — see below.

### Explicitly out of scope

The **reverse direction** — preventing `deploy destroy` on an upper layer (e.g. a zone)
while lower-layer deployments (tenants) still depend on it — is **not** addressed by
this ADR. It requires "who depends on me" discovery, which nothing today tracks; it is
adjacent to ADR-0038 Gap 3 (fleet-level visibility), which is not yet built. This is
flagged as a candidate for a future ADR, not solved here.

## Detailed Design

- `DeploymentSpecModel.requires: Optional[List[str]]` — a list of deployment file paths
  this deployment depends on. Supports `@repo_name/path.yaml` cross-repo references
  (the existing file-reference notation used elsewhere in strata YAML). Validated for:
  - path resolution (the referenced file must exist / resolve via the repo map),
  - no self-reference (a deployment file cannot list itself in `requires`).
- **Pre-flight check** runs before Phase 1 PLAN in `deploy run`: for each path in
  `requires`, resolve the latest deployment manifest for that upstream deployment
  (preferring the gitops-synced manifest) and confirm `spec.status == "success"`. If the
  manifest is missing or stale, fall back to a `strata env status` reachability check
  against the upstream's backend. Unmet preconditions produce a **hard error** (not an
  exit-5 hand-off) with a clear message naming which upstream deployment is
  missing/failed and what command to run to investigate (e.g. re-run its deploy, or
  check `strata env status -f <upstream>`).
- Also wire the same check into `strata validate --deep`, so CI can verify the
  precondition without attempting a full deploy.
- Rough test count estimate (~8–10 tests): field validation (path resolves correctly;
  self-reference is rejected); manifest-missing produces a clear error; manifest
  `status == failed` blocks; manifest `status == partial` blocks; manifest
  `status == success` passes; `env status` fallback path is exercised when the manifest
  is missing/stale; pre-flight check is wired in before Phase 1 PLAN (not after); and
  `strata validate --deep` surfaces the same error as `deploy run`.

## Implementation Phases

### Phase 1 — `spec.requires` field and pre-flight hard-error check

- `DeploymentSpecModel.requires: Optional[List[str]]` field, path resolution and
  self-reference validation.
- Pre-flight hard-error check in `deploy run`, using the existing gitops manifest
  `spec.status` with `strata env status` as the fallback.
- Ship the DIY lifecycle-script recipe (Option 1) as an interim documented pattern in
  the meantime — this does not block Phase 1 and can land independently, sooner.

### Phase 2 — `strata validate --deep` integration

- Wire the same precondition check into `validate --deep` so CI pipelines can confirm
  the precondition is met without running a full deploy.

### Phase 3 (future, separate ADR) — reverse-direction safe-destroy checks

- Preventing `deploy destroy` on an upper layer while downstream layers still depend on
  it. Dependent on ADR-0038 Gap 3 (fleet-level visibility) landing first, since that is
  the "who depends on me" discovery this would need.

## References

- [ADR-0003: Layered architecture](0003-layered-architecture.md) — the layering
  precedent this ADR's dependency direction follows.
- [ADR-0007: Deployment state locking](0007-deployment-state-locking.md) — precedent
  for a pluggable remote lock-backend pattern, contrasted with `deploy_log_path`'s
  purely local filesystem resolution.
- [ADR-0011: Promotion strategies for version progression across environments](0011-promotion-strategies-for-version-progression.md) —
  the existing layered-environment/ring precedent this hierarchy extends.
- [ADR-0021: Deployment Manifests as First-Class Build Artifacts](0021-deployment-manifests-as-first-class-build-artifacts.md) —
  source of the `spec.status` field this ADR's precondition check reads.
- [ADR-0038: Multi-tenant fleet management patterns and gaps](0038-multi-tenant-fleet-management-patterns.md) —
  Gap 3 (fleet-level visibility) and Gap 5 (layer consistency) are related gaps; Gap 3
  is the prerequisite for the out-of-scope reverse-direction follow-up.
- [ADR-0057: Deployment workflow orchestration — work items and hand-off gates](0057-deployment-workflow-orchestration.md) —
  considered and rejected as the delivery mechanism (Option 3), but its core principle
  — one general mechanism instead of many one-offs — is exactly what informed rejecting
  Option 4 (overloading `inputs.from`) in favor of a narrow, dedicated `spec.requires`
  field.
