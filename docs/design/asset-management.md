# Asset Management (non-code, per-environment input files) — Design

- Status: current
- Last updated: 2026-10-12

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
guard `SourceModel.source_path` already uses — `remote` is how an asset
points outside this solution's own repository.

### Workspace wiring: pool + per-step selection

`WorkspaceSpecModel.assets` (the pool) and `ProvisioningStepModel.assets`
(the per-step selection) follow the exact two-layer pattern
`namespaces`/`firewalls`/`dns_zones`/`networks` (pool) +
`ProvisioningStepModel.targets` (selection) already establish — a
workspace declares *what it may use* (cross-document existence, Phase 1
via `References(PlatformKind.ASSET)`), a step declares *what it,
specifically, needs this run* (same-document existence, checked by a
second, independent `asset_names` set in
`WorkspaceSpecModel.validate_execution()`, deliberately not merged into
the existing `target_names` set — an asset is materialised input a step
consumes, not a provisioning target a step acts on):

```yaml
spec:
  assets: [openapi]
  execution:
    - name: publish_api
      provisioner: terraform_apim
      targets: [apim]
      assets: [openapi]
```

### Build-time sync (`sync_asset()`)

`build_controller.py`'s `build_run()` statically scans every
`step.assets` across `workspace.spec.execution` into one `set[str]` of
asset names genuinely needed by *this* build, and resolves each exactly
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
- The destination is always `build_path/assets/<asset-name>/` — keyed by
  asset name only, **never** path-mirrored the way `sync_source()`
  preserves a provisioner's own relative repo layout (that convention
  exists solely to keep sibling Terraform modules' relative
  `source = "../.."`-style references working; an asset has no such
  sibling-composition need, and only one variant is ever resolved per
  build, so there's no collision to avoid either). A single-file source
  still lands inside this directory (preserving its own filename), so the
  destination is always a directory regardless of whether the source was
  one file or a tree — matching ADR-0025's rule that strata supplies
  input, never rewrites or templates the consumer's code to discover it
  by some other means.

### `strata.assets` projection

`ResolvedAssetInfo` (`resolved_context.py`, alongside `ResolvedModule`) —
a plain `path`/`files` pair — is populated by the same asset-sync loop
above: after each successful copy, it lists the destination directory's
sorted filenames and stores the result in a new
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

`_strata_assets()` (`terraform_projection.py`) projects this into
`var.strata` as a sixteenth key (`"assets": _strata_assets(graph)`),
alongside the other fifteen `build_strata_payload()` already produced
(`workspace`, `tenant`, `providers`, `resources`, `namespaces`, `modules`,
`dns`, `networks`, `firewalls`, `topologies`, `properties`, `custom`,
`flags`, `variables`, `default_tags`) — matching
[ADR-0035](../decisions/0035-unified-terraform-projection.md)'s "every
category always present, empty when unused" rule in every way but one:
`strata.assets` is **deliberately** keyed only by names at least one
execution step's `step.assets` actually references in *this* build, not
the full `workspace.spec.assets` pool — unlike `namespaces`/`firewalls`/
`dns_zones`/`networks` (pure YAML data, always safe to project
unconditionally), an asset entry requires real filesystem resolution, and
eagerly resolving every declared-but-unreferenced pool entry would turn a
harmless, currently-unused pool declaration into a hard build failure the
moment that entry's variant doesn't exist on disk yet. A provisioner reads
the result as an ordinary Terraform value instead of globbing a hardcoded
relative path:

```hcl
local.strata.assets.openapi.path   # "assets/openapi", relative to the build root
local.strata.assets.openapi.files  # ["api.json", "nested/deep.json"]
```

`files` is a **recursive, files-only** listing of what actually landed,
each entry relative to `path` — so it stays correct for a glob that
preserved nested structure, and never reports a directory name as though
it were a file.

`strata_variables.tf`'s generated type carries the matching
`assets = map(object({ path = string, files = list(string) }))` entry,
alongside `modules`. Nothing secret-shaped here (a filename is not a
value needing `strata_secrets` treatment), and nothing is written into
`resolved.yaml` for this — an asset's build-output path is a filesystem
location a provisioner's own code already knows how to reach via the
destination convention above; `strata.assets` is additive, read-only
metadata about that same location, not a replacement for it.

### Deliberately out of scope

- **Version pinning** (a `kind: version`-style `pins.assets` overlay) — no
  real evidence an asset variant's `source_path`/`remote` needs
  point-in-time pin/audit tracking the way an image tag does; an asset
  variant is already pinned by its own declared `source_path` (whatever
  is on disk there *is* the version). Revisit if a real case needs it.
- **Credentialed remote fetch for assets** — already fully solved by the
  existing `remote`/`resolve_remote()`/`SourceIntegration` mechanism
  ([remotes.md](../work/remotes.md)); nothing asset-specific needed.
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
  `WorkspaceSpecModel`'s pool-of-names pattern, reused unchanged for
  `assets`.
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
  [workspace-execution-model-review.md](../work/workspace-execution-model-review.md)
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
- Glob support, the two path-traversal guards, the scalar-only fill rule,
  and the recursive `files` listing all came out of a code review of the
  shipped feature (2026-10-13,
  [asset-management-review.md](../work/asset-management-review.md)).
  Three of those were defects; globbing was not. It replaced an open
  question this design had answered badly — whether `files` should be a
  shallow or recursive listing — by removing the question: once the
  author states which files they want, strata has nothing left to infer.
  The shallow `iterdir()` listing it replaced also reported directory
  names as though they were files. The review's own verification found
  glob syntax and the existing `{name}` tokens compose without collision
  (a token name is `\w+`, so brace expansion can never be mistaken for
  one), and that a wildcard-free `source_path` still resolves exactly as
  before — so no existing asset document changed meaning.
