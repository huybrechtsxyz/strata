# Asset Management (non-code, per-environment input files) — Design

- Status: current
- Last updated: 2026-10-20

## Overview

A provisioner sometimes needs **plain input files that are not IaC code** —
an OpenAPI contract (`.json`/`.xml`), a set of images, a data/seed file, a
certificate bundle — and the *right* file differs per environment (dev/
qas/prod each have their own `openapi.json`). None of this belongs inside
the provisioner's own source tree (e.g. a `terraform/` directory): that
tree is normal IaC code, usually committed and reviewed as code, and
mixing in binary/data files pollutes history and review diffs; the *same*
`.tf`/Ansible code must also run unmodified against dev/qas/prod, so the
file cannot be hardcoded as a sibling file next to the `.tf` code either.

A standalone `kind: asset` document fills this gap: a named, reusable
file-set location whose `source_path` templates against a deployment's own
resolved `properties` at build time, materialised into the build output by
`strata build run` before any execution step runs, and projected into
`var.strata` alongside every other document category.

Two later additions extend the same feature to one workspace serving many
customers, each with their own files. A workspace's assets are **named
slots** with a default document, and a deployment rebinds a slot to another
Asset document (the rule [provider-slots.md](provider-slots.md) already
established for providers). An asset document can also carry its own git
`location`, so a customer's files can live in the customer's repository
without a `remotes:` entry in `strata.yaml` per customer. A customer is then
one small asset document plus one deployment.

## Current Design

### Schema (`kind: asset`)

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: openapi
spec:
  description: "OpenAPI contract files for the Contoso API Gateway"
  remote: null                                        # SourceModel's own field/meaning, reused unchanged
  source_path: "assets/contoso/{deploy_tier}/openapi"  # {deploy_tier} filled from graph.properties at build time
```

`AssetSpecModel` (`asset_model.py`) deliberately re-declares just
`remote`/`source_path` rather than building on `SourceModel` — `SourceModel`
also carries Helm chart-registry mode (`chart_name`/`chart_version`/
`chart_repository`) and `target_path`, none of which apply to a plain file
copy, and inheriting them would also inherit `validate_source_mode()`'s
git-vs-chart check, meaningless here. `remote` omitted means this
solution's own repository, matching `SourceModel`'s own convention.

`source_path` may contain `{name}`-style segment tokens — the same token
syntax `path_conventions.compile_pattern()` uses for *matching* a directory
convention, reused here for the opposite direction, *filling*
(`path_conventions.fill_pattern()`). No fixed vocabulary for the token
name: `{deploy_tier}` is just whatever `properties` key a workspace already
uses (the same opaque-value precedent
[ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md)
already set for `deploy_tier` itself), `{ring}`/`{region}`/`{customer}`/
anything else works identically, and more than one token can appear in the
same template (e.g. `assets/{customer}/{region}/{deploy_tier}/openapi`),
each filled from a different layer of the same already-merged `properties`
dict. A value filling a token must be a `str`/`int`/`float` — a
`dict`/`list`/`bool` would stringify into a nonsense path segment
(`assets/True/openapi`), so it is rejected up front with a clear
diagnostic rather than failing later as "path does not exist".

`source_path` **may also be a glob** — `*`, `**`, `?`, `[abc]` — so the
author selects exactly which files they want rather than strata inferring
it: `assets/{deploy_tier}/openapi/*.json` takes only the JSON contracts,
`.../**/*.json` takes them recursively. A `source_path` with no wildcard
names one file or directory, exactly as before. Shell brace expansion
(`{a,b}`) is **not** supported — `pathlib.glob()` has no equivalent, and
`{...}` is already strata's own segment-token syntax; the two never
collide, since a token name is `\w+` and so can never contain a comma.

`source_path` must be relative. An absolute path or one containing `..`
is rejected at document-load time by the same `validate_relative_path()`
guard `SourceModel.source_path` already uses — `remote` (or `location`, below)
is how an asset points outside this solution's own repository.

### Workspace wiring: slots + per-step selection

`WorkspaceSpecModel.assets` (the slots) and `ProvisioningStepModel.assets`
(the per-step selection) follow the two-layer pattern
`namespaces`/`firewalls`/`dns_zones`/`networks` (pool) +
`ProvisioningStepModel.targets` (selection) already establish — a
workspace declares *what it may use*, a step declares *what it,
specifically, needs this run* (same-document existence, checked by a
second, independent `asset_slots` set in
`WorkspaceSpecModel.validate_execution()`, deliberately not merged into
the existing `target_names` set — an asset is materialised input a step
consumes, not a provisioning target a step acts on).

Each `spec.assets[]` entry is a `WorkspaceAssetModel {slot, asset}`: `slot`
is the stable key a step selects and IaC addresses, `asset` the document
bound by default. A bare string is shorthand for a slot named after its own
document (a `mode="before"` coercion plus the shared
`scalar_shorthand_schema()` so the editor schema accepts it), which keeps
every workspace written before slots existed valid and its
`strata.assets.<name>` keys unchanged. `References(PlatformKind.ASSET)` sits
on `asset`, so existence checks and `strata graph` edges follow
(`spec.assets.N.asset`). A step selects a **slot**, never a document name:

```yaml
spec:
  assets:
    - slot: api
      asset: api-default
    - openapi                # bare name = slot and document share the name
  execution:
    - name: publish_api
      provisioner: terraform_apim
      resources: [apim]
      assets: [api]
```

### Build-time sync (`sync_asset()`)

`build_controller.py`'s `build_run()` statically scans every
`step.assets` across `workspace.spec.execution` into one `set[str]` of
slots genuinely needed by *this* build, resolves each slot to its
bound Asset document (`effective_assets()`, below), and resolves each exactly
once via `sync_asset()` (`source_sync.py`) — right after `remotes` are
assembled, before the provisioning step loop starts, the same timing
`properties`/`graph` already have. This is deliberately *not* lazy
per-step: a step naming the same asset as another step would otherwise
need an idempotency rule, and computing `strata.assets` (below) needs the
copy to have already happened before the step loop begins anyway.

`sync_asset()` is structurally parallel to `sync_source()`: it resolves
`remote` via `resolve_remote()`, copies the result (file or directory)
into the build output, and raises `SourceSyncError` on any failure — but
with three differences from `sync_source()`:

- `source_path` is templated via `fill_pattern()` first, not used
  literally. Before filling, every `{name}` token in the template is
  checked against its corresponding `properties` value with
  `has_value_tokens()` — if that value still contains an unresolved
  `${...}` Value token (which only ever resolves at deploy time, per
  [value-token-resolution.md](value-token-resolution.md)), `sync_asset()`
  raises a dedicated diagnostic immediately, rather than filling the
  literal unresolved string into the path and failing later with a
  confusing "path does not exist" error. A `{name}` token with no
  matching `properties` key at all is likewise a clear `SourceSyncError`
  (`fill_pattern()`'s own `ValueError`, re-wrapped) — never a silent
  empty-string substitution.
- **The filled path may be a glob**, in which case every match is copied
  **preserving its path relative to the pattern's last wildcard-free
  directory** — `assets/openapi/**/*.json` lands
  `assets/openapi/nested/deep.json` at `assets/<name>/nested/deep.json`,
  never flattened onto a same-named sibling. A pattern matching nothing
  is a clear error, not a silently empty asset.
- **Every resolved path is checked back against the resolved remote root**
  (`path_safety.resolve_within()`). A declared `source_path` is already
  validated as relative at document-load time, but that cannot cover a
  path only assembled at build time: a `{name}` token filled with
  `../../..` (or an absolute value), or a glob match the pattern never
  named literally. Both are rejected before anything is copied.
- The destination is always `build_path/assets/<slot>/` — keyed by
  the workspace slot only, **never** path-mirrored the way `sync_source()`
  preserves a provisioner's own relative repo layout (that convention
  exists solely to keep sibling Terraform modules' relative
  `source = "../.."`-style references working; an asset has no such
  sibling-composition need, and only one variant is ever resolved per
  build, so there's no collision to avoid either). Keying by slot rather
  than by document keeps the on-disk path stable when a deployment rebinds
  the slot. A single-file source
  still lands inside this directory (preserving its own filename), so the
  destination is always a directory regardless of whether the source was
  one file or a tree — matching ADR-0025's rule that strata supplies
  input, never rewrites or templates the consumer's code to discover it
  by some other means.
- **The slot's own directory is cleared immediately before copying** (never the
  shared `assets/` root). `strata.assets.*.files` is listed from disk, and a custom
  `--build-path` is not cleaned by default, so without this a rebuild after a
  rebind would keep, and report, the previous document's files. The clear runs
  only after the new source was found, so a mistyped path does not destroy a
  good earlier build, and it is guarded by `resolve_within()`. Sibling
  directories under `assets/` are left alone: a custom build path may hold
  content this build does not own.

### `strata.assets` projection

`ResolvedAssetInfo` (`resolved_context.py`, alongside `ResolvedModule`) —
a plain `path`/`asset`/`files` triple — is populated by the same asset-sync loop
above through `resolved_asset_info()` (`source_sync.py`), which lists the
slot directory's sorted filenames **from what is on disk** and stores the result in a new
`ResolvedWorkspaceGraph.resolved_assets: dict[str, ResolvedAssetInfo]`
field. Since `ResolvedWorkspaceGraph` is frozen (the same immutable
controller-resolves/integration-only-reads split
[ADR-0021](../decisions/0021-integration-layer.md) D2 establishes for
every other resolved type) and is already assembled before the asset-sync
loop runs, `build_controller.py` replaces it once via `dataclasses.replace()`
right before the provisioning step loop, so every
`integration.prepare(..., graph=graph, ...)` call downstream sees the
populated field. Skipped entirely on `dry_run` — nothing is actually
copied, so there is nothing real to list.

`build_strata_assets()` (`terraform_projection.py`) projects this into
`var.strata` as a sixteenth key (`"assets": build_strata_assets(graph)`),
alongside the other fifteen `build_strata_payload()` already produced
(`workspace`, `tenant`, `providers`, `resources`, `namespaces`, `modules`,
`dns`, `networks`, `firewalls`, `topologies`, `properties`, `custom`,
`flags`, `variables`, `default_tags`) — matching
[ADR-0035](../decisions/0035-unified-terraform-projection.md)'s "every
category always present, empty when unused" rule in every way but one:
`strata.assets` is **deliberately** keyed only by slots at least one
execution step's `step.assets` actually selects in *this* build, not
the full `workspace.spec.assets` list — unlike `namespaces`/`firewalls`/
`dns_zones`/`networks` (pure YAML data, always safe to project
unconditionally), an asset entry requires real filesystem resolution, and
eagerly resolving every declared-but-unreferenced pool entry would turn a
harmless, currently-unused pool declaration into a hard build failure the
moment that entry's variant doesn't exist on disk yet. A provisioner reads
the result as an ordinary Terraform value instead of globbing a hardcoded
relative path. Each entry is keyed by slot:

```json
"api": {
  "asset": "c0062-api",                   // the document bound to the slot for this deployment
  "path": "assets/api",                   // relative to the build root, for tooling
  "path_from_step": "../../assets/api",   // the same directory, relative to the step's Terraform root
  "files": ["openapi.json", "nested/deep.json"]
}
```

`files` is a **recursive, files-only** listing of what actually landed,
each entry relative to `path` — so it stays correct for a glob that
preserved nested structure, and never reports a directory name as though
it were a file.

`strata_variables.tf`'s generated type carries the matching
`assets = map(object({ asset, path, path_from_step, files }))` entry,
alongside `modules`, commented per field. Nothing secret-shaped here (a filename is not a
value needing `strata_secrets` treatment), and nothing is written into
`resolved.yaml` for this — `strata.assets` is additive, read-only
metadata about the files in the build output.

### Reading assets from Terraform

`path` is relative to the **build root**, but Terraform runs with its working
directory at the step's provisioner directory, a different depth per provisioner
(`terraform/apim` is two levels down, `infra` one). Reading `path` from HCL would
mean hard-coding that depth, the very thing the projection exists to avoid, and
`strata.assets` is broadcast to every step even though two steps can sit at
different depths.

So the projection also carries `path_from_step`, computed per step from
`ResolvedWorkspaceGraph.step_root` (the step's directory relative to the build
root) with `posixpath.relpath()` — pure string arithmetic, no filesystem. `build_run()`
and `deploy_run()` each pass their own `step_root` per step (`dataclasses.replace()`).

**Working principle: strata mechanisms stay out of Terraform.** An author should
write ordinary HCL against plain values, so the interface is a generated local,
not the raw fields. `strata_locals.tf` (alongside `local.strata`) defines
`local.strata_assets.<slot>` with `dir` and a `files` map, both already joined to
`path.root`:

```hcl
file(local.strata_assets.api.files["openapi.json"])
fileset(local.strata_assets.api.dir, "**/*.json")
```

The same code works for every customer. Terraform has no user-defined functions, so a
generated local is the available primitive. The values stay **relative** on purpose:
an absolute path inside a resource attribute differs per machine and shows up as
drift (`file()` returns content, so it is safe; a `source = ...` style attribute is
not), and a consumer that needs one wraps it in `abspath()`. A root that declares its
own `variable "strata"` skips both generated files and writes its own paths, the
existing opt-out rule.

### Per-deployment binding (asset slots)

A deployment rebinds a slot with `spec.assets: [{slot, asset}]`
(`DeploymentAssetBindingModel`), the rule provider slots already set
([provider-slots.md](provider-slots.md), [workspace-overrides](../work/workspace-overrides.md)):
the workspace declares slots with defaults, the deployment rebinds by name,
**rebind only** (no field patching; use `{name}` templating for a variant inside
one repository, a different document for a different structure), and reuse goes
through `extends`/`partial`. The deployment, not the environment, carries it: it is
a document selection like `workspace:` or `version:`.

```yaml
kind: deployment
meta: { name: prd-c0062-deployment }
spec:
  extends: deploy-base
  assets:
    - slot: api
      asset: c0062-api
```

`merge_deployment_specs()` merges `assets` by `slot` through the same merge-key table
as `stages` and `providers`. `effective_assets(workspace, deployment) -> {slot:
document}` (`workspace_service.py`) overlays the bindings on the workspace defaults,
on the same private helper as `effective_providers()`; a binding for an undeclared
slot is ignored there and reported by validation.

`deploy run` rebuilds its own graph, so it must not lose what `build run` projected
(it did, once: `strata.assets` was `{}` at deploy time, so Terraform planned against
an empty map). It rebuilds `resolved_assets` from the directories on disk, using
`effective_assets()` for the `asset` name, lazily inside the per-step block that
writes `strata.auto.tfvars.json` with that step's own `step_root`. Container steps
(Helm/Compose) and `--smoke-test` never write that file, so they never require the
directories; a missing one is an error telling the user to run `strata build run`,
not a silently empty projection. Known limit, accepted: if a deployment's bindings
change between `build run` and `deploy run`, the directory on disk is stale and deploy
cannot tell — the existing build/deploy contract for every rendered artifact.

### Asset location (`spec.location`)

Binding by reference alone leaves one `remotes:` entry in `strata.yaml` per customer
repository; location alone gives a shared workspace no way to choose which customer's
asset it uses. Together they make a customer one asset document (in the config
repository) plus one deployment:

```yaml
kind: asset
meta: { name: c0062-api }
spec:
  location:
    type: git
    url: https://git.example.com/customers/c0062-api.git
    reference: v1.4.0
  source_path: openapi
```

`AssetLocationModel` is a `SolutionRemoteModel` minus `name` (the asset's name is the
identity), minus what the first version cannot honour: only `type: git`
(`resolve_remote()` fetches nothing else), `reference` required and traversal-checked,
and `integration` for credentials (below). `location` and `remote` are mutually exclusive.

`sync_asset()` builds a `SolutionRemoteModel` named after the asset from the location and
calls `resolve_remote(..., checkout_path=layout.asset_checkout_path(...))`, so the
checkout is `.strata/assets/<asset name>/<ref>`, a separate root from
`.strata/remotes/`, and an asset name can never collide with a manifest remote name. For
`fetch: external`, CI places the checkout at exactly that path and the error names it.
(An existing checkout is reused without checking its URL, as for manifest remotes: a URL
changed at an unchanged ref is not detected.)

**Versions.** Without a pin the version is `location.reference` in the asset document.
`pins.assets[<asset name>]` in a `kind: version` document overrides it
(`apply_asset_version_pin()` in `build_run()`), so one shared ring document can carry a
pin per customer. `check_version_pins()` warns on a pin matching no asset, warns when the
asset has no `location` (pin its remote under `pins.remotes` instead), and errors for a
`fetch: external` location, the same rule as remotes. `version update` and `scaffold
--from` include `fetch: strata` locations in their inventory.

Templated URLs stay rejected: each customer document spells its own URL, which is
boilerplate but explicit and easy to grep.

**Credentials.** `location.integration` (and a manifest remote's `integration`) names a
`kind: integration` document with the `sources` capability whose `spec.authentication`
(`token` or `ssh_key`) names keys of `secrets` the deployment reaches. One mechanism serves
every build-time clone (assets, provisioner sources and their dependencies, workload
modules):

- `RemoteCredentialResolver` (`controllers/remote_credentials.py`) is created once per
  `build_run()` and passed down to `sync_asset()`, `sync_source()` and `sync_module_source()`.
  `resolve_remote()` asks it through a callable only when a clone is about to run, so
  `--dry-run`, an existing checkout, `fetch: external` and an unset integration read
  nothing. The result is cached per integration.
- It resolves only the keys the authentication references (`git_push.credential_keys()`,
  next to `prepare_git_credentials()` which consumes them, with a test that keeps the two in
  step). Without `--resolve` each key must use a store `build run` may read without a
  network call (`value_controller.is_direct_store()`: constant, environment, GitHub,
  artifact); a Key Vault or Infisical-backed token needs `--resolve`.
- It is strict: an unknown or disabled integration, one without `sources`, an authentication
  method git cannot use (`oauth2`, ...), an undeclared or unresolvable key, and a declared
  `known_hosts` that does not resolve (never trust-on-first-use) are all a
  `RemoteCredentialError` (a `SystemError`, exit 1) naming the asset or remote, before any
  `git` call.
- The value reaches git only through the subprocess environment (a temporary `GIT_ASKPASS`
  helper or a `0600` key file, removed afterwards), never argv, `resolved.yaml`, the tfvars
  output or the SBOM. A failed clone's error text has every resolved value of six or more
  characters replaced by `***`.
- A `url` with a password, or an http(s) username that looks like a token, is rejected on both
  models (`validate_no_url_credentials()`, a heuristic for the token form: known prefixes or 32+
  token characters); `git@host:org/repo.git`, `ssh://git@host/...` and an ordinary `user@` pass.

Per-customer credentials are per-integration: a shared identity is one integration document
named by many assets; a distinct identity is one small integration document each. Templating
the key references per customer is not built, pending evidence it is needed. See
[integrations/git.md](../integrations/git.md) for the user-facing guide and
[remotes-at-scale.md](../work/remotes-at-scale.md) for how it was designed.

### Validation

`_check_slot_bindings()` (`semantic_checks.py`) holds the rules provider and asset
bindings share; each noun is a thin wrapper, and the provider's `provider_type_mismatch`
is an `on_rebound` callback because only providers have a type to compare.

| Code                                 | Severity | When                                                                                         |
| ------------------------------------ | -------- | -------------------------------------------------------------------------------------------- |
| `asset_slot_unknown`                 | error    | the deployment binds a slot its workspace does not declare                                   |
| `asset_binding_redundant`            | warning  | the binding names the slot's own default (skipped when the raw document uses `extends`)      |
| `asset_integration_not_sources`      | error    | `location.integration` names an integration without the `sources` capability                 |
| `asset_integration_disabled`         | error    | `location.integration` names a disabled integration and `fetch` is `strata`                  |
| `asset_integration_auth_unsupported` | error    | the integration's authentication method cannot authenticate git (`fetch: strata`)            |
| `fetch_credential_key_undeclared`    | error    | a deployment clones this asset with an integration whose key its environments do not declare |
| `remote_integration_ignored`         | warning  | `location.integration` is set with `fetch: external` (nothing is cloned)                     |

A `partial` document is judged only once merged into the deployment that extends it, and
existence of the bound document is the generic reference check's job. The same goes for
`location.integration`: a missing document is the generic `unknown_reference`. The matching
check for a manifest remote's `integration` (`remote_integration_unknown`,
`remote_integration_not_sources`, plus `remote_integration_disabled` and
`remote_integration_auth_unsupported` as warnings for a git remote, since GitOps and audit pushes also use it;
for a `helm`/`oci` remote they are errors, plus `remote_integration_username_required`)
lives in `_check_remote_integrations()`. `fetch_credential_key_undeclared` is checked per
deployment, against the assets its workspace steps select.

### Deliberately out of scope

- **Rebinding resources or modules**, and field patching of a bound asset —
  rebind only, as for providers; see
  [workspace-overrides.md](../work/workspace-overrides.md).
- **Asset locations other than git** (OCI, Helm): `resolve_remote()` fetches only git.
- **Per-customer templating of credential key references** — not built; a distinct identity
  is one integration document each, until there is evidence that is too many.
- **A per-asset `target_path`**: the destination stays a fixed convention, since it
  would put a path back into every customer's asset file.
- **Structural validation of a variant's on-disk shape** (e.g. "must
  contain an `openapi.json`") — no evidenced need; a provisioner's own
  code is the only thing that knows what it expects to find.
- **A missing on-disk `source_path` (after filling) as a `strata validate`
  Phase 2 warning, in addition to the build-time `SourceSyncError`** — not
  pursued; Phase 2 does no real filesystem check for any other kind
  today, so this would be a new, kind-specific exception rather than
  applying an existing pattern.
- **`DeploymentLayersModel.segments` as a second, merged-in source of
  fill values alongside `properties`** — not pursued; no real workspace
  needed it, `properties` alone covered every real case checked.

## Related Decisions

- [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) —
  `WorkspaceSpecModel`'s pool-of-names pattern, the base the slots extend.
- [provider-slots.md](provider-slots.md) — the slot-and-rebind rule asset slots reuse:
  `effective_*()`, the merge-key table, the shorthand schema helper, the validation shape.
- [workspace-overrides.md](../work/workspace-overrides.md) — why rebinding lives on the
  deployment, and the rule shared by every category.
- [remotes-at-scale.md](../work/remotes-at-scale.md) — the 300-customer premise behind
  `location`.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  `ProvisioningStepModel.targets`, the precedent `ProvisioningStepModel
  .assets`'s own cross-check in `validate_execution()` mirrors directly.
- [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) —
  `SourceModel`'s field shape, reused here.
- [ADR-0022](../decisions/0022-strata-build-run.md) /
  [build-command.md](../work/build-command.md) — the build pipeline this
  plugs into (`sync_source()`, the step loop).
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) —
  why assets are copied byte-for-byte, never templated, and why the
  destination is a documented convention rather than a new indirection
  token.
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) /
  [artifact-references.md](../archive/artifact-references.md) — the
  adjacent, already-built "named external reference" kind, and why its
  shape doesn't cover this gap (strata never fetches an artifact; it must
  always fetch an asset).
- [ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md) /
  [deployment-tier-resource-sizing.md](deployment-tier-resource-sizing.md) —
  the precedent this design's `{name}`-from-`properties` templating
  follows directly: no schema, no fixed vocabulary, an opaque
  deployment-resolved value.
- [value-token-resolution.md](value-token-resolution.md) — why Mechanism
  B (`${var:}`-style embedded tokens) is deploy-time-only by design, and
  therefore not the right tool for a build-time file copy.
- [path-conventions.md](path-conventions.md) — `compile_pattern()`'s
  `{name}` token syntax, reused here for filling rather than matching.
- [remotes.md](../work/remotes.md) — `resolve_remote()`, the prerequisite
  this design reuses unchanged.
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) /
  [unified-terraform-projection.md](unified-terraform-projection.md) —
  the finished `var.strata` projection; `strata.assets` is a sixteenth
  category following that design's own established shape.

## History

- The first draft keyed a variant off a real `Environment` document
  identity (`References(PlatformKind.ENVIRONMENT)`). Rejected: ADR-0034
  already settled the identical "needs to vary dev/qas/prod" problem with
  an opaque `properties` value, deliberately decoupled from which
  `Environment` document is attached; this repo's own fixture doesn't
  even hold up against a hard identity reference (`config/environments/`
  declares `prd`, not `prod`); and a deployment reachable to several
  `Environment` documents at once has no principled answer for "which one
  names the variant." Checking other systems (Terraform workspaces,
  Kustomize overlays, Helm `-f` filename convention, CI `environment:`
  labels) confirmed the common pattern: the selector is always a free,
  opaque label resolved by simple equality, never a validated identity
  reference — reinforcing the `properties`-templating direction actually
  taken.
- Embedding `${var:KEY}`/Mechanism-B tokens directly in `source_path`,
  resolved at build time, was also rejected: that mechanism is
  deploy-time-only by deliberate, already-settled cross-cutting design
  ([value-token-resolution.md](value-token-resolution.md)); making an
  exception for this one new field would reopen a question that doc
  already closed for consistency's sake.
- The asset reference was originally sketched on `ProvisionerModel`, then
  moved to `WorkspaceSpecModel.assets` (pool) + `ProvisioningStepModel
  .assets` (selection) after noticing `WorkspaceSpecModel` already has an
  established, repeated pool-then-select pattern for exactly this shape
  — independently reinforced the same day by
  [workspace-execution-model.md](workspace-execution-model.md)
  arriving at the identical shape for an unrelated `targets`-splitting
  decision.
- Whether `properties` really is resolved by the time a step copies files
  was checked directly against `build_controller.py`'s real `build_run()`
  rather than assumed — confirmed the merge and graph assembly both run
  strictly before the step loop. That same check surfaced one real edge
  case: a `${var:}` token embedded *inside* a `properties` value (always
  deploy-time-only) would otherwise fail obscurely rather than with a
  clear diagnostic — closed by the dedicated `has_value_tokens()` check
  `sync_asset()` now runs before filling each segment.
- The `strata.assets` projection was added after noticing `sync_asset()`
  and `build_strata_payload()` were designed completely unconnected — a
  provisioner could reach the copied files only by hardcoding the
  destination convention's relative path, with no way to know which files
  actually landed there short of its own glob. Adding `ResolvedAssetInfo`/
  `resolved_assets`/`_strata_assets()` following the `ResolvedModule
  .source_path` precedent (orchestrator resolves, the Integration only
  ever reads already-resolved plain data) forced a real revision to the
  sync mechanism: computing `strata.assets` needs the copy to have
  already happened before the step loop starts, so `sync_asset()` is
  called once per statically-scanned name up front rather than lazily
  per `step.assets` entry inside the loop — which also deleted the
  earlier two-steps-naming-the-same-asset idempotency concern
  structurally, by construction, rather than by patching it.
- Whether a missing `{name}` token should be a hard error always, or only
  when the asset is actually reachable from the deployment being built,
  resolved itself once `strata.assets`' own "keyed only by what's
  referenced" scoping was settled: only assets a build's execution steps
  actually reference are ever resolved at all, so an unreachable pool
  entry's missing token/variant never surfaces as a failure in the first
  place — there was no separate "defensive-only" case left to design.
- Glob support replaced an open question this design had answered badly —
  whether `files` should be a shallow or a recursive listing — by removing
  the question: once the author states which files they want, strata has
  nothing left to infer. (The shallow `iterdir()` listing it replaced also
  reported directory names as though they were files.) Verified before
  committing to it: glob metacharacters and the existing `{name}` tokens
  compose without collision — a token name is `\w+`, so brace expansion can
  never be mistaken for one, though it isn't *supported* either, since
  `pathlib.glob()` has no equivalent — and a wildcard-free `source_path`
  still resolves exactly as before, so no existing asset document changed
  meaning. Flattening every match into one directory was the alternative to
  preserving structure, and was rejected: two same-named files from
  different directories would collide silently.
- The build-time containment check (`path_safety.resolve_within()`) was put
  in `path_safety.py` rather than `layout.py`, despite `layout.py` claiming
  ownership of *every* derived path ("nothing outside this module should
  join a path segment") and `sync_asset()`/`sync_source()` both already
  bypassing that rule — it validates untrusted input rather than deriving a
  strata-owned location, and is useful to any caller joining an untrusted
  segment. Searching first confirmed there was no prior art to reuse:
  `is_relative_to()` appeared nowhere in `src/`, and every existing
  `relative_to()` call was display-only — `layout.py`'s own `display_path()`
  deliberately *swallows* the `ValueError` and falls back to the original
  string, the exact opposite of a security check. `references.py` was
  checked as a candidate home and ruled out: it resolves `(kind, meta.name)`
  document identity, nothing filesystem-related. The helper resolves both
  sides before comparing, since a purely lexical check misses
  `root/sub/../../outside`, and runs **per glob match** — a glob escapes via
  `..` even when the pattern never names the escaping path literally.
- The document-load-time guard, by contrast, had prior art and had simply
  been missed: `validate_relative_path()` was already applied to every other
  path-bearing field in the schema, and `AssetSpecModel.source_path` was the
  sole exception. The absolute case was the worse half of that gap —
  `Path('/repo/root') / '/etc/passwd'` discards the left operand entirely,
  so an absolute `source_path` read straight from the filesystem root. The
  two guards are not redundant: a string check cannot cover a path only
  assembled at build time out of a `{name}` fill value. Both were afterwards
  extended past assets — `resolve_within()` to `sync_source()` and
  `sync_module_source()`, where the remaining vector is narrower but real (a
  symlink inside the repo pointing out of it, which no string validation
  catches and which `copytree` follows by default), and
  `validate_no_path_traversal()` from a `..` *substring* match to a
  per-segment one, so `archive..json` is an ordinary filename again.
- Restricting a `{name}` fill value to `str`/`int`/`float` needs an explicit
  `isinstance(value, bool)` rejection *first*: `bool` subclasses `int` in
  Python, so the natural scalar check silently accepts `True` and fills a
  literal `assets/True/openapi` segment. `None` deliberately falls through
  instead of raising there, so that a missing key and a null value both
  reach `fill_pattern()`'s clearer "unknown token" error.
- Slots, per-deployment binding, a self-contained `location` and `path_from_step` came
  from one scenario: a single APIM workspace for roughly 300 customers, each needing their
  own API definitions bound to the same step. Grounding it against the code found three
  defects, fixed first because slots made them worse: `deploy run` rebuilt its graph
  without `resolved_assets` (Terraform planned against `strata.assets = {}`, reproduced
  end to end); the packaged Terraform type omitted the `provider` attribute provider slots
  had added (Terraform silently drops attributes an `object` type does not declare, so no
  test that merely *accepts* a payload could notice, which is why a parity test now compares
  the payload with the type in both directions); and `path` was relative to the build root
  while Terraform runs in the step's directory.
- Layout: a per-slot subdirectory under the single `build_path/assets/` root beat copying
  straight into it. Direct copy would allow collisions between two assets, need a manifest
  written at build time (deploy could not tell which files belong to a slot), and push
  namespacing into each customer repository. If it is ever adopted instead: detect collisions
  within one run, in memory (never by "file exists on disk"), treat identical bytes as a no-op
  and different bytes as an error naming both sources.
- The first idea for the stale-file problem was to wipe `build_path/assets/`. Review found
  that too broad: a custom `--build-path` is deliberately not cleaned because it may hold
  content the build does not own. Hence the per-slot wipe, after the new source is found.
- Making the Terraform path usable: documenting the convention left a hard-coded `..` count in
  every consumer; an absolute path in the tfvars goes stale when the build output is moved
  between a build job and a deploy job; a `TF_VAR_` variable set by `deploy run` was verified
  against Terraform 1.12.2 to work, but only exists when strata runs Terraform (a plain
  `terraform plan` in the build directory then fails), cannot override a field of `var.strata`
  because `*.auto.tfvars.json` outranks `TF_VAR_*`, and buys nothing `abspath()` does not;
  an `abs_path` written at deploy time duplicates `abspath()` and would make the build-time
  and deploy-time files disagree. A relative `path_from_step` plus a generated local won, and
  the principle that strata mechanisms stay out of Terraform is why the interface is the
  local rather than the field.
- Where the files' location lives: inline on the deployment would need an either-or binding,
  a replace-by-key merge mode and synthetic checkout names, and could not be pinned; a
  templated remote URL was already rejected in remotes-at-scale.md. A `location` on the asset
  document, with the document in the config repository and the files in the customer's, needs
  none of that. Checking it under its own `.strata/assets/` root (rather than naming the
  synthetic remote `asset-<name>`, which could still collide with a manifest remote) is why
  `resolve_remote()` gained an optional `checkout_path`.
- Factoring the slot-binding check once there were two users, with an `on_rebound` callback for
  the one provider-only rule, kept the provider check's tests unchanged: the proof the refactor
  preserved behaviour.
