# Cross-pipeline output promotion — writing stage outputs into variable/secret stores

- Status: proposed
- Date: 2026-08-11
- Related: [ADR-0005](0005-secret-resolution-at-build-time.md) (secret resolution model — `var:`/`secret:` read side this ADR writes into), [ADR-0026](0026-resolved-model-cache.md) (resolved-value cache — staleness lever on the consuming side), [ADR-0058](0058-cross-deployment-dependency-gating.md) (cross-deployment dependency gating — the ordering half of this same problem), [ADR-0063 Gap 4](0063-gap4-output-passing.md) (`inputs_from` — same-deployment, same-invocation output passing this ADR does *not* replace), [ADR-0063 Gap 5](0063-gap5-output-capture.md) (`deployment-outputs.json` — the durable artifact this ADR promotes data out of), [ADR-0065](0065-strata-state-service.md) (state service — a candidate secondary channel, see Option C), [ADR-0067](0067-server-identity-authentication-authorization.md) (server identity/auth — gates the state-service channel)

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

Strata already has a well-built mechanism for passing a Terraform (or other provisioner)
output from one stage to the next: `ResolvedValues.stage_outputs`, populated by
`TerraformDeployer.collect_outputs()` after `apply` and auto-injected into every
subsequent stage's subprocess environment (`TF_VAR_<key>` for Terraform, a bare `<key>`
env var for Ansible/Compose — see `inject_tf_vars`/`inject_compose_env`). This works well
for the case it was built for: stages within **one deployment file, in one
`strata deploy run` invocation**.

It breaks down completely for a scenario that shows up as soon as an organization splits
infrastructure lifecycle across **independent pipelines** rather than stages of one
pipeline — for example:

```
Pipeline A — bootstrap_customer.yaml   (runs once per tenant onboarding)
    provisions: tenant namespace, Key Vault secret scope, storage account
    outputs: namespace_name, keyvault_uri, storage_account_name, db_admin_password (sensitive)

Pipeline B — deploy_environment.yaml   (runs on every app release, independently, later)
    needs: namespace_name, keyvault_uri, storage_account_name, db_admin_password
```

Pipeline B may run hours, days, or weeks after pipeline A, from a different CI job,
possibly a different repository, with **no shared process memory and no shared
filesystem**. `ResolvedValues.stage_outputs` cannot help — it dies with the process that
created it. This is also exactly the three-layer bootstrap pattern already sketched (as a
design draft) in `docs/guides/at-scale.md` — global bootstrap, zone bootstrap, tenant
bootstrap, each a separate deployment file, each potentially run by a separate pipeline —
so this is not a hypothetical, it is the natural consequence of strata's own recommended
at-scale architecture.

This ADR was triggered by a narrower, concrete case (a `kind: dns` record needing a VM's
public IP produced by an earlier stage — see the DNS `output_key:` work) whose own design
notes flagged that the "same invocation" constraint would eventually need a real answer.
This ADR is that answer, generalized past DNS to any producer/consumer pair.

## Decision Drivers

- **The correct boundary between two independent pipelines is a network-reachable,
  access-controlled store — not a shared disk.** Different CI runners, possibly
  different repos/orgs, cannot be assumed to share a filesystem or artifact cache.
- **Don't invent a new storage abstraction.** Strata already has one:
  `VariableStoreType`/`SecretStoreType` + `StoreIntegration` (Vault, Consul, Azure App
  Config, Bitwarden, etc.), already read by `var:`/`secret:` everywhere in the platform.
  The read side is solved; only the write side (promoting a stage output into one of
  these stores) is missing.
- **Sensitive and non-sensitive outputs must route to different backend classes.**
  Terraform's `sensitive` output flag already splits `stage_outputs` from
  `stage_outputs_sensitive` (ADR-0063 Gap 5) and `deployment-outputs.json` already never
  writes a sensitive value to disk (`sensitive_keys` lists keys, omits values). Any
  promotion mechanism must preserve that split rather than re-introduce a path where a
  generated password ends up in a config store.
- **Ordering is a separate concern from data.** ADR-0058 already establishes "did the
  upstream deployment succeed?" as its own mechanism (`spec.requires`). This ADR must not
  try to also solve ordering — it only concerns itself with "what values did upstream
  produce," assuming ADR-0058 (or an equivalent operator-side gate) already answered "is
  upstream done."
- **Fail loud, not silent.** `ValueController` already treats a missing/unreachable store
  as fatal (`resolved.store_unavailable_errors` overrides `strict` in both directions). A
  failed promotion write must be held to the same bar — a downstream pipeline silently
  reading a stale or missing value is worse than an upfront failure.
- **Multi-tenant collisions are the default failure mode, not an edge case.**
  `docs/guides/at-scale.md` describes ~100 tenants sharing a landscape; a flat key
  namespace for promoted outputs collides on the first day two tenants bootstrap in
  parallel.

## Considered Options

### Option A — Status quo: manual extraction from `deployment-outputs.json`

Pipeline B's CI job downloads pipeline A's `deployment-outputs.json` (ADR-0063 Gap 5) as
a build artifact and `jq`-extracts the value it needs, passing it into pipeline B as a
CI variable.

- Pro: nothing to build; this already works today, and is explicitly documented as the
  supported consumption pattern for that artifact.
- Con: requires CI-specific artifact plumbing between two pipelines (works differently in
  every CI system), is invisible to strata (no validation that the reference is even
  correct until the downstream job fails), and never covers sensitive outputs at all
  (`deployment-outputs.json` deliberately never writes secret values to disk).

**Rejected as the only answer** — it is a real, working escape hatch and remains valid for
ad-hoc/low-frequency cases, but does not scale past a handful of pipeline pairs and has no
answer for secrets.

### Option B — Generalize the drafted `spec.inputs.from` (`docs/guides/at-scale.md`) to be genuinely remote

Extend the design-draft, unimplemented `spec.inputs.from` mechanism so that instead of
reading an upstream deployment's build artifact from local/shared disk, it fetches it over
a network call.

- Con: this reinvents a network-fetch protocol and an auth model from scratch, for a
  narrower need (read one JSON file) than a real KV store already solves.
- Con: `spec.inputs.from` was explicitly scoped (ADR-0058's Option 4 discussion) to
  build-time property injection for one deployment reading another's outputs — conflating
  it with general-purpose cross-pipeline value distribution overloads a still-unimplemented
  field with a second responsibility, the same objection ADR-0058 already raised and
  rejected for reusing that field as a gating mechanism.

**Rejected**, kept explicitly out of scope — if/when `spec.inputs.from` is built for its
original narrower purpose, it may reuse whatever "read a value" helper this ADR
introduces, but the YAML surfaces stay distinct.

### Option C — Route promoted outputs through the strata state service (ADR-0065)

The state service (ADR-0065) is a small, first-party HTTP+SQL event store, already
reachable from any pipeline via the existing `webhook` audit sink, already used to durably
forward `cost.recorded`/`drift.recorded`/`manifest.recorded`-style events. A new event type
(e.g. `deployment.outputs_promoted`) could carry a stage's non-sensitive outputs to the
same store, and a downstream pipeline could query them back out.

Genuinely useful as an **extra, complementary channel** — it is already durable, already
central, and the emit side (`AuditController.forward()`, sinks, redaction) is entirely
built. But it is not a good primary mechanism for this ADR's purpose today, for three
reasons:

- **No read API yet.** ADR-0065's own "Remaining Work" marks Phase 3 (a query/read API)
  as deferred. Today, "reading a value back" means a downstream pipeline running a raw
  SQL query directly against the state service's database (explicitly the supported
  pattern per ADR-0065 — "operators query the database directly with any SQL tooling") —
  workable, but heavier than a `var:`/`secret:` declaration, and it hands out direct DB
  access to every consuming pipeline rather than a scoped read.
- **It is an append-only event log, not a current-value store.** Consuming a promoted
  output means "find the most recent `deployment.outputs_promoted` event for this
  deployment/key," a different (and slower) access pattern than a KV `get`. Fine for
  audit/history; not the natural fit for "give me the current keyvault URI."
- **It never carries secrets.** Same redaction rule as `deployment-outputs.json` — a real
  secret store is still required for the sensitive half of the problem regardless of
  whether this channel exists.

**Adopted as a secondary, optional channel** (see Decision Outcome) — not the primary
mechanism, and re-evaluate its role once ADR-0065 Phase 3 ships a real read API.

### Option D — Promote stage outputs into existing variable/secret stores via `StoreIntegration.set_*` (RECOMMENDED)

Add a declarative `promote_outputs:` mapping on a deployment stage. After that stage's
`collect_outputs()` succeeds, strata writes each listed output into a real variable or
secret store using APIs that already exist and are already unused for this purpose —
`StoreIntegration.set_variable()` / `set_secret()` (every backend: Vault, Consul, Azure
App Config, Bitwarden, etc.). The downstream pipeline then just declares an ordinary
`var:`/`secret:` in its own `environment.yaml`, pointed at the same store and key —
**zero new resolution code anywhere else in strata**, because `var:`/`secret:` resolution
already exists and already speaks every one of these backends.

- Pro: reuses the read side (`var:`/`secret:`, `ValueController`, every store integration)
  completely unchanged.
- Pro: network-reachable and access-controlled by construction — this is what
  Vault/Consul/Azure App Config/Bitwarden are for; no new remote protocol invented.
- Pro: naturally preserves the sensitive/non-sensitive split — `secret:`-classed outputs
  can only be promoted via `set_secret` into a real secret store, never a config store.
- Pro: precedent already exists in the codebase for "strata writes into a store" —
  `ValueController._resolve_variable()`'s seed-on-missing path already calls
  `integration.set_variable(key, item.default)` when a declared variable's default needs
  writing on first use.
- Con: genuinely new surface area — a new stage-level model field, new deploy-orchestrator
  wiring (a write step after `collect_outputs()`), new validation (tenant/deployment key
  scoping, sensitivity routing), and new failure-mode handling.

**This is the winning option**, with Option C (state service) adopted as a secondary,
lower-priority channel for the same output data where an operator already runs the state
service and mainly wants audit/discovery rather than direct consumption — most consumers
are still expected to prefer a store (Option D), matching the pattern nearly every other
`var:`/`secret:` producer in strata already uses.

## Decision Outcome

**Option D (store-backed promotion)** as the primary mechanism, **Option C (state
service)** as an optional secondary/audit channel for the same data, explicitly paired
with **ADR-0058's `spec.requires`** for ordering (this ADR only ever addresses "what
values did upstream produce," never "is upstream done").

### Detailed design (sketch — not fully specified, subject to review during implementation)

```yaml
# bootstrap_customer.yaml — Pipeline A
stages:
  - name: infrastructure
    provisioner: terraform_azure
    promote_outputs:
      - key: namespace_name                # this stage's Terraform output name
        save_as: {variable: acme_namespace_name, store: azure_appconfig}
      - key: keyvault_uri
        save_as: {variable: acme_keyvault_uri, store: azure_appconfig}
      - key: db_admin_password
        save_as: {secret: acme_db_admin_password, store: azure_keyvault}   # sensitive → secret store only
```

```yaml
# deploy_environment.yaml — Pipeline B, a completely separate pipeline/invocation
spec:
  variables:
    - key: keyvault_uri
      store: azure_appconfig
      value: acme_keyvault_uri        # ordinary var: read — no new strata code needed here
  secrets:
    - key: db_admin_password
      store: azure_keyvault
      value: acme_db_admin_password
```

Key rules to enforce during implementation:

1. **Tenant/deployment-scoped key naming is mandatory, not advisory.** A flat namespace
   collides across tenants on day one at the scale `docs/guides/at-scale.md` describes.
   Validation should require (or auto-derive) a prefix, not just document a convention.
2. **Sensitivity routing is enforced, not trusted.** An output Terraform marks `sensitive`
   must not be promotable via `save_as.variable` — only `save_as.secret`, into a
   `SecretStoreType`-backed store. Mirrors the split `deployment-outputs.json` already
   enforces.
3. **Write failure is fatal to the deploy**, matching `resolved.store_unavailable_errors`'
   existing always-fatal behavior — never a warning-and-continue.
4. **Idempotent overwrite, not append.** Re-running `bootstrap_customer` must overwrite the
   previously promoted value, not create a duplicate/versioned entry (that's what the
   state service / `deployment-outputs.json` are for, if history matters).
5. **Staleness on the consuming side** is already governed by ADR-0026's
   `resolved_values` cache (`--refresh-cache`) — confirm promoted keys aren't served from
   a stale cache entry without an explicit refresh.
6. **Provenance stays consistent with `deployment-outputs.json`.** Both are derived from
   the same `collect_outputs()` call; they should never disagree about what a stage
   produced.

### Consequences

- Good: cross-pipeline data sharing becomes a normal, declared `var:`/`secret:` — the same
  mental model operators already use everywhere else in strata, not a bespoke mechanism
  per consumer.
- Good: composes with point 2's DNS `output_key:` (same-invocation shortcut) and ADR-0058's
  `spec.requires` (ordering) without overlapping either.
- Good: the state service (ADR-0065) gets a plausible, additive use case (audit/discovery
  of promoted outputs) without becoming a hard dependency for anyone who doesn't already
  run it.
- Bad: real new surface area — a new model field, deploy-orchestrator write step, and
  validation rules, none of which exist today.
- Bad: introduces a second place (alongside `deployment-outputs.json`) that records "what
  did this stage output" — must be kept consistent by construction (same call site), not
  by convention, or the two will drift.
- Neutral: does not remove Option A (manual `deployment-outputs.json` extraction) — it
  remains a valid low-frequency escape hatch; this ADR only stops it being the *only*
  answer.
