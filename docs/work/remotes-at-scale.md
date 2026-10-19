# Remotes at Scale (per-customer repositories) — Work

- Status: draft
- Last updated: 2026-10-19

## Overview

**Premise.** Strata deploys a shared platform (e.g. Azure API Management) from an infra repo
(`iac-int-deployment`: Terraform, Ansible, pipelines). A *separate* concern — each customer's own
API definitions (OpenAPI contracts, APIM policies, schemas) — lives in **one repository per
customer**, and has to stay that way (customer separation is a hard requirement, not a
preference). At realistic scale that is **~300 customer repositories**, each consumed by exactly
one tenant's deployment.

The *consuming* side is already solved: `kind: asset` (`asset_model.py`,
[asset-management.md](../design/asset-management.md)) materialises per-deployment input files at
build time, and its `spec.remote` can point outside the solution's own repository. The gap is the
*declaring* side: `SolutionRemoteModel` (`strata.yaml`'s `spec.remotes`) was designed for a
**small, hand-curated set of infra-level sources** — the bootstrap `config` repo, a few
Terraform/Ansible module repos, a chart registry — and was never evidenced or designed against
per-tenant fan-out:

- Its whole justification for being a *named* indirection is dedup — "one remote, one ref, so two
  modules can't silently drift to different versions of the same source" (`SourceModel`
  docstring). With 300 single-consumer repos that payoff never materialises (the same reasoning
  that reintroduced `chart_repository` as an inline escape hatch).
- `strata.yaml` is the single bootstrap document. 300 entries there means every customer
  onboarding edits the shared solution manifest.
- No per-remote credentials exist yet: `remote.integration` is schema-only — the `"sources"`
  capability has no ABC ([remotes.md](remotes.md) item 4); `git clone` relies on whatever git auth
  is configured locally.

This doc enumerates the options for declaring per-customer sources, before any is chosen.

### Requirements

- **R1 — One repository per customer.** No shared repo; customer separation is a hard
  requirement.
- **R2 — Each customer has its own versions.** Customer A's API definitions move to a new ref
  independently of customer B's — a per-customer pin, not one ref for all customers.

## Current Design / Progress

### What references a remote today

Every field typed `Annotated[PlatformName, RemoteReference()]`:

| Field                                              | Direction                   | Resolved by                          |
| -------------------------------------------------- | --------------------------- | ------------------------------------ |
| `AssetSpecModel.remote`                            | **pull** (files)            | `sync_asset()` → `resolve_remote()`  |
| `SourceModel.remote` (provisioner/module `source`) | **pull** (IaC code, charts) | `sync_source()` → `resolve_remote()` |
| `ProvisionerGitOpsModel.remote`                    | push (GitOps config repo)   | `git_push.py`                        |
| `AuditGitSinkTargetModel.remote`                   | push (audit sink)           | audit git sink                       |

`kind: artifact` is *not* in this list — `spec.registry` is free text; strata never fetches an
image. Both pull paths funnel through one primitive, `remote_resolution.resolve_remote()`, so any
option below fixes asset and source consumers alike if it changes what that primitive accepts.

### Constraints any option must respect

- **C1 — Bootstrap ordering.** Remotes must resolve before Configuration loads, because
  Configuration itself can live in a remote (`SolutionRemoteModel` docstring). Any remote that
  hosts Configuration must stay in `strata.yaml`; per-customer remotes never do, so they *may*
  live elsewhere.
- **C2 — Identity, not paths (ADR-0015).** Cross-document references are `(kind, meta.name)`;
  `RemoteReference()` today validates against the manifest's `remotes` list only.
- **C3 — Checkout identity.** `layout.remote_checkout_path(root, remote, reference)` keys a
  checkout by remote **name** + ref. A remote whose URL varies per deployment breaks that key.
- **C4 — Version pinning.** `kind: version`'s `spec.pins.remotes` is keyed by
  `SolutionRemoteModel.name` and overrides `reference`. An anonymous/inline remote cannot be pinned.
- **C5 — Credentials.** Per-customer repos may need per-customer credentials. The read path is
  half-built: `resolve_remote()` already accepts `auth`/`resolved_values` (added for
  docs/design/gitops-integration.md Phase 4, shares `git_push.prepare_git_credentials()`), but
  none of its three callers in `source_sync.py` (`sync_source()`, `sync_asset()`,
  `sync_module_source()`) passes it — every read-path clone is still ambient-auth only.
  `fetch: external` (CI checks out, strata only reads) remains the only proven private-repo
  pattern.
- **C6 — Scale.** Discovery, validation and `strata graph` must stay usable with ~300 extra
  declarations; a build must only fetch the remote(s) *its own* deployment needs (already true:
  `build_run()` scans only the executed steps' assets).

### What R2 runs into today (verified against source)

The version mechanism *can* carry a per-customer ref, but three things push back:

- **V1 — One version document per deployment.** `Deployment.spec.version` names exactly one
  `kind: version` document (`deployment_model.py`); `apply_remote_version_pins()`
  (`build_controller.py`) overlays its `pins.remotes[<remote name>]` onto that remote's
  `reference`. So per-customer versions = each customer deployment points at its *own* version
  document. That works mechanically — but `VersionModel` has no `extends`/layering, so every
  platform-wide pin (APIM Terraform remote, images, charts) must be repeated in every customer's
  version document. A platform bump becomes a 300-file edit.
- **V2 — Pins only apply to `fetch: strata` remotes.** `VersionPinsModel.remotes` rejects a pin on
  a `fetch: external` remote (CI already placed it, so the pin could only misreport). R2 via
  `kind: version` therefore requires strata itself to clone the customer repo — which drags in
  C5 (credentials), currently unsolved.
- **V3 — Promotion slots are per workspace.** `build_promotion_view()`
  (`promotion_controller.py`) groups every version document sharing a `spec.workspace` by
  `(ring, order, wave)` and errors on a duplicate slot (`promotion_slot_duplicate`). 300 customer
  version documents all tagged `ring: prd` in the same APIM workspace collide — unless each gets
  a distinct `wave`, which bends `wave` (a rollout batch) into a customer id.

So R2 is not only a remotes question: it is equally a **version-composition** question —
"platform pins + customer pins" as two layers, where today a deployment gets exactly one.

**Caveat (found in the Option 3b deep dive):** V1 and V3 only bite if R2 is read as "one
*version document* per customer". If each customer's source has its **own name** (Options 1,
3b), the pin *keys* are already per customer — so one shared version document per ring can hold
all 300 customer pins side by side (`pins.<category>.c0062-api: v1.4.0`, `...c0063-api: v2.0.1`).
Each customer still has its own version; no platform-pin duplication (V1), no promotion-slot
collision (V3). Whether that reading of R2 is acceptable is Q4.

## Options

### Option 1 — New `kind: remote` document

Same fields as `SolutionRemoteModel`, as a standalone discovered document — one file per customer
repo (e.g. `customers/c0062/remote.yaml`), added like any other kind.

- **+** Fits the existing "standalone kind, referenced by name" pattern exactly (asset, artifact,
  tenant). Onboarding = add a file, never touch `strata.yaml`.
- **+** Named → version-pinnable (C4), checkout-keyable (C3), shows up in `strata graph`.
- **−** Two places declare remotes (manifest for bootstrap per C1, documents for the rest).
  `RemoteReference()` must resolve against the union, with a name-collision rule.
- **−** Still one declaration per customer (300 files) — moves the list, doesn't shrink it.

### Option 2 — `configuration.spec.remotes`, merged across files

Each remote declared in its own `kind: configuration` document; the loader already merges every
configuration document in the `spec.configuration` directory.

- **+** Reuses an existing merge mechanism; no new kind.
- **−** Directly collides with C1 — the reason remotes were put on the manifest *instead of*
  Configuration. Workable only by splitting (bootstrap remotes on the manifest, the rest on
  Configuration), at which point it is Option 1 with a worse home: Configuration is
  platform-level policy, not a per-customer inventory.
- **−** `configuration.py` already explicitly says `spec.remotes` is NOT here, on purpose.

### Option 3 — Inline remote block on the consumer (`asset.spec.remote` as an object)

Allow `spec.remote` to be either a name *or* an inline `{type, url, reference, fetch,
integration}` block — the same "by-reference or inline, never both" shape as
`SourceModel.chart_repository` vs `remote`. Could extend to `SourceModel` too.

- **+** Precedent exists (`chart_repository`); single-consumer case has zero indirection.
- **−** Anonymous → no version pin (C4) and no stable checkout key (C3) without inventing a
  synthetic name.
- **−** Still one asset document per customer (300 asset documents, each with a hard-coded URL)
  — the per-customer list just moves into assets.
- Superseded by Option 3b, which keeps the inline idea but gives the location the asset's own
  name, fixing both C3 and C4.

### Option 3b — Self-contained asset (artifact-shaped) — deep dive

#### Idea

Compare the three things that point outside the solution today:

|                      | `asset`                                   | `artifact`                                 | `source` (`SourceModel`)                          |
| -------------------- | ----------------------------------------- | ------------------------------------------ | ------------------------------------------------- |
| Strata fetches?      | yes — `sync_asset()` copies files         | never — runtime pulls the image            | yes — `sync_source()` git, or Helm pull           |
| Location declared on | a manifest remote (`spec.remote`)         | **itself** (`image_name`/`image_tag`)      | a manifest remote, or inline `chart_repository`   |
| Version pin          | only indirectly, `pins.remotes[<remote>]` | **own**, `pins.artifacts[<artifact name>]` | `pins.remotes[<remote>]`, `pins.charts[<module>]` |
| Credentials          | via the remote's `integration`            | **own** `spec.integration`                 | via the remote's `integration`                    |

`artifact` is the only one that carries identity, version and credentials on the document
itself. Option 3b gives `asset` the same shape: an asset may declare its own location instead of
naming a manifest remote. The asset's `meta.name` becomes the identity that pins, checkout paths
and credentials key on — no manifest entry, no templating (Option 4), no tenant link (Option 5).

#### Shape (sketch)

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: c0062-api
spec:
  description: "API definitions for customer c0062"
  location:                     # mutually exclusive with spec.remote
    type: git
    url: https://git.example.com/customers/c0062-api.git
    reference: v1.4.0           # overridable by pins.assets[c0062-api]
    fetch: strata               # or external
    integration: customer-git   # optional, 'sources' capability
  source_path: "openapi/**/*.json"
```

`location` = `SolutionRemoteModel` minus `name` (the asset's own name fills that role), reusing
its validators (`validate_reference_for_type`, `validate_local_remote`, traversal guard on
`reference`) unchanged. `remote` XOR `location` — same either/or rule as `SourceModel.remote` vs
`chart_repository`. Omitting both keeps today's meaning: this solution's own repository.

#### What changes in code (verified call sites)

- **Model** — `AssetSpecModel` (`asset_model.py`) gains `location`; XOR validator with `remote`.
- **Fetch** — `sync_asset()` (`source_sync.py`) today does `remote = remotes.get(asset.spec.remote)`
  then `resolve_remote(root, remote)`. With `location` it builds a `SolutionRemoteModel` from it
  (name = asset name) and calls the same `resolve_remote()`. No new clone logic; `fetch: external`,
  ref-keyed caching and the clone path all come for free.
- **Checkout path** — `layout.remote_checkout_path(root, name, ref)` keys on name + ref under
  `.strata/remotes/`. An asset name could collide with a manifest remote name there, so either a
  separate root (e.g. `.strata/assets/<name>/<ref>`) or a Phase 2 "asset/remote name collision"
  error. Separate root is simpler and needs no cross-kind rule.
- **Pins** — new `pins.assets` category (`PIN_CATEGORIES`, `VersionPinsModel`), overlaying
  `location.reference` exactly like `apply_remote_version_pins()` overlays a remote's `reference`.
  `check_version_pins()` (`version_pins.py`) gains an `assets` branch: stale-pin warning when no
  asset matches, `pin_not_applicable` when the asset has no `location` (it rides a manifest
  remote — pin that instead) or its `location.fetch` is `external` (same rule as remotes, V2).
  `log_pin_applied()` reused as-is.
- **Credentials** — `resolve_remote()` already takes `auth`/`resolved_values` (C5); `sync_asset()`
  would be the first read-path caller to pass them, resolved from `location.integration`. Optional:
  `fetch: external` needs none of this.
- **Validation** — `location.integration` gets `References(PlatformKind.INTEGRATION)` + a
  `sources`-capability check, matching how `SolutionRemoteModel.integration` is checked by
  `solution_service.py`.

#### The real gap: selecting the customer's asset

Asset *selection* is static today and lives on the **workspace**:
`WorkspaceSpecModel.assets` (pool) + `ProvisioningStepModel.assets` (per step), checked by
`validate_execution()` (`workspace_model.py`); `build_run()` scans exactly those names. But the
APIM workspace is *shared* by every customer deployment — a step saying `assets: [openapi]`
names one asset, not "this deployment's customer's asset". `DeploymentSpecModel` has no asset
field at all. With 300 per-customer asset documents, something must say which one fills the
step's slot for *this* deployment. Candidates:

- **S1 — Deployment binds a slot.** Workspace step declares a slot name (`assets: [api]`); the
  deployment maps it: `spec.assets: {api: c0062-api}`. Deployments are already per customer
  (`spec.tenant`, `spec.properties`), so this is where per-customer facts live today. The
  binding is on the deployment, not the remote/asset — the asset stays customer-agnostic, so
  Option 5's "no customer link" decision is not reopened. Needs: new deployment field, Phase 2
  check that every step slot is bound (and only to existing assets), `build_run()` resolving slot
  → asset name via the deployment.
- **S2 — Templated asset name in the step** (`assets: ["{customer}-api"]`). Same machinery as
  `source_path` filling, but it is templating a *reference*, the thing Option 4 rejected for
  remotes. Likely rejected for the same reason.
- **S3 — One workspace per customer.** Workspaces are the shared platform recipe; 300 copies is
  exactly the duplication this doc is trying to avoid. Rejected.
- **S4 — Environment override (v1 precedent).** The workspace declares a named slot with a
  default; an environment rebinds it. This is exactly how v1 retargeted *providers* per
  deployment — verified in v1's `environment_model.py`, `EnvironmentProviderOverrideModel`:

  ```yaml
  # v1 environment
  spec:
    overrides:
      providers:
        - provider: azure                 # must match a provider in the workspace
          file: providers/americas.yaml   # replaces the workspace's provider file binding
          configuration: { ... }          # optional property overrides on top
  ```

  Its own docstring: *"enabling the same workspace to target different regions or cloud accounts
  per deployment."* The same v1 `EnvironmentOverridesModel` also carried `remotes` (override a
  remote's `reference` only — "structural fields … must not vary per environment"), plus
  `resources`, `modules`, `properties`, `includes`. The v2 equivalent for assets (by name, not
  file — ADR-0015):

  ```yaml
  # workspace
  spec:
    assets: [api]                 # slot, default document 'api'
  # customer environment (v2 sketch)
  spec:
    overrides:
      assets:
        - asset: api
          use: c0062-api
  ```

  - **+** Real v1 precedent for exactly this "same workspace, different binding per deployment"
    problem. Environments are already per customer (`deployment.spec.environments`,
    `tenant.spec.environments`), so the binding lands where per-customer values already live.
  - **+** One mechanism could later also cover providers (westeurope vs americas) and remote refs.
  - **−** Reopens v2's deliberately deferred `overrides` subtree: v2's `environment_model.py`
    dropped all of it (0 uses across 26 real environment documents), parking `overrides.remotes`
    with *"Re-add when Deployment exists to select an environment"* — that condition is now met.
  - **−** Partial overlap with version pins, which already override remote refs per deployment.
  - **−** Environments merge (tenant first, then deployment) — needs an explicit precedence rule
    for two environments overriding the same slot.

  v2 today has **no** provider override either: `WorkspaceSpecModel.providers` is a fixed list
  of provider names that `build_controller.py` reads directly.

S1 and S4 were the candidates consistent with the decisions so far (S2/S3 rejected). **Decided
2026-10-19: S1** — bindings live on the deployment, environments stay values-only (see
[workspace-overrides.md](workspace-overrides.md), Decision summary). S4 not chosen.

#### Versions under 3b

- Default: the asset's own `location.reference` *is* that customer's version — bump = edit that
  customer's asset file. Per-customer by construction (R2), no version document needed.
- Per ring (dev/qas/prd at different refs): ring version documents pin `pins.assets[c0062-api]`.
  One shared `prd` version document holds every customer's key — no V1 duplication, no V3 slot
  collision (see caveat under V1–V3).

#### Assessment

- **+** R1/R2 met; no `strata.yaml` growth; onboarding = one asset file + one deployment binding.
- **+** Mirrors an existing, accepted shape (`artifact`), reuses `resolve_remote()` end to end.
- **+** Scoped: assets only — does not touch `SourceModel`, provisioners or modules.
- **−** Assets only (Q5): per-customer Terraform/Helm *code* would need the same treatment
  separately (or Option 1).
- **−** Two ways to locate an asset (`remote` vs `location`) — one more either/or rule.
- **−** Needs a per-deployment asset binding (S1 deployment, or S4 environment override) — a new
  concept in its own right.
- **−** Credentials still need the `integration` → `auth` wiring on the read path, unless every
  customer repo is `fetch: external`.

### ~~Option 4 — Templated remote (one declaration, per-deployment URL)~~ — REJECTED

**Decision (2026-10-19):** no templated remotes. A remote stays a fixed, fully-declared
identity; URL/ref are never filled per deployment.

One remote whose `url` (and possibly `reference`) carries `{token}` segments filled from the
deployment's resolved `spec.properties` — the exact mechanism `asset.spec.source_path` already
uses (`path_conventions.fill_pattern()`):

```yaml
remotes:
  - name: customer-api
    type: git
    url: "https://git.example.com/customers/{customer}-api.git"
    reference: "{api_ref}"
    fetch: external
```

- **+** 300 repos → **1** declaration, provided repo URLs follow a convention. Onboarding a
  customer needs no new remote at all — just the tenant's existing `properties`.
- **+** Reuses an existing, tested filling primitive and its validation rules.
- **−** Breaks C3: the checkout key must include the *filled* URL (or the token values), not just
  the remote name.
- **±** C4/R2: one pin key (`customer-api`) for 300 repos is actually fine — each customer's own
  version document pins `remotes.customer-api` to its own ref (V1). Still subject to V1's
  duplication and V2's `fetch: strata` requirement. Alternatively `reference: "{api_ref}"` filled
  from tenant properties sidesteps `kind: version` entirely, at the cost of losing
  `status`/`reason`/`reviewed` pin metadata and promotion visibility.
- **−** Only works when URLs are conventional; an outlier customer needs Option 1/3 alongside.
- **−** Unresolvable at Phase 1 — `strata validate` can only check the template, not each repo.

### ~~Option 5 — Tenant-owned remote~~ — REJECTED

**Decision (2026-10-19):** no customer link on remotes. A remote is not owned by, or resolved
through, a tenant.

`kind: tenant` is already one document per customer (`customers/c0062.yaml`). Let a tenant
declare its own source(s) (`tenant.spec.remotes`), and let a consumer refer to "the deploying
tenant's remote `<name>`" rather than a global name.

- **+** The per-customer list already exists — no new files, no new kind; onboarding a customer
  already means creating the tenant document.
- **+** Per-tenant credentials (C5) attach naturally to the tenant.
- **−** New resolution scope: `RemoteReference()` becomes deployment-context-dependent (which
  tenant is deploying), so it can no longer be a plain Phase 1 name check.
- **−** Mixes identity/residency data with source locations on one kind.

### Option 6 — No strata change: CI checks out into the solution tree

The customer pipeline (or the shared deploy pipeline) checks out the customer repo into a
conventional path (e.g. `customers/{customer}/api/`), and the asset uses **no remote**, only a
templated `source_path`:

```yaml
spec:
  source_path: "customers/{customer}/api/openapi/**/*.json"
```

- **+** Works **today**, zero schema/code change; credentials stay entirely in CI (matches the
  only proven private-repo pattern, `fetch: external`).
- **−** Checkout location is CI convention, invisible to strata (the same class of problem v1's
  `deploy_path` had — mitigated because strata derives the path, not a human-maintained copy).
- **−** Discovery would walk the checked-out customer repo — needs `discovery.exclude`, or
  checkout outside the solution root.
- **−** Fails R2 from strata's side: no ref pinning or provenance — the customer ref lives only
  in CI (V2 forbids pinning `fetch: external`). Acceptable only if CI owns per-customer versioning.

### Option 7 — Single shared registry (publish, don't clone)

Each customer repo's CI publishes its API bundle to one OCI/package registry under a
per-customer path/tag. Strata declares **one** remote and selects per customer via
`source_path` tokens.

- **+** One remote, one credential; immutable versioned bundles.
- **−** Requires OCI remote fetch (not built — [remotes.md](remotes.md) item 5) and a publish step
  in all 300 customer pipelines.

## Related Decisions

- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — solution manifest,
  identity-not-paths; why remotes live on `strata.yaml`.
- [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) — `SourceModel.remote`
  selection side.
- [ADR-0021](../decisions/0021-integration-layer.md) D9 — `"sources"` capability reserved, no ABC.
- [remotes.md](remotes.md) — `resolve_remote()` status, credential/OCI gaps.
- [asset-management.md](../design/asset-management.md) — the consumer this premise starts from.

## Remaining Work / Open Questions

- **Q1 — Are customer repo URLs conventional?** Less relevant now that Option 4 is rejected —
  a per-customer declaration is unavoidable; the question is only *where* it lives
  (Options 1/3), or whether it is avoided by CI/registry (Options 6/7).
- **Q2 — Credentials: uniform or per customer?** One identity with read access to all 300 favours
  6/7; distinct per customer favours 1 with a per-remote `integration`.
- **Q3 — Who fetches?** If the shared pipeline can check out the customer repo itself
  (`fetch: external`), strata never needs credentials — Option 6 is viable today as a stopgap.
- ~~**Q4 — Pinning.**~~ Answered by R2: each customer has its own versions. Remaining sub-question:
  where do they live — per-customer `kind: version` documents (V1/V3 friction), per-customer keys
  in shared ring version documents (no friction, see caveat under V1–V3), or a new layered
  version (platform + customer)? (Tenant `properties` dropped with Options 4/5.)
- **Q6 — Version composition.** Should a deployment be able to combine a shared platform version
  with a per-customer version (e.g. `Deployment.spec.version` as a list, or `Version.extends`)?
  Without it, R2 means duplicating platform pins per customer (V1).
- **Q7 — Promotion per customer.** Does each customer promote independently through rings
  (then V3's slot model needs a customer dimension), or do customers ride the platform's
  rollout with only their API ref varying?
- **Q5 — Scope.** Assets only, or also `SourceModel` (per-customer Terraform/Helm code)? Any change
  at `resolve_remote()` level covers both; a change on `AssetSpecModel` alone does not.
- Options are not exclusive — e.g. Option 1 for the declaration with Option 6 as a stopgap
  until credentials (C5) exist.
- ~~**Q8 — Asset slot binding (Option 3b).**~~ Answered 2026-10-19: S1 —
  `deployment.spec.assets` (list of `{slot, asset}`), same rule as `deployment.spec.providers`
  in [workspace-overrides.md](workspace-overrides.md); reuse via `extends`/`partial`.
- ~~**Q10 — Revive v2 environment `overrides`?**~~ Answered 2026-10-19: no — bindings go on the
  deployment, see [workspace-overrides.md](workspace-overrides.md).
- **Q9 — Option 1 has the same selection gap.** A per-customer `kind: remote` is still named
  statically by an asset (`spec.remote: c0062-api`) — so Option 1 also needs either 300 asset
  documents or a binding like S1. The selection problem is independent of where the location is
  declared.

## Changelog

- 2026-10-19: Created. Premise from the APIM / per-customer API definitions scenario; inventory
  of `RemoteReference()` consumers; seven options with constraints C1–C6.
- 2026-10-19: Added R1/R2 (each customer has its own versions). Verified against source what
  that runs into: one version document per deployment with no layering (V1), pins forbidden on
  `fetch: external` remotes (V2), per-workspace promotion slot uniqueness (V3). Q4 answered;
  Q6/Q7 added.
- 2026-10-19: Rejected Option 4 (no templated remotes) and Option 5 (no customer link on
  remotes). Q1/Q2 updated; remaining candidates: 1, 3, 6, 7 (2 already weak on C1).
- 2026-10-19: Deep dive on Option 3b (self-contained, artifact-shaped asset). Verified call
  sites (`sync_asset()`, `resolve_remote()`, `remote_checkout_path()`, `check_version_pins()`,
  `WorkspaceSpecModel.assets`). Findings: C5 is half-built (`resolve_remote()` already takes
  `auth`, no read-path caller passes it); V1/V3 only bite with one version document per customer
  — per-customer pin keys in shared ring documents avoid both; the real gap is *selection*
  (workspace names assets statically, deployments cannot bind one) — S1 proposed. Q8/Q9 added.
- 2026-10-19: Added S4 (environment override) as a second selection candidate, grounded in v1's
  real `EnvironmentProviderOverrideModel` (same workspace, different provider file per
  deployment) and `EnvironmentRemoteOverrideModel`. Confirmed v2 has no provider/asset override
  today. Q8 reworded (S1 vs S4); Q10 added.
- 2026-10-19: S1 chosen over S4 — bindings on the deployment (`deployment.spec.assets`),
  environments stay values-only; decided in workspace-overrides.md. Q8/Q10 answered.
