# Cross-Run Step-Output Persistence — A New Pluggable Key/Value Capability

- Status: accepted
- Date: 2026-10-12
- Related: [ADR-0021](0021-integration-layer.md) (D2's registry/auto-bind
  mechanism, D9's closed-capability-vocabulary rule — both reused here
  unchanged, not reopened), [ADR-0022](0022-strata-build-run.md) (D2's
  named-binding-wins-else-auto-bind duality, mirrored exactly by the new
  `output_store`/`output_store_integration` fields)
- See also: [docs/archive/cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md)
  (full evidence record, brainstorming, and build progress — this ADR
  records only the decisions themselves), [docs/work/key-value-store-integration.md](../work/key-value-store-integration.md)
  (the capability's own backend-by-backend design), [docs/work/output-store-lock-audit.md](../work/output-store-lock-audit.md)
  (deferred follow-on: audit-trail visibility for D2's optimistic writes)

## Context and Problem Statement

`${output:step.key}` only ever resolved when the producing step ran *in
the same `deploy run` invocation* as the step referencing it —
`deploy_controller.py`'s in-memory `step_outputs` dict is rebuilt fresh
every invocation. That breaks down for a real, common deployment shape:
an infra step and a later config/apps step, each run from **separate
pipelines** (separate CI jobs, separate scopes/stages, possibly days
apart), where the later step still needs the first one's output (e.g. a
VM's IP for a firewall rule). The dependency tree already exists and is
already walked correctly (`depends_on`, transitive); the gap was purely
**data availability across separate invocations**, not the dependency
model itself.

Persisting this inside `build_path` or `.strata/` (both considered)
cannot solve it: `build run`'s default `clean=True` wipes `build_path`
before every render, and a real multi-pipeline CI setup may check out a
fully independent, ephemeral workspace per pipeline with no shared
filesystem at all. The data has to live somewhere durable and shared
*outside* any single pipeline's own disposable environment — the
backend for which is a deployment/environment-specific choice, not
something strata can hardcode.

Originally sketched against a recalled "v1 precedent" (a lock-file
system similar to Terraform's own remote state + locking). Checked
directly against real v1 source before finalizing this decision: no such
precedent exists anywhere in v1 (zero matches for `lock`, `remote_state`,
or any cross-run output concept). This design is original to v2.

## Decision

### D1 — A new capability (`KeyValueStoreIntegration`), not a reuse of `StoreIntegration`

`StoreIntegration` (`resolve(key) -> str`) is a single-value resolver
with no version/compare-and-swap concept at all — a real shape mismatch
for "read/write a small structured record, possibly contended," not a
style preference. A new capability, `Capability.KEY_VALUE` /
`KeyValueStoreIntegration` (`get(key) -> KeyValueEntry | None`,
`put(key, value, if_version=None) -> str`), is added instead, following
ADR-0021 D9's existing closed-capability-vocabulary rule exactly — no
new dispatch mechanism invented. Named for the **capability**
(`key_value`), not this feature's own field name (`output_store`),
since it already has more than one planned consumer (a future lock
primitive, [docs/work/key-value-store-integration.md](../work/key-value-store-integration.md)'s
"Validated reuse" section) — a future feature reusing the same
capability stays governed by the same allow-list (D4) rather than
needing its own.

### D2 — Optimistic compare-and-swap, not a pessimistic lock/unlock pair

Concurrency safety is a version/ETag returned from `get()` and
optionally required on `put()`, never a separate acquire/release lock.
No lock to abandon if a process crashes mid-update — just a
read-modify-write retry loop on a version conflict
(`KeyValueConflictError`). This maps onto real, native primitives in
every backend genuinely capable of it (Consul's `ModifyIndex`, etcd's
`mod_revision`, ETag-conditional-write for the cloud SDKs) rather than
strata inventing its own distributed-locking protocol. Real contention
is rare by construction — dependent steps are sequential and never race
with each other; this only matters when two *unrelated* steps in two
parallel pipelines happen to write the same deployment's record at
nearly the same instant.

Not every backend can honor this uniformly: a new `SUPPORTS_CAS:
ClassVar[bool]` (default `True`) lets a backend structurally incapable
of full CAS (Azure Key Vault has no conditional-write primitive on
`set_secret()` at all; the planned file backend can only do create-only,
not update-only-if-unchanged) declare so — checked both at validate time
(no credentials touched) and defensively at resolve time, so picking
such a backend for this feature is a loud, immediate, pre-flight error
instead of a silently-never-persisted record discovered only after
relying on it in production (found via code review, 2026-10-12, against
the Key Vault case specifically).

### D3 — One record per deployment; staleness is a hard pre-flight failure, never a silent warn-and-use

A single small JSON document per deployment (not one per step, not one
per workspace) holds every step's outputs collected so far, keyed
internally by step name, each entry carrying a `collected_at` timestamp.
Freshness is a deliberate, visible republish action (re-running the step
that produces it), never an automatic background refresh — reading an
entry older than a configurable trust window (`WorkspaceSpecModel.
output_store_max_age` / `deploy run --output-max-age`, no enforcement
when unset) is a hard failure, with **no warn-and-use-anyway mode, not
even as an opt-in**. A silently stale value feeding, say, a firewall
rule is exactly the class of quiet-wrong-value bug this system actively
removes elsewhere (ADR-0031's own tfvars-rewrite rule is the same
philosophy applied to a different layer) — a step that cannot get a
trustworthy input has no well-defined way to proceed regardless of how
"close" the stale value might be.

The read path is deliberately asymmetric, not a single pass/fail: a
record that was simply never written is forgiving (the referencing step
may not even use it; the existing deep token-resolution error still
fires later if it actually does) — only a record that exists but is
untrustworthy (stale, or the backend itself unreachable) stops the
pipeline immediately, before that step's own tool ever runs.

### D4 — A sensitive output is never written to this store, in any backend, under any condition

Same non-negotiable rule `strata.auto.tfvars.json`'s own sentinel
mechanism already enforces elsewhere in this system (ADR-0031): this
store is for ordinary data (a VM's IP, a resource ID), never a secret.
`collect_step_outputs()`'s existing `sensitive_keys` return value is the
filter — a sensitive key is excluded before the write ever happens, not
redacted after. A `${output:...}` reference to a sensitive output that
did not run in this same invocation is therefore always a hard,
unconditional resolution error — a case this store is not designed to
solve, not a gap in it.

### D5 — A platform-wide allow-list, same shape as the existing store allow-lists, shipped with the capability rather than retrofitted later

`ConfigurationSecurityModel.allowed_key_value_stores` (mirrors the
existing `allowed_secret_stores`/`allowed_variable_stores`/
`allowed_feature_stores`) restricts which backend **types** a workspace
may configure, enforced at validate time against the backend's actually-
resolved type — resolved through `output_store_integration` when set
(the named document's own `spec.type`), never through the separately
and independently declared `output_store` field, even when both are set
and disagree (a real governance-bypass bug found via code review,
2026-10-12: checking the wrong field let a disallowed backend silently
pass validation while a different, unapproved one actually ran at
deploy time — fixed to match the resolver's own precedence exactly).

Shipped alongside the capability itself rather than deferred, departing
from this repo's usual "minimal slice" default: the three existing
allow-lists were built *alongside* their own store capabilities, not
retrofitted after real `workspace.yaml` files already used them
unguarded — retrofitting a restriction later would be a breaking change
for anyone already relying on `output_store:` unguarded, while shipping
`None` (unrestricted) today costs nothing. Without this, an org that
already restricts, say, `consul` as an approved *secret* store for
compliance reasons could have a workspace author silently point
`output_store: consul` at the same physical cluster for "ordinary" data,
with zero policy review at the infrastructure level — the restriction
they believed they'd enforced would not actually hold.

## Consequences

- Good: a real, common multi-pipeline deployment shape (infra step and a
  later, separately-triggered apps step) now resolves `${output:...}`
  correctly, with the limitation that motivated this ADR (in-memory,
  single-invocation-only resolution) made explicit and bounded rather
  than silently worked around.
- Good: the concurrency model has no lock to leak or abandon on a
  crashed process, and degrades to a clear, named error
  (`SystemError`/`KeyValueConflictError`) rather than data corruption
  under real contention.
- Good: a sensitive value and a stale value are both structurally
  prevented from reaching a dependent step silently — matching this
  system's existing zero-tolerance posture for quiet-wrong-value bugs
  elsewhere (ADR-0031), not a weaker standard for this new capability.
- Good: the governance allow-list and `SUPPORTS_CAS` gate both run at
  validate time, before any credentials are touched or any deploy
  begins — a misconfigured backend is a `strata validate` failure, not
  a discovery made mid-deploy or (worse) never discovered at all.
- Bad: cross-run `${output:...}` resolution now depends on an
  operator-configured external backend existing and being reachable —
  a new operational dependency and failure mode that did not exist when
  the feature was purely in-memory and single-invocation.
- Bad: only two backends are built today (Azure App Configuration, Azure
  Blob Storage) despite six being designed — real backend choice is
  narrower than the design anticipates until the rest are built (lower
  priority, tracked in [docs/work/key-value-store-integration.md](../work/key-value-store-integration.md)).
- Accepted gap: write-contention audit visibility (who held the
  optimistic lock, when) is not yet built — deferred to
  [docs/work/output-store-lock-audit.md](../work/output-store-lock-audit.md),
  low priority since Terraform's own backend locking already covers the
  actual IaC state-consistency concern; this gap is purely about
  observability for the separate, already-write-safe output-store path.
