# Asset Slots (per-deployment asset binding) — Work

- Status: graduated to [asset-management.md](../design/asset-management.md) — every phase is built
  and no question is open. This file is kept only for its phase-by-phase record and can be deleted.
- Last updated: 2026-10-20

## Overview

A [`workspace`](../config/workspace.md) lists the assets a build may materialise (`spec.assets`, a
pool of `kind: asset` document names) and each execution step selects from that pool
(`step.assets`). Both name the asset *document*, so a workspace shared by many deployments can only
ever use the assets it names: there is no way to say "same workspace, but for this deployment use
that other asset". The motivating case is one APIM workspace deployed for ~300 customers, each with
its own API definitions (see [remotes-at-scale.md](remotes-at-scale.md)).

This applies the rule already decided and built for providers
([provider-slots.md](../design/provider-slots.md), [workspace-overrides.md](workspace-overrides.md)):

- the **workspace** declares named **slots**, each with a default asset document;
- the **deployment** rebinds slots by name (`deployment.spec.assets`);
- **rebind only** (no field patching), reuse through `extends`/`partial`, bindings merge by `slot`.

Scope of this doc is two pieces that the 300-customer premise needs **together**:

1. **Binding** (A1-A6): which asset document fills a workspace slot for a deployment.
2. **Location** (A7): the asset document names its own git repository, instead of reaching it
   through a remote declared in `strata.yaml` (remotes-at-scale.md, Option 3b).

Responsibilities are split deliberately: the asset *definition* documents live in the config repo
(e.g. `config/assets/customers/c0062-api.yaml`); the files they describe live in the customer
repositories. Binding alone is not enough: an asset document today names a manifest `remote`, so
binding by reference would still need one `strata.yaml` entry per customer repository. Location
alone is not enough either, because a shared workspace has no way to choose which customer's
asset it uses. With both, a customer costs two files — its asset document and its deployment
(which already exists) — and `strata.yaml` does not grow.

## Current Design / Progress

### What exists today (verified against source)

| Piece          | Today                                                                                                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Workspace pool | `WorkspaceSpecModel.assets: list[Annotated[PlatformName, References(ASSET)]]`, unique names (`workspace_model.py`)                                                                         |
| Step selection | `ProvisioningStepModel.assets: list[PlatformName]`, unique per step; `validate_execution()` requires each to be in the pool                                                                |
| Build          | `build_run()` collects `{name for step in execution for name in step.assets}`, `sync_asset()` each into `build_path/assets/<name>/`                                                        |
| Projection     | `graph.resolved_assets` → `strata.assets.<name> = {path, files}` (`_strata_assets()`); keyed only by names a step references                                                               |
| Deploy         | rebuilds its own graph, **without** `resolved_assets`                                                                                                                                      |
| Terraform cwd  | `build_path / (target_path or source_path)` of the step's provisioner — a different depth per provisioner; `strata.assets.*.path` is relative to the **build root**, not to this directory |
| Deployment     | no asset field                                                                                                                                                                             |

### Three findings while grounding this design

All three are independent of slots and should be fixed first (Phase 0), because slots make them
worse.

- **D-A — `strata.assets` is lost at deploy time (pre-existing).** `build run` writes
  `strata.auto.tfvars.json` with `strata.assets = {openapi: {path, files}}`; `deploy run` rewrites
  that file from a graph it builds itself (`deploy_controller.py`, `build_resolved_workspace_graph()`
  → `build_strata_payload()`), and that graph has no `resolved_assets`, so the file Terraform
  actually plans and applies against has `assets = {}`. Reproduced end to end with a temporary
  probe: after build `{'openapi': {'path': 'assets/openapi', 'files': ['api.json']}}`, after
  deploy `{}`. Nothing covers it: no deploy-level asset test exists.
- **D-B — the packaged Terraform type does not declare `provider` (my miss from provider slots).**
  `strata_variables.tf` types `providers = map(object({type, region, display_name, …}))` without
  the `provider` attribute that provider slots added to the payload. Terraform silently drops
  attributes absent from an `object` type, so `var.strata.providers.<slot>.provider` is invisible
  to any root using the generated file (the default). The existing
  `test_generated_type_accepts_a_fully_populated_strata_payload` cannot notice: extra attributes
  are accepted, just discarded.
- **D-C — `path` cannot be used from Terraform without hard-coding the depth.** `strata.assets.<name>.path`
  is `assets/<name>`, relative to the build root. Terraform runs with its working directory at the
  step's provisioner directory (`TerraformIntegration` passes `cwd=path`; `path` is
  `build_path / (target_path or source_path)`), so the real location from HCL is `../../assets/<name>`
  for `terraform/apim` and `../assets/<name>` for `infra` — a number of `..` that depends on how
  many segments the provisioner's directory has, which HCL cannot know. The same `path` is also
  broadcast to every step, although two steps can sit at different depths. Today every consumer has
  to hard-code the depth, which is exactly the hard-coded relative path the projection was meant to
  replace.

## Technical Design

### A1 — Workspace: asset slots with a default document

```yaml
kind: workspace
spec:
  assets:
    - slot: api               # stable key — steps select it, IaC addresses strata.assets.api
      asset: api-default      # default Asset document
    - openapi                 # bare name = slot and document share the name
  execution:
    - name: publish_api
      provisioner: terraform_apim
      assets: [api]           # selects slots, exactly as it selects names today
```

`WorkspaceAssetModel {slot, asset}` with bare-string coercion and
`scalar_shorthand_schema(PlatformName)` for the editor schema — the `WorkspaceProviderModel`
pattern. Consequences:

- `spec.assets` becomes `list[WorkspaceAssetModel]`; slots unique (`"asset slots"`); two slots may
  share a document.
- `ProvisioningStepModel.assets` is **unchanged** in shape: its values now name slots. A bare-string
  slot equals its asset name, so every existing workspace and step stays valid and keeps its
  `strata.assets.<name>` keys.
- `validate_execution()` checks `step.assets` against the slot set (message wording unchanged, so
  "references undefined asset 'x'" still reads correctly).
- `References(PlatformKind.ASSET)` moves to `WorkspaceAssetModel.asset`; the reference walker
  recurses, so existence checks and `strata graph` follow (location `spec.assets.N.asset`).

### A2 — Deployment: rebind slots

```yaml
kind: deployment
meta: { name: apim-c0062 }
spec:
  extends: apim-base
  tenant: c0062
  environments: [prd]
  assets:
    - slot: api
      asset: c0062-api        # another kind: asset document
```

`DeploymentAssetBindingModel {slot, asset}` / `DeploymentSpecModel.assets` — slots unique per
deployment, no bare-string shorthand (same reasoning as provider bindings). `_LIST_MERGE_KEYS`
gains `"assets": "slot"`, so a base can bind some slots and a leaf others without dropping any.
A shared base (`partial: true`) covers the "every customer in this region/tier" case; the
per-customer deployment only adds its own binding.

### A3 — Effective binding and build wiring

`effective_assets(workspace, deployment) -> {slot: asset document name}` next to
`effective_providers()` in `services/workspace_service.py`; both use one private overlay helper
(defaults overlaid by deployment bindings, unknown slot ignored there, reported by validation).

`build_run()`:

1. per selected slot, wipe `build_path/assets/<slot>/` immediately before copying into it — never
   the shared `assets/` root (see A3b below);
2. `slots = {slot for step in execution for slot in (step.assets or [])}` (as today, now slots);
3. per slot: look up the effective asset document, `sync_asset(..., slot=slot)`;
4. `resolved_assets[slot] = resolved_asset_info(build_path, slot)`.

**Destination is keyed by slot, not asset name:** `build_path/assets/<slot>/`. The on-disk path is
then stable across rebinds, which is what `strata.assets.<slot>.path` promises IaC (and what any
hand-written relative path would rely on). For a bare-string slot nothing changes. Errors from
`sync_asset()` name the asset and, when different, the slot.

`_strata_assets()` projects `strata.assets.<slot> = {asset, path, path_from_step, files}` —
`asset` is the bound document name, mirroring `strata.providers.<slot>.provider`;
`path_from_step` is A6. The packaged `strata_variables.tf` gains `asset = string` and
`path_from_step = string` in the same changes (Phase 0b makes the parity rule explicit).

### A3b — Layout: per-slot subdirectory (chosen) versus direct copy

`build_path/assets/` is already the one known root; the question is whether each slot gets its own
subdirectory under it.

|                                      | Per-slot subdirectory (**chosen**)             | Direct copy into `build_path/assets/`                         |
| ------------------------------------ | ---------------------------------------------- | ------------------------------------------------------------- |
| Files land at                        | `assets/<slot>/<file>`                         | `assets/<file>`, mirroring the remote's layout                |
| Collisions                           | impossible by construction                     | possible between two assets                                   |
| HCL needs                            | one `path_from_step` per slot                  | one value for the root                                        |
| Deploy: which files belong to a slot | recovered by listing the slot's directory (A4) | not recoverable from disk — needs a manifest written at build |
| Namespacing comes from               | the slot                                       | the customer repository's own folder layout                   |

Chosen: the per-slot subdirectory. It keeps collisions impossible, lets deploy recover each slot's
files from disk with no extra state, and keeps `strata.assets.<slot>.files` meaningful. The
"known root" benefit of direct copy is already delivered by `path_from_step` (A6).

**Stale files — applies to either layout.** Today `sync_asset()` copies with
`dirs_exist_ok=True` and `strata.assets.*.files` is listed from disk, so a rebuild into a custom
`--build-path` (not cleaned by default) keeps files from a previous build and reports them. Rebinding
makes that likelier. The directory for a slot is strata's own output for that slot, so it is wiped
immediately before the slot is copied (step 1 above); from then on every file in it was written by
this run. The shared `assets/` root is **not** wiped: a custom `--build-path` is not cleaned by
default precisely because it "may be pointing somewhere not exclusively owned by this build"
(`build_command.py`), so a user's own `assets/` content there must survive, and directories of slots
no longer selected are simply left alone (they are not projected).

**If direct copy is ever adopted instead** (recorded so the question is not reopened blind):

- Detect collisions **within one run only**, in memory, never by "file already exists on disk" —
  with the wipe above a pre-existing file is not a collision, and without it a stale file would be
  reported as one.
- Same destination from two assets with identical bytes: a no-op. Different bytes: an **error**
  naming both sources. A warning is not enough: the winner would be decided by copy order.
- Deploy needs a manifest (`build_path/assets/.manifest.json`, slot → files) because the slot
  can no longer be read off the directory.
- Reserve `assets/` against a provisioner `target_path`/`source_path` of the same name.

### A4 — Deploy sees the same assets (fixes D-A)

`resolved_asset_info(build_path, slot) -> ResolvedAssetInfo` (in `source_sync.py`) derives `path`
and the recursive, files-only `files` listing **from what is on disk**; `build_run()` and
`deploy_run()` both call it. `deploy_run()` collects the slots selected by **all** of the
workspace's steps (as `build_run()` does: the projection is broadcast, so `--stage`/`--scope` do not
narrow it), but builds `resolved_assets` lazily, inside the per-step block that writes
`strata.auto.tfvars.json` (`integration.ENV_VAR_PREFIX is not None`), with that step's `step_root`
(A6). Container-capable (Helm/Compose) steps and `--smoke-test` never write that file, so they
never require the asset directories. The `asset` name comes from `effective_assets()` of the
resolved deployment, so no manifest file is needed. A selected slot whose directory is missing is
an error telling the user to run `strata build run` first, not a silently empty `strata.assets`.

Known limit, accepted: if a deployment's bindings change between `build run` and `deploy run`, the
directory on disk is stale and deploy cannot tell. That is the existing build/deploy contract for
every rendered artifact; the asset directory is not special.

### A5 — Validation

`_check_deployment_asset_bindings()` beside `_check_deployment_provider_bindings()`, same rules:

| Code                      | Severity | When                                                                                    |
| ------------------------- | -------- | --------------------------------------------------------------------------------------- |
| `asset_slot_unknown`      | error    | the deployment binds a slot its workspace does not declare                              |
| `asset_binding_redundant` | warning  | the binding names the slot's own default (skipped when the raw document uses `extends`) |

No type check: every asset is the same kind. `partial` documents are skipped, location is
`spec.assets`, and the message notes a possibly inherited binding, exactly as for providers.
Existence of the bound asset document is the generic reference check. The shared parts (workspace
lookup, unknown slot, redundant warning, inherited hint) are factored into one helper both checks
call, rather than copied; only the provider type comparison stays provider-specific.

### A6 — A path that works from the step's Terraform directory (fixes D-C, "Option B")

**Working principle: strata mechanisms stay out of Terraform.** The Terraform author should see
plain values and write ordinary HCL. Slots, step roots and relative-path arithmetic are strata's
business, so the consumer interface is one generated local and `path_from_step` is only the
plumbing behind it.

Keep `path` exactly as it is (build-root-relative, unchanged for existing consumers, meant for
tooling rather than HCL) and add `path_from_step` to the payload: the same directory expressed
relative to **the step that reads it**. Strata also generates `local.strata_assets` from it, in
`strata_locals.tf`:

```hcl
locals {
  strata_assets = {
    for slot, a in local.strata.assets : slot => {
      dir   = "${path.root}/${a.path_from_step}"
      files = { for f in a.files : f => "${path.root}/${a.path_from_step}/${f}" }
    }
  }
}
```

What the Terraform author writes (never `path` or `path_from_step`):

```hcl
file(local.strata_assets.api.files["openapi/pets.json"])   # one file
fileset(local.strata_assets.api.dir, "**/*.json")          # or search the directory
abspath(local.strata_assets.api.dir)                       # only when an absolute path is needed
```

**Decision:** `local.strata_assets` is the way of working; `path_from_step` stays in the payload as
its input. Terraform has no user-defined functions, so a generated local is the available
primitive. The values stay relative on purpose: an absolute path inside a resource attribute differs
per machine and shows up as drift (`file()` returns content, so it is safe; a `source = ...` style
attribute is not). A consumer that needs an absolute path wraps it in `abspath()`, so strata
projects no absolute path of its own.

Mechanics, all pure string work — no filesystem and no build root needed inside the projection:

- `ResolvedWorkspaceGraph` gains `step_root: str = "."`: the step's directory relative to the build
  root, POSIX, e.g. `terraform/apim`.
- `_strata_assets(graph)` computes `path_from_step = posixpath.relpath(info.path, start=graph.step_root)`.
- **Build:** `build_run()` already has `source_path` per step; it passes
  `replace(graph, step_root=source_path.relative_to(build_path).as_posix())` to that step's
  `integration.prepare()`. For a source-less (sync/GitOps) provisioner the directory is
  `build_path / step.name`, as the existing code already uses.
- **Deploy:** `deploy_run()` builds `payload` once and filters it per step. It recomputes only the
  `assets` category per step, inside the per-step tfvars block (A4), from
  `replace(graph, step_root=<that step's relative dir>)`, so the payload broadcast stays unchanged
  for every other category.
- Forward slashes are used on every platform; Terraform accepts them on Windows.
- Two steps sharing one provisioner directory get the same value; two at different depths get
  different ones.
- **Generated local.** `build_strata_locals()` appends the `strata_assets` block (static text,
  independent of the secret handling), so it exists exactly when `strata_locals.tf` does. A root
  that declares its own `variable "strata"` skips both generated files and writes its own paths,
  the existing opt-out rule. The `strata_` prefix avoids clashes with a consumer's own locals.

Alternatives considered:

- **Document the convention** (consumers compute the depth). Cheap, but it keeps the fragile
  hard-coded `..` count in every consumer.
- **Project an absolute path into the tfvars at build time.** Simple, but it goes stale when the
  build output is relocated between `build run` and `deploy run` (a build job followed by a deploy
  job on another machine), which the relative form survives.
- **Pass the base path in a `TF_VAR_` environment variable** set by `deploy run` (strata already
  sets `TF_VAR_strata_secrets` this way). Checked against Terraform 1.12.2 on Windows: it works
  for absolute paths with forward slashes or backslashes, with spaces in the path, and for paths
  relative to the working directory. Not chosen because:
  - it only exists when strata runs Terraform — a plain `terraform plan` in the build directory
    fails with `No value for required variable` unless the variable has a default and every
    consumer handles the empty case;
  - Terraform gives `*.auto.tfvars.json` precedence over `TF_VAR_*`, so it could not override a
    field of `var.strata`; it would have to be a second, separate variable outside the one
    projection ADR-0035 establishes;
  - what it buys — an absolute path that survives relocation — is already available by wrapping
    `path_from_step` in `abspath()`.
- **Write an absolute `abs_path` into the tfvars at deploy time** (deploy already rewrites the
  file per step). Relocation-safe, but redundant with `abspath()` above and it would give the
  build-time and deploy-time files different values for the same key.

- **Hand-write the path in each consumer from `path_from_step`.** Works, but it puts the join
  (`path.root`, the slash, the file name) in every Terraform module, which is the strata mechanism
  leaking into HCL that the working principle rules out.
- **A single `strata.root`** (the build root relative to the step, e.g. `../..`), used as
  `"${local.strata.root}/${path}"`. It would also cover other build-root-relative artifacts, but
  it still asks the consumer to do the join. It can be added later beside `strata_assets`.

The env-var and `abs_path` routes can be added later without changing `path_from_step` if a real
consumer needs a value HCL cannot derive.

### A7 — Asset location: the asset document carries its repository (Option 3b)

The definition stays in the config repo; it now says where its files are:

```yaml
kind: asset
meta: { name: c0062-api }
spec:
  location:
    type: git
    url: https://git.example.com/customers/c0062-api.git
    reference: v1.4.0
    fetch: strata                    # or external
  source_path: "openapi/**/*.json"   # still templated and globbable
```

Per customer: this document plus the deployment binding `asset: c0062-api`. 300 customers are 300
small asset documents and the 300 deployments that already exist; `strata.yaml` does not change.
Templated URLs stay rejected (remotes-at-scale.md, Option 4): each document spells its own URL, which
is boilerplate but explicit and easy to grep.

Mechanics:

- **Model.** `AssetSpecModel.location: AssetLocationModel | None`, mutually exclusive with `remote`
  (the `SourceModel.remote`/`chart_repository` either-or shape); both omitted keeps today's meaning,
  this solution's own repository. `AssetLocationModel` is `SolutionRemoteModel` minus `name`, reusing
  its validators (a git `reference` is required; no traversal in it). First version: `type: git`
  only — `resolve_remote()` fetches nothing else.
- **Fetch.** `sync_asset()` builds a `SolutionRemoteModel(name=<asset name>, ...)` from the location
  and calls `resolve_remote()`. That function derives its checkout from the remote's name under
  `.strata/remotes/`, so it gains an optional `checkout_path` override; assets pass
  `layout.asset_checkout_path(root, asset, reference)` = `.strata/assets/<asset name>/<ref>`. An
  asset name can therefore never collide with a manifest remote name. For `fetch: external`, CI must
  place the checkout at exactly that path, and the error message names it.
- **Identity.** An asset's `(kind, name)` is unique, so the checkout key is stable and needs no
  synthetic name. (Reusing an existing checkout without checking its URL is `resolve_remote()`'s
  current behaviour for manifest remotes too: a URL changed at an unchanged ref is not detected.)
- **Versions.** Without a pin, the version is `location.reference` in the asset document: bump it to
  move one customer. For ring-level control, `pins.assets[<asset name>]` overrides
  `location.reference` (`PIN_CATEGORIES` gains `assets`, overlaid like `apply_remote_version_pins()`;
  `check_version_pins()` gets a branch: stale-pin warning when no asset matches, `pin_not_applicable`
  when the asset uses `remote` — pin that remote instead — and an error when `fetch: external`, the
  same rule as remotes). One shared ring `kind: version` can carry a pin per customer, so per-customer
  versions (R2) need neither a version document per customer nor distinct promotion slots.
- **Credentials.** The model carries `integration`, as `SolutionRemoteModel` does, but the first
  version **rejects** it with a validation error rather than ignoring it: `resolve_remote(auth=…)`
  exists, yet no read-path caller passes it (C5). `fetch: external` and ambient git credentials work
  now.
- **Unchanged.** `source_path` templating from `properties`, `strata graph` edges (reference form
  only), the editor schema, and the per-slot destination.

### Binding versus `{name}` templating

`source_path` already templates against the deployment's resolved `properties`
(`assets/{customer}/{deploy_tier}/openapi`). The two mechanisms answer different questions and
compose — a bound asset's `source_path` is still filled from the deployment's properties:

| Need                                                                    | Use                                       |
| ----------------------------------------------------------------------- | ----------------------------------------- |
| Same asset, a different variant per deployment inside one repository    | templating (`{customer}` from properties) |
| A different asset document: other `remote`, other glob, other structure | binding (`deployment.spec.assets`)        |

### Out of scope

- Rebind-only means no field patching of a bound asset (e.g. overriding `source_path` per
  deployment) — as for providers; use templating or bind another document.
- A per-asset `target_path`: the destination stays a fixed convention (Q1).
- Asset locations other than git (OCI, Helm): `resolve_remote()` fetches only git.
- Credentials through `location.integration`: rejected until the read-path auth wiring (C5) exists.
- Rebinding resources or modules — Q6 in workspace-overrides.md.

## Implementation Plan

One phase, one focused change, full check suite after each.

- [x] **Phase 0a — Deploy keeps `strata.assets` (D-A).** `resolved_asset_info()` in
  `source_sync.py`, used by `build_run()` and `deploy_run()`; deploy builds `resolved_assets` from
  `build_path`, lazily inside the tfvars block (A4). `_strata_assets()` became the public
  `build_strata_assets()` so deploy can reuse it. Tests: a build + deploy keeps `strata.assets`
  (the probe, made permanent; verified to fail without the fix); a missing directory is a clear
  error; `--smoke-test` needs no asset directories; `resolved_asset_info()` unit tests.
- [x] **Phase 0b — Template parity (D-B).** Added `provider = string` to the packaged `providers`
  object type. The parity test (`test_packaged_type_declares_every_attribute_the_payload_emits`)
  parses the packaged `variable "strata"` type and compares it with the payload of a fully populated
  graph, in both directions (emitted but undeclared; required but not emitted). Run first, it found
  exactly one drift across all categories: `providers.<slot>.provider`. A second test proves
  the symptom with a real Terraform: `var.strata.providers["p1"].provider` is readable. Both fail
  without the fix. Later phases add `asset`/`path_from_step` to the type and the parity test
  covers them automatically.
- [x] **Phase 0c — Step-relative path (D-C, A6).** `ResolvedWorkspaceGraph.step_root` (default
  `"."`), `path_from_step` in `build_strata_assets()` and the packaged type, `replace(graph,
  step_root=…)` per step in `build_run()` (both the real and the dry-run template call) and
  `deploy_run()`, and the generated `local.strata_assets` appended to `strata_locals.tf` (always
  present, with or without secrets). Tests: the pure projection at four depths; a real build and a
  real deploy with two provisioners at different depths, each asserting
  `(step_dir / path_from_step / files[0])` exists; a real Terraform run from `terraform/apim`
  reading a file with `file(local.strata_assets.api.files["api.json"])` and `fileset(
  local.strata_assets.api.dir, ...)`; the parity test covers the new attribute. Mutating the
  deploy `step_root` makes the deploy tests fail.
- [x] **Phase 1 — Workspace slots (A1).** `WorkspaceAssetModel {slot, asset}` with bare-string
  shorthand and the shared `scalar_shorthand_schema()`; `spec.assets` is a list of them; slots
  unique (`asset slots`); `validate_execution()` checks `step.assets` against slots; the
  `References(ASSET)` moved to `spec.assets[].asset`. `build_run()` resolves each selected slot to
  its default document and, pulled forward from Phase 2 because a slot differing from its document
  would otherwise be broken, materialises it under `assets/<slot>/` (`sync_asset(..., slot=)`; errors
  name the slot when it differs). `config/` and every existing asset test pass unchanged apart from
  the reference path. Tests: slot forms and uniqueness, selection by slot not document, editor
  schema accepts the bare string, a missing default document is an `unknown_reference` at
  `spec.assets.0.asset`, build and deploy with a slot that differs from its document.
- [x] **Phase 2 — Deployment binding (A2, A3).** `DeploymentAssetBindingModel {slot, asset}`,
  `DeploymentSpecModel.assets` (unique slots), `"assets": "slot"` in `_LIST_MERGE_KEYS`,
  `effective_assets()` beside `effective_providers()` (both on one private `_overlay_bindings()`),
  `build_run()` and `deploy_run()` use it. `asset` (the bound document) is now in
  `strata.assets.<slot>` and in the packaged type (Q2 settled in favour); `resolved_asset_info()`
  takes it as an argument because it cannot be read from disk. The per-slot wipe is in
  `sync_asset()`, after the new source is found (a typo does not destroy a good build), limited to
  `assets/<slot>/` and guarded by `resolve_within()`. Tests: two deployments of one workspace get
  different files at the same `strata.assets.api.path`; the binding is inherited through
  `extends`; rebuilding into a reused build path drops the old files and keeps `assets/mine/`;
  model, merge, `effective_assets()` and graph-edge tests; deploy reports the bound document.
  The wipe tests fail with the wipe disabled.
- [x] **Phase 3 — Asset location (A7).** `AssetLocationModel` (git only, `type` defaults to `git`,
  `reference` required and traversal-checked, `integration` rejected with a message), `location`
  XOR `remote` on `AssetSpecModel`, `layout.asset_checkout_path()` (`.strata/assets/<asset>/<ref>`),
  `resolve_remote(checkout_path=…)` (Q7: the parameter, no name tricks), `sync_asset()` building the
  remote from the location. `pins.assets` (Q8: shipped with it): `PIN_CATEGORIES`, the model field,
  `apply_asset_version_pin()` in `build_run()`, a `check_version_pins()` branch (stale-pin warning;
  warning when the asset has no location, pointing at `pins.remotes`; error for `fetch: external`),
  and the `version update`/`scaffold --from` inventory (`fetch: strata` locations only). Tests, with
  real local git repositories: a location asset is cloned at its tag under the asset checkout root and
  not under `.strata/remotes`; two customers get their own files at the same `strata.assets.api.path`
  with no manifest remote; a Version pin moves one customer to another tag; `fetch: external` uses
  the CI-placed checkout and, when missing, names the expected path; the model rules; the pin
  checks; the layout keys. The pin end-to-end test fails with the overlay disabled.
- [x] **Phase 4 — Validation (A5).** `_check_slot_bindings()` in `semantic_checks.py` holds the rules
  both nouns share (`{noun}_slot_unknown` error, `{noun}_binding_redundant` warning, the `extends`
  and `partial` handling); `_check_deployment_provider_bindings()` is now a thin wrapper that adds
  only its `provider_type_mismatch` callback, and `_check_deployment_asset_bindings()` is the other.
  The provider tests pass unchanged. Tests: clean rebinding, unknown slot (also against a workspace
  with no asset slots), redundant, reset to the default via `extends` not flagged, unknown slot
  inherited through `extends` reported with the hint, partial base not checked alone, a missing bound
  document is `unknown_reference` and not a binding finding, provider and asset findings stay
  separate. The new tests fail with the check unwired.
- [x] **Phase 5 — Docs + dogfood.** `docs/config/asset.md` rewritten (slots, `location`, pins, using
  an asset from Terraform, validation); `workspace.md` (asset slots), `deployment.md` (rebinding an
  asset), `version.md` (`pins.assets`); the packaged type's asset fields are commented and the
  Terraform-facing design doc (`unified-terraform-projection.md`) shows only `local.strata_assets`.
  `config/` gets `assets/` with a default asset (from the local `infra` stand-in, so `strata build run
  prd-deployment` stays offline) and two location-based customer assets, the `api` slot selected by
  the step, `prd-c0062`/`prd-c0063` deployments binding them, a `pins.assets` entry, and a
  `local.strata_assets` use in the Terraform stand-in. Tests: the example's three deployments resolve
  to three documents with no manifest remote naming them; the example builds offline and projects the
  default asset; the whole example still validates against its exported editor schemas. The finished
  design is folded into [asset-management.md](../design/asset-management.md) with its rationale as
  History.

## Related Decisions

- [provider-slots.md](../design/provider-slots.md) — the rule this reuses; its `effective_providers`,
  merge-key table, validation shape and `scalar_shorthand_schema()` are the precedents.
- [workspace-overrides.md](workspace-overrides.md) — scope decisions shared by every category.
- [asset-management.md](../design/asset-management.md) — the asset feature this extends (pool,
  step selection, `sync_asset()`, `strata.assets`).
- [remotes-at-scale.md](remotes-at-scale.md) — the 300-customer premise; Option 3b is A7 here.
- [remotes.md](remotes.md) — `resolve_remote()`, checkout layout and the credential gap (C5).
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — identity is
  `(kind, meta.name)`; why a rebind needs a stable slot name.

## Remaining Work / Open Questions

- ~~**Q1 — Destination keyed by slot.**~~ Answered 2026-10-20: per-slot subdirectory
  `build_path/assets/<slot>/` (stable across rebinds, unchanged for bare-string slots); direct copy
  into the shared root recorded under A3b with its collision rule. The destination stays a
  fixed convention either way: assets deliberately have no `target_path` (unlike a provisioner
  `source`), and a per-asset one would put the path back into each customer's asset file, so it
  would change per customer.
- ~~**Q2 — `asset` field in `strata.assets.<slot>`.**~~ Answered in Phase 2: included, consistent
  with `providers.<slot>.provider`; the parity test keeps the packaged type in step.
- ~~**Q3 — Build/deploy skew.**~~ Answered in Phase 5: accepted and documented (the existing
  build/deploy contract for every rendered artifact); no marker file.
- ~~**Q4 — Shared validation helper.**~~ Answered in Phase 4: factored now, with an `on_rebound`
  callback for the one provider-only check.
- ~~**Q5 — Deploy-time missing directory.**~~ Answered: an error telling the user to run `build run`
  (A4); "silently empty, as today" was the D-A bug, not a design.
- ~~**Q6 — Name of the step-relative field.**~~ Answered in Phase 5: `path_from_step`; Terraform
  authors read `local.strata_assets` and never type it.
- ~~**Q7 — Checkout override mechanism.**~~ Answered in Phase 3: `resolve_remote(checkout_path=…)`.
- ~~**Q8 — `pins.assets` in the first release of A7.**~~ Answered in Phase 3: shipped with it.

## Changelog

- 2026-10-19: Created. Design for deployment-level asset binding on the provider-slots pattern.
  Three defects found while grounding it, recorded as Phase 0: `strata.assets` is lost at deploy
  time (reproduced), the packaged Terraform type omits the `provider` attribute provider slots
  added, and `path` is relative to the build root rather than the step directory (added below).
- 2026-10-19: Added the step-relative path (A6, "Option B", Phase 0c): `path` is build-root-relative
  while Terraform runs in the step's directory, so consumers had to hard-code the depth. Adds
  `path_from_step`, computed purely from a per-step `step_root`; absolute paths and a documented
  convention considered and not chosen. Q6 (field name) added.
- 2026-10-20: Layout decided: per-slot subdirectory under the single `build_path/assets/` root, with
  a wipe of that directory before each asset sync (Q1 closed). Direct copy into the shared root
  recorded with its collision rule. A6 decision recorded: `path_from_step` is the main mechanism;
  absolute paths via HCL `abspath()`. `TF_VAR_` env var and deploy-time `abs_path` evaluated
  (the env var verified against Terraform 1.12.2) and not chosen.
- 2026-10-20: Review of the whole design. R1: binding by reference alone leaves one manifest remote
  per customer repository, so the premise needs binding **and** location — A7 (asset documents in
  the config repo carry their own git `location`) added; an inline-on-deployment alternative was
  considered and dropped (needs an either-or binding, a replace-by-key merge mode and synthetic
  checkout names, and cannot be pinned). R2: the wipe was too broad for a custom `--build-path`;
  now per slot. R3: deploy collects slots for all steps, lazily inside the tfvars block. R4: the
  parity test covers every category. Plan renumbered (new Phase 3, validation and docs moved to 4
  and 5); Q5 closed, Q7 and Q8 added.
- 2026-10-20: Working principle recorded in A6: strata mechanisms stay out of Terraform. The
  consumer interface is a generated `local.strata_assets.<slot>.{dir,files}` in `strata_locals.tf`;
  `path_from_step` is demoted to the payload input behind it (Phase 0c, Phase 5 docs and Q6
  updated). Relative values kept on purpose; `abspath()` left to the caller.
- 2026-10-20: Phase 0a built (D-A fixed). Deploy rebuilds `strata.assets` from the asset
  directories on disk; 2709 tests pass.
- 2026-10-20: Phase 0b built (D-B fixed). The parity test found no drift beyond `provider`;
  2712 tests pass.
- 2026-10-20: Phase 0c built (D-C fixed, `local.strata_assets` generated). A root with its own
  `variable "strata"` keeps skipping both generated files (existing test). 2720 tests pass.
- 2026-10-20: Phase 1 built. Workspace asset slots; the slot-keyed destination moved here from
  Phase 2 so a slot that differs from its document works end to end. 2732 tests pass.
- 2026-10-20: Phase 2 built. Deployment asset bindings, `effective_assets()`, per-slot wipe,
  `asset` in `strata.assets` (Q2 closed). 2747 tests pass.
- 2026-10-20: Phase 3 built. Asset `location`, `pins.assets`, checkout under `.strata/assets/`
  (Q7 and Q8 closed). A customer is now an asset document plus a deployment; `strata.yaml` is
  untouched. 2778 tests pass.
- 2026-10-20: Phase 4 built. `asset_slot_unknown` and `asset_binding_redundant`, on a helper shared
  with the provider check (Q4 closed). 2787 tests pass.
- 2026-10-20: Phase 5 built; Q3 and Q6 closed. User docs, the commented type, the dogfood example and
  the fold into `docs/design/asset-management.md`. Graduated. 2789 tests pass.
