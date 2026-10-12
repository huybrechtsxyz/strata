# Cross-Run Step-Output Delivery — Work

- Status: draft — key/value store capability split out to
  [key-value-store-integration.md](key-value-store-integration.md)
  (built, see that doc). **Implementation Plan steps 1-4 are built,
  including the `deploy_controller.py` wiring itself** (resolving the
  output store once per run, `_merge_durable_outputs()` as the
  cross-run fallback before each step's tokens are finalised, and
  `DeploymentOutputStore.record()` called per step after collection),
  **plus the configurable staleness trust window**
  (`WorkspaceSpecModel.output_store_max_age` / `deploy run
  --output-max-age`). **Step 5 (lock-audit reactivation) split out to its
  own doc**, [output-store-lock-audit.md](output-store-lock-audit.md) —
  not started, low priority (Terraform's own backend locking already
  covers the actual IaC use case; this is an audit-trail nice-to-have on
  top of an already write-safe CAS-retry path). Remaining backends
  (file, Consul, etcd, S3) also deferred, lower priority.
- **Code-review fixes (2026-10-12):** (1) a real governance-bypass bug —
  `semantic_checks.py` checked `output_store` before resolving
  `output_store_integration`'s own type, backwards from
  `resolve_output_store_integration()`'s actual precedence, letting a
  disallowed backend silently pass `strata validate` when a workspace
  declared both fields with disagreeing types; fixed to match the
  resolver's precedence exactly. (2) `output_store`/`output_store_
  integration` resolving to a backend lacking real compare-and-swap
  (e.g. `azure-keyvault` — see [key-value-store-integration.md](key-value-store-integration.md)'s
  new `SUPPORTS_CAS` note) is now rejected at validate time instead of
  silently never persisting anything, downgraded to an easy-to-miss
  warning on every single deploy.
- **Doc cleanup + v1-precedent re-check (2026-10-12):** stale "not yet
  built/designed" claims throughout this doc (left over from before the
  real build) corrected to match what's actually shipped. The
  long-standing "re-confirm the v1 lock-file precedent" open question is
  now resolved — checked directly against real v1 source, no such
  precedent exists; this feature is original to v2 (see Open Questions).
- Last updated: 2026-10-12
- Tracks follow-on work for: [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)
  (the settled decisions this doc's own brainstorming led to)
- Split out from: [deploy-time-output-sensitivity-gaps.md](deploy-time-output-sensitivity-gaps.md)
  gap 2

## Overview

`${output:step.key}` (docs/work/deploy-command.md's "Cross-step output
context") only ever resolves today when `step` ran *in the same `deploy
run` invocation* as the step referencing it — `deploy_controller.py`'s
`step_outputs: dict[str, dict[str, str]]` is purely in-memory, rebuilt
fresh every invocation. That's fine when one invocation covers every
step. It breaks down for a real, common deployment shape:

```
step1 (infra, tool: terraform)     -> produces vm_ip
step2 (config, different pipeline) -> ${output:step1.vm_ip} to set a firewall rule
step3 (installapps, another pipeline) -> ${output:step1.vm_ip} needed for AKS
```

All three are real `ProvisioningStepModel`s with real `depends_on: [step1]`
— the dependency tree already exists and is already walked correctly
(`_upstream_step_names()`, transitive). The gap is purely about **data
availability across separate `deploy run` invocations** (separate
pipelines, separate scopes/stages, possibly separate CI jobs entirely),
not about modeling the dependency itself.

## Current Design / Progress

**Accepted now, shipped as the actual fix (BUILT 2026-10-12, see
Implementation Plan below for exactly what):** cross-step
`${output:...}` resolution remains scoped to a single `deploy run`
invocation — that limitation is real and explicit, not silently patched
over. What changes is the *failure*: a missing cross-run reference is
now caught by a pre-flight check (`_merge_durable_outputs()`, before
that step's own tool runs) that reports exactly which step/key is
unusable and why — a durable record that genuinely doesn't exist yet is
forgiving (the step may not even reference it; the existing deep
token-resolution error still fires if it does), one that exists but is
stale or unreachable is not (see Security below for the sensitive-output
case, handled separately by never durably writing one in the first
place).

**The real fix — a pluggable cross-run/cross-pipeline output store —
is now fully designed and built**, `DeploymentOutputStore` on top of
the `KeyValueStoreIntegration` capability (see Implementation Plan
below).

### Why not just persist a file inside `build_path`?

Considered and rejected as the primary mechanism (brainstormed
2026-10-11): `build run`'s default `clean=True` wipes the entire
`build_path` before every render, which would delete any such file the
moment a later pipeline's own `build run` ran — before that pipeline's
`deploy run` ever got a chance to read it. Worse, in a real multi-pipeline
CI setup, each pipeline may check out a fully independent, ephemeral
`build_path` with no shared filesystem at all — nothing strata does
on-disk inside `build_path` can fix that.

`.strata/` (the existing runtime-state directory at the solution root,
`layout.py`'s `STRATA_DIR`) was considered next — it survives a
`build_path` clean/re-render within the *same* checkout, which is a
genuine improvement. But it still doesn't solve the fully-independent-
checkout case, and more importantly the user's own real deployment shape
is explicitly **separate CI pipelines**, each with their own ephemeral
workspace that "goes away" — there is no durable local filesystem at all
to anchor this to. The data has to live somewhere outside any single
pipeline's own disposable environment.

### Direction: a pluggable cross-run output store

Originally sketched as a "v1 precedent" (a lock-file system, conceptually
similar to Terraform's own remote state + locking, supporting multiple
backends including blob storage) — **checked directly against real v1
source and found not to exist** (see Open Questions' now-resolved entry
below): this design is original to v2. The shape still fits the real
problem regardless of provenance: step outputs need to be written
somewhere **durable and shared across pipelines**, with the actual
backend being a deployment/environment-specific choice, not something
strata hardcodes.

**Scope, settled:** one record per deployment, not one per step and not
one per workspace — a single small JSON document holding every step's
outputs collected so far for that deployment, keyed internally by step
name:

```jsonc
{
  "step1": {
    "collected_at": "2026-10-11T14:00:00Z",
    "values": { "vm_ip": "10.0.0.5" }
  }
}
```

**Staleness, settled in both shape and enforcement policy:**
`collected_at` is a real timestamp, checked against a configurable
"trust until"/max-age window on read — "you will need to re-run
`terraform plan` (or whatever re-collects outputs) regularly to keep
this fresh" is the user's own framing, i.e. freshness is a deliberate,
visible republish action, never an automatic background refresh.
**Decided:** an expired entry is a hard pre-flight failure, no
warn-and-use-anyway mode at all (not even as an opt-in) — same decisive
framing as the user's own: "if the output of the previous step is not
available, the step using it cannot be executed — that means the
pipeline stops." Treated identically to the missing-entirely case (same
pre-flight check, same "stop before any step's tool runs" bar) — a
silently stale `vm_ip` feeding a firewall rule is exactly the class of
quiet-wrong-value bug this whole unified-projection effort has been
about removing elsewhere, and a step that cannot get the input it
depends on has no well-defined way to proceed regardless.

**Layered architecture, settled:** split the raw I/O primitive from the
record's own business logic, so backends stay thin and swappable and the
merge/staleness/security logic is written exactly once:

- **A new capability** (raw key/value I/O, backend-agnostic) — not a
  reuse of `StoreIntegration` (`capabilities.py`), whose `resolve(key)
  -> str` is a single-value resolver with no version/CAS concept at all,
  a real shape mismatch for "read/write a small JSON record with
  optional compare-and-swap." Working name `KeyValueStoreIntegration`
  (not `BlobStoreIntegration` — the data here is always a small
  structured record, never a true large binary object, and the chosen
  first backends (plain file, Consul, Azure App Configuration) are all
  genuinely key/value shaped, not blob-shaped; blob storage (Azure Blob/
  S3) still implements the same interface trivially — a blob is just a
  bigger value at a key).
- **One shared, backend-agnostic layer on top** (not itself an
  `Integration` — plain code) owning: JSON (de)serialisation, the
  sensitive-key filter (never serialise a key in `collect_step_outputs()`'s
  own `sensitive_keys` — gap 1's return value is exactly this filter),
  the staleness check on read, and the merge-on-write (read current
  record, update only this run's steps, write back) — written once,
  reused by every backend.

**Concurrency, settled in mechanism (not yet in full coverage):**
compare-and-swap via a version/ETag the backend returns from `get()` and
optionally requires on `put()`, not a separate lock/unlock pair — no lock
to abandon if a process crashes mid-update, just a read-modify-write
retry loop on a version conflict. This maps onto real, native primitives
in most candidate backends: Consul's KV store has built-in CAS via its
`ModifyIndex`; Azure App Configuration's SDK supports conditional writes
via ETag `match_condition`; Azure Blob and S3 both support conditional
PUT via ETag. A plain file backend has no native version concept — see
Open Questions. Only matters in practice when two *unrelated* steps in
two parallel pipelines finish at nearly the same instant and both
read-modify-write the same record; dependent steps are sequential by
construction and never race with each other.

**Backend priority — settled, updated:** real implementation order is
**Azure Key Vault, Azure App Configuration, Azure Blob Storage, in that
order** — real teams already hold Azure credentials/infra for these, and
some teams specifically use Key Vault as a cheaper small-settings store
than App Configuration for non-secret values (a direct real-world fit
for this capability, not just this feature's own need). Supersedes this
paragraph's earlier call (file + Consul/App-Config first, Blob later) —
revised once the actual first-three consumers were known, not because
the earlier reasoning (reuse infra a workspace already has configured)
was wrong; Key Vault/App Config/Blob simply satisfy it more directly
than file+Consul did. File, Consul, etcd, and S3 are fully designed in
[key-value-store-integration.md](key-value-store-integration.md) too,
but are not next in line to build. CI-native artifact/cache
mechanisms (GitHub Actions `actions/cache`/`upload-artifact`+
`download-artifact`, Azure Pipelines `PublishPipelineArtifact`/
`DownloadPipelineArtifact`) are **deferred, not in this interface at
all** — both are fundamentally publish-once/immutable-per-run, not a
mutable single resource a get→modify→put loop can target, so they would
need their own, differently-shaped access pattern (e.g. one artifact per
step per run, not one evolving shared record) rather than implementing
`KeyValueStoreIntegration` directly. Revisit only if a real consumer
needs CI-native support specifically.

### Proposed interface — DONE, split into its own doc

The `KeyValueStoreIntegration` capability (raw get/put-with-CAS) plus its
first two backends (file, Azure App Configuration) are designed in full,
ready to implement, in
[key-value-store-integration.md](key-value-store-integration.md) — kept
out of this doc's own body since it's a genuinely standalone, reusable
piece. `DeploymentOutputStore` (the business-logic layer built on top:
serialisation, the sensitive-key filter, staleness, merge-on-write) stays
here, as the next step once the capability itself is built.

### Security constraint (non-negotiable, same as everywhere else in this
system)

**A sensitive output must never be written to this store, in any
backend.** Same ADR-0031 rule `strata.auto.tfvars.json`'s own sentinel
mechanism already enforces — this store is for ordinary data (a VM's IP,
a resource ID), not secrets. `collect_step_outputs()`'s already-shipped
`sensitive_keys` return value (gap 1, done) is exactly the filter this
store's writer needs: write only the keys *not* in `sensitive_keys`. A
`${output:...}` reference to a sensitive output that didn't run in this
invocation is a hard, clear, pre-flight error — not a case this store
ever tries to solve.

## Implementation Plan (`DeploymentOutputStore`, on top of the already-
designed key/value capability)

Identified by walking through "configure the backend, find the steps
that need it, gather+store on run" and checking what's actually
load-bearing between those three — steps 1-4 are built (below); step 5
(lock audit) split out to [output-store-lock-audit.md](output-store-lock-audit.md),
not started:

1. **Configuration — settled, mirrors `ProvisionerModel.tool`/`.integration`
   exactly (ADR-0022 D2), not a new mechanism. Fields, governance, and
   resolution function all BUILT (2026-10-12), see below.** Two new
   optional `WorkspaceSpecModel` fields —

   ```python
   output_store: str | None = Field(
       None,
       description="Key/value backend TYPE for cross-run step-output "
       "persistence (e.g. 'file', 'azure-appconfig', 'consul') — auto-binds "
       "to zero-or-one enabled Integration document of this type, same "
       "rule as a Provisioner's bare 'tool'. Omit to disable cross-run "
       "output persistence entirely (today's in-memory-only behavior).",
   )
   output_store_integration: str | None = Field(
       None,
       description="Explicit Integration document name for the output-store "
       "backend, overriding auto-bind — same precedence as a Provisioner's "
       "'.integration' field winning over '.tool'.",
   )
   ```

   **Built (2026-10-12):** both fields added to `WorkspaceSpecModel`
   (`workspace_model.py`) exactly as designed — `output_store: PlatformName
   | None` (matches `ProvisionerModel.tool`'s own type, not a bare `str`)
   and `output_store_integration: Annotated[PlatformName,
   References(PlatformKind.INTEGRATION)] | None` (the `References()`
   annotation means a dangling name is automatically caught by Phase 2's
   existing reference validation — confirmed via `test_references.py`'s
   own introspection test, which had to be updated to include the new
   `spec.output_store_integration` path once this landed).
   `resolve_output_store_integration()`/`_require_key_value_integration()`
   below are **also now built** (2026-10-12), in `integration_resolution.py`
   — the one real difference from `resolve_integration()`: both fields
   unset returns `None` (feature disabled) rather than being a usage
   error, since a Provisioner always names a `tool` but a workspace need
   not configure an output store at all. Built from pieces that already
   exist there unchanged: `output_store_integration` set wins outright
   (look the named document up by name, `_construct()` it); else
   `bind_integration_config(index, output_store, requester=...)` (the
   exact zero-or-one-enabled-match auto-bind every other capability tier
   already shares) + `_construct()`. A new `_require_key_value_integration()`
   guard (same shape as the existing `_require_infra_integration()`)
   rejects a resolved integration that doesn't implement
   `KeyValueStoreIntegration` (e.g. a typo'd `output_store: "terraform"`)
   or implements it but can't honor real CAS (`SUPPORTS_CAS` \u2014 added
   later, code review, see "Code-review fixes" at the top of this doc).
   22 tests in `test_integration_resolution.py` (9 new: named-binding-
   wins, missing-name, zero/one/many-candidate auto-bind, non-key-value-
   type rejection, unregistered-type rejection, and the both-unset-is-None
   case specifically). **The caller is also now built** — `deploy_
   controller.py`'s `deploy_run()` resolves it once per run (Implementation
   Plan step 4 below).

   **Deliberately not `ProvisionerBackendModel`'s shape
   (`type`+`configuration` inline dict)**, even though that model looks
   similar at a glance and already exists for exactly this
   "pluggable-backend" problem on the Terraform side: `ProvisionerBackendModel`
   is inert data strata hands to the `terraform` binary as an HCL block —
   strata itself never constructs a Python object from it. The output
   store needs a live `KeyValueStoreIntegration` *instance* strata's own
   code calls `.get()`/`.put()` on, which only the `Integration`/registry
   mechanism (`bind_integration_config()` + `registry.get()`) provides —
   the same reason every other capability (`StoreIntegration`,
   `InfraIntegration`) already goes through it instead of a bare
   type+dict field.

   **Governance — settled and BUILT (2026-10-12), a deliberate new
   addition, not copied mechanically:** `ConfigurationSecurityModel`
   (`configuration_model.py`) already has `allowed_secret_stores`/
   `allowed_variable_stores`/`allowed_feature_stores` — platform-wide
   allow-lists for the three existing store capabilities, enforced by
   `EnvironmentService.validate_allowed_stores()`. This capability gets
   the same treatment:

   ```python
   allowed_key_value_stores: list[str] | None = Field(
       None,
       description="Key/value backend types (e.g. 'azure-keyvault', "
       "'azure-appconfig', 'azure-blob') permitted for a Workspace's "
       "output_store/output_store_integration. None means unrestricted.",
   )
   ```

   Named after the **capability** (`Capability.KEY_VALUE`), not the
   *feature field* (`output_store`) — same reasoning as the capability's
   own rename from `output_store` to `key_value`: it already has more
   than one planned consumer (locks, docs/work/key-value-store-integration.md's
   "Validated reuse" section), so a future lock feature reusing a
   *different* workspace field would still be governed by this same
   allow-list, not need its own.

   Why this one is a deliberate addition rather than deferred (ADR-0003's
   "minimal slice" policy would normally argue against building a control
   for a feature with zero real callers yet): the three existing
   allow-lists were built *alongside* their store capabilities, not
   retrofitted later (`docs/work/gap_fit_v1.md`'s "RESOLVED 2026-09-29"
   entry) — this repo's own precedent is to ship the policy lever with
   the capability. More importantly, retrofitting it later is the
   expensive path specifically here: `None` (unrestricted) costs nothing
   today, but adding a restriction after real `workspace.yaml` files
   already use `output_store:` unguarded would be a breaking change for
   them. And the gap is real, not hypothetical: without this, an org that
   restricts `consul` as a secret store for compliance reasons could
   still have a workspace author point `output_store: consul` at the same
   physical cluster for "ordinary" data with zero policy review — the
   restriction they thought they'd enforced doesn't actually hold at the
   infrastructure level.

   **Enforcement** — new `WorkspaceService.validate_allowed_key_value_store()`,
   same convention as `EnvironmentService.validate_allowed_stores()` but a
   genuinely different shape: a workspace declares at most **one**
   key/value backend (not many independent per-key declarations like
   variables/secrets/features), and that backend can be named two ways
   (`output_store`, a bare type string — no lookup needed; or
   `output_store_integration`, a named `Integration` document whose own
   `spec.type` is the real type to check — needs the `DocumentIndex` to
   resolve, unlike `validate_allowed_stores()`, which only ever reads
   `self.model`). A dangling `output_store_integration` reference is not
   re-checked here — Phase 2's existing reference validation already
   catches that; this method only runs once a name is known to resolve.
   Wired into the same `semantic_checks.py` call site that already
   resolves the single `Configuration.spec.security` once and reuses it
   across every document needing it — one new loop over `Workspace`
   documents alongside the existing one over `Environment` documents, not
   a second independent `Configuration` lookup.

   **Built (2026-10-12), exactly as designed above:**
   `ConfigurationSecurityModel.allowed_key_value_stores`
   (`configuration_model.py`), `WorkspaceService.
   validate_allowed_key_value_store(security, backend_type)`
   (`workspace_service.py` — takes the already-resolved backend type
   directly, never touches `DocumentIndex` itself, per ADR-0021 D2), and
   `_check_workspace_key_value_store()` (`semantic_checks.py` — the
   controller-side resolution: `output_store` used directly, or
   `output_store_integration` resolved to its target document's own
   `spec.type`; a dangling reference silently skipped, matching the
   module's own established "skip rather than guess" rule), wired into
   `_check_workspaces()` alongside its existing topology/subnet checks.
   Full suite green (2366 passed after fixing one pre-existing
   introspection test that asserts `WorkspaceModel`'s exact reference-path
   set — `test_workspace_references_are_discovered_from_its_own_fields`
   — which had to include the new `spec.output_store_integration` path;
   finding that test fail was itself a confirming signal, not a bug: it
   proves a dangling `output_store_integration` name is automatically
   caught by Phase 2's reference validation, exactly as designed), mypy/
   ruff/lint-imports clean.

2. **Record identity / key naming convention — settled:** `/`-delimited
   keys, `strata/deployment-outputs/<deployment-name>` — the per-
   deployment record's own key in whatever backend is configured,
   matching "one record per deployment" (settled above). Chosen over
   this repo's two *other*, unrelated existing key-naming precedents —
   `"strata:tag-stability"` (colon-delimited, CycloneDX SBOM component
   properties) and `.strata/<area>/<sub>` (slash-delimited, on-disk
   runtime state, `layout.py`'s `STRATA_DIR`) — neither of which was
   designed for a key written into an arbitrary pluggable external
   backend: `/` is the one separator all six backends (Backends 1-6,
   [key-value-store-integration.md](key-value-store-integration.md))
   treat as an ordinary, unremarkable character in a key, never special
   to any of their write APIs, and it matches the on-disk precedent's
   own shape most closely. `<deployment-name>` is embedded as a bare
   path segment with no escaping needed — `PlatformName`'s own pattern
   (`^[a-z][a-z0-9_-]*$`) already guarantees a deployment/workspace name
   can never itself contain `/` or `:`. The fixed `strata/deployment-outputs/`
   prefix exists so an operator sharing one Consul/App-Config/bucket
   across unrelated purposes doesn't collide with something else living
   in the same backend.
3. **Write path — BUILT (2026-10-12), exactly as designed:**
   `DeploymentOutputStore.record(step, values, sensitive_keys)`
   (`src/strata/controllers/deployment_output_store.py`): filters
   `sensitive_keys` out first (gap 1's existing filter — a sensitive
   output is never serialised here, full stop), then a real
   read-modify-write: `get()` the current record (or treat a missing
   record as empty, writing with `NEW_KEY_VERSION`), merge in just this
   run's `step` entry (`collected_at` + the filtered `values`),
   `put(if_version=...)`, retrying the whole merge up to
   `_MAX_CAS_RETRIES` (5) times on `KeyValueConflictError` — never
   retrying with a blind unconditional `put()`. Retry exhaustion raises
   `strata.utils.errors.SystemError` (real contention, not a caller
   mistake). Actually *calling* this per step, immediately after that
   step's output is collected, is `deploy_controller.py`'s job — also
   now built, step 4 below.
4. **Read path — BUILT (2026-10-12), exactly as designed:**
   `DeploymentOutputStore.read(step)` (same file) — looks up the shared
   record, raises `OutputRecordNotFoundError` when no record exists at
   all *or* when it exists but has no entry for `step` (both are
   "nothing to read", not distinguished further at this layer — a
   missing backend entirely vs. a genuinely-never-recorded step look
   identical from here), and `OutputRecordStaleError` when an entry
   exists but `collected_at` is older than `max_age` (only checked when
   `max_age` is set — `None` means no staleness check at all). Both
   exceptions subclass `UsageError`.

   **`deploy_controller.py` wiring — BUILT (2026-10-12):** the output
   store is resolved once per `deploy_run()` invocation, right after the
   workspace itself (`resolve_output_store_integration(index, workspace)`
   — `None` when the workspace configures neither field, matching
   today's behavior exactly, no new required step for existing
   consumers). A new `_merge_durable_outputs()` helper runs per step,
   right where `visible_outputs`/`visible_sensitive_outputs` are already
   computed from this invocation's own `step_outputs` — for any upstream
   (transitive) dependency *not* already produced this run, it tries the
   durable store as a fallback, **deliberately asymmetric**: a record
   that was simply never written (`OutputRecordNotFoundError`) is
   forgiving — silently skipped, since this step may not even reference
   that upstream step's outputs, and the existing deep token-resolution
   error still fires later if it does; a record that exists but is
   untrustworthy (`OutputRecordStaleError`, or the backend itself
   unreachable) is not forgiving — both stop the pipeline immediately,
   with a clear message naming the step and the upstream step, before
   this step's tool ever runs. `DeploymentOutputStore.record()` is
   called immediately after `collect_step_outputs()` for every step
   (per step, not batched to end-of-run, exactly as designed) — a write
   failure (`SystemError`/`IntegrationError`) becomes a **warning**, not
   a hard failure, matching `collect_step_outputs()`'s own established
   precedent: the step already deployed successfully, so losing the
   durable record should not fail the run.

   **Staleness trust window — BUILT (2026-10-12):** `WorkspaceSpecModel.
   output_store_max_age` (seconds, `None` default — no enforcement,
   today's unchanged behavior) is the workspace-level default; `deploy
   run --output-max-age SECONDS` overrides it for a single invocation
   (same override precedence as `--pin` over `spec.version`). Whichever
   value wins is converted to a `timedelta` and passed to
   `DeploymentOutputStore`'s constructor, so `OutputRecordStaleError` is
   now reachable through a real `deploy run` — confirmed end to end with
   a backdated durable record against both the workspace-level setting
   and the CLI override.

   **Deliberately still deferred:** a true *static* pre-flight pass scanning every step's payload up front, before any
   step in the whole run starts — this wiring checks per step, right
   before that step's own tool runs (same granularity every other
   pre-flight check in this function already uses for `${var:}`/
   `${secret:}` tokens), not across the whole run ahead of time.

   Verified: 8 new tests in `test_deploy_controller.py` (6 direct unit
   tests against `_merge_durable_outputs()` covering all four outcomes
   plus the "already in memory, never consults the store" case, and 2
   full `deploy_run()` integration tests — one proving a step's output
   is actually durably recorded, one proving a later step resolves
   `${output:...}` from a durable record for an upstream step
   deliberately excluded from the invocation via `--stage`, simulating
   "ran in an earlier invocation" for real). Full suite green, mypy/
   ruff/lint-imports clean.
5. **Lock audit — split out to its own doc:**
   [output-store-lock-audit.md](output-store-lock-audit.md). Not started,
   low priority — for the primary IaC use case, Terraform's own backend
   locking already does the real locking; this is purely an audit-trail
   nice-to-have for `DeploymentOutputStore.record()`'s own (already
   write-safe) CAS-retry writes, split out so it gets its own tracked
   doc instead of living only as an unbuilt step here.

   **Interim step, done now, ahead of the rest of this plan:** since
   `ManifestLockReferenceModel`/`DeploymentManifestModel.lock` has zero
   real producer today — confirmed, nothing in `src/` or `tests/`
   references either beyond the model file itself — left live-but-always-
   `None` in a shipped audit schema is exactly the "looks implemented but
   isn't" gap this repo avoids elsewhere. Commented out in place (not
   deleted) in `audit_manifest_model.py`, pointing at the split-out doc
   above for its reactivation plan.

## Open Questions

- **Staleness enforcement policy — settled:** hard pre-flight failure,
  always, no warn-and-use mode even as an opt-in — see the Staleness
  paragraph and Implementation Plan step 4 above.
- **The file backend has no native version/CAS primitive for an
  *update* — settled, superseded by a stronger mechanism than originally
  planned:** originally "accept no concurrency protection for this
  backend at all" (silent degradation). Now instead rejected outright at
  validate time for this feature specifically — `SUPPORTS_CAS` (code
  review, 2026-10-12) will be `False` on the file backend once built (see
  [key-value-store-integration.md](key-value-store-integration.md)'s own
  Backend 1 section), so `output_store: file` fails loud and early
  instead of silently never protecting an update the way Key Vault
  originally did. The file backend itself remains otherwise useful
  (other `KeyValueStoreIntegration` consumers that only ever need
  create-only semantics, e.g. a simple lock-acquire, are unaffected).
- **Where is the backend configured? — settled:** two new
  `WorkspaceSpecModel` fields (`output_store`/`output_store_integration`),
  resolved via the existing `bind_integration_config()`/`_construct()`
  pair, same mechanism as every other capability tier — see
  Implementation Plan step 1 above.
- **Does this need its own ADR? — RESOLVED (2026-10-12):** yes —
  [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md),
  covering the new capability, the optimistic-CAS concurrency model, the
  staleness/sensitivity rules, and the governance allow-list. This doc
  remains the brainstorming/evidence record and build-progress tracker;
  the ADR records only the decisions themselves.
- **Re-confirm the v1 lock-file precedent against real v1 source — RESOLVED
  (2026-10-12): no such precedent exists.** Checked directly against v1's
  real source (`e:\SourcesXYZ\strata`) — zero matches anywhere for
  `lock`/`Lock`, and zero for `remote_state`/`blob_storage`/`shared_state`/
  `output_store`/CAS-related terms either. v1 has no lock-file system, no
  remote-state abstraction, and no cross-run output store of any kind —
  this whole feature (capability, governance, CAS gating, staleness
  window) is original to v2, not a port of anything. The "user's own
  recollection" that motivated the original design sketch did not hold up;
  corrected here rather than left open.

## Related

- [key-value-store-integration.md](key-value-store-integration.md) —
  the `KeyValueStoreIntegration` capability + file/Azure App Configuration
  backends this doc's `DeploymentOutputStore` layer is built on, designed
  in full and ready to implement as its own first step.
- `src/strata/controllers/integration_resolution.py` — `resolve_integration()`/
  `bind_integration_config()`/`_construct()`, reused unchanged by this
  doc's new `resolve_output_store_integration()` (Implementation Plan
  step 1).
- `src/strata/models/provisioning_model.py`'s `ProvisionerModel.tool`/
  `.integration` — the named-binding-wins-else-auto-bind duality step 1's
  two new `WorkspaceSpecModel` fields mirror exactly; `ProvisionerBackendModel`
  — the look-alike inline `type`+`configuration` shape considered and
  rejected for this (inert HCL-bound data, not a live Python instance).
- `src/strata/models/audit_manifest_model.py`'s `ManifestLockReferenceModel`
  — the existing, currently-unpopulated lock-audit shape
  [output-store-lock-audit.md](output-store-lock-audit.md) (step 5,
  split out) plans to reuse for the output store's own CAS-retry write
  attempts.
- [deploy-time-output-sensitivity-gaps.md](deploy-time-output-sensitivity-gaps.md) —
  gap 1 (sensitive `${output:}` handling, done) lives there; this doc is
  gap 2, split out once its scope grew past "a gap."
- [docs/work/deploy-command.md](deploy-command.md) — the
  existing `${output:...}` cross-step token design this extends.
- `src/strata/integrations/capabilities.py`'s `StoreIntegration` — the
  existing pluggable-backend-by-registry pattern this reuses structurally.
