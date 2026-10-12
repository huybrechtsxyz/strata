# Asset Management (non-code, per-environment input files) — Work

- Status: draft
- Last updated: 2026-10-11

## Overview

A gap found while onboarding a real project: a provisioner sometimes needs
**plain input files that are not IaC code** — an OpenAPI contract (`.json`/
`.xml`), a set of images, a data/seed file, a certificate bundle — and the
*right* file differs per environment (dev/qas/prod each have their own
`openapi.json`). None of this belongs inside the provisioner's own source
tree (e.g. a `terraform/` directory) because:

- That tree is normal IaC code, usually committed and reviewed as code;
  mixing in binary/data files (images, large fixtures, per-customer specs)
  pollutes history and review diffs.
- The *same* `.tf`/Ansible code must run unmodified against dev/qas/prod —
  only the attached file differs, so it cannot be hardcoded as a sibling
  file next to the `.tf` code either (that would mean three near-duplicate
  provisioner sources, one per environment).

Nothing in today's schema models "a named, non-code input, with one file/
folder variant per environment, copied into the build output for a
provisioner to consume." This doc works through the shape of that gap —
nothing here is implemented yet.

## Real evidence / motivating shape

```
assets/contoso/dev/openapi.json
assets/contoso/dev/openapi.xml
assets/contoso/qas/openapi.json
assets/contoso/qas/openapi.xml
assets/contoso/prod/openapi.json
assets/contoso/prod/openapi.xml
```

One named asset family (`openapi`), three environment-scoped variants, each
a *set* of files (not necessarily one file). A real execution step (e.g. a
Terraform provisioner that uploads the spec to an API Management instance,
or an Ansible play that copies a certificate) needs exactly the variant
matching the deployment's own environment, materialised into that step's
build output — the same "render once at build time, nothing resolved later"
shape `build run` already uses for everything else (ADR-0022).

## What already exists that this can reuse, unchanged

Checked before designing anything new — most of the hard part
(remote-to-filesystem resolution, byte-for-byte copying into the build
directory) is already built and working for a structurally identical
problem (`ProvisionerModel.source`):

- **`SolutionRemoteModel` + `resolve_remote()`** (`docs/work/remotes.md`) —
  already resolves a named remote (`local`/`git`, `fetch: strata` or
  `external`) to a real filesystem root. An asset's files can live in the
  *same* repository (a plain `assets/` directory, sibling to the IaC
  source, selected via `remote: null`) or in a **completely separate**
  repository (a dedicated assets/design repo, large binaries kept out of
  the IaC repo's history entirely) — both already work today, zero new
  code needed here.
- **`sync_source()`/`sync_module_source()`** (`strata/controllers/source_sync.py`)
  — already does exactly "resolve a remote, copy a `source_path` directory
  or file into a build-output directory, byte-for-byte, never templated"
  (ADR-0025). An asset sync function is a small variation of this, not a
  new mechanism.
- **`PlatformKind`/`DocumentIndex`** — a named, independently-referenceable,
  cross-workspace document is an already-proven pattern (`provider`,
  `tenant`, `artifact`); nothing new to invent at the discovery/indexing
  layer.
- **The `properties` deep-merge chain** (`build-time-value-categories.md`
  Q3: Workspace → each reachable Environment, tenant's folded in first →
  Deployment, later wins) — already computes exactly the kind of opaque,
  deployment-resolved, build-time-safe value (`deploy_tier` and friends,
  ADR-0034) this design fills an asset's path template from. No new merge
  logic needed, just a new consumer of an existing, already-tested result.

## What's genuinely missing

1. A way to declare a **named, reusable file-set location**, independent
   of any single workspace (so the same asset family can be referenced
   from more than one provisioner/workspace, matching why `artifact`/
   `tenant`/`provider` are standalone kinds rather than embedded fields —
   docs/archive/artifact-references.md's "Identity/reuse" reasoning
   applies identically here), whose path can vary per deployment without
   a fixed environment vocabulary.
2. A workspace-level pool declaring which asset families it uses (Phase 1
   existence-checked, same shape as `providers`/`namespaces`/`firewalls`/
   `dns_zones`/`networks`), plus a per-`ProvisioningStepModel` selection of
   *which* of those this specific step needs — mirroring `targets`'
   existing pool-then-select pattern exactly, not a new shape.
3. A sync function that fills the path template from the deployment's own
   resolved `properties` and copies the result into a predictable
   build-output location.
4. A documented, predictable destination convention so a provisioner's own
   `.tf`/Ansible code can actually reference the copied files (no new
   token/indirection mechanism — see "Destination convention" below).

## Why this is not the same as `kind: artifact` (ADR-0026)

`artifact` was deliberately scoped to **container image references**
(`image_name`/`image_tag`/`registry`) that strata never fetches — the
image stays in a registry and is pulled by the runtime (Docker/Kubernetes),
never copied to disk by strata at all. The shape here is the opposite:
strata **must** copy real files onto disk at build time, there is no
"registry" a Terraform/Ansible tool pulls from on its own. Reusing
`artifact`'s schema would mean grafting a file-copy behaviour onto a kind
whose entire reason for existing is "strata does not fetch this" — the
same category of shape-mismatch rejection ADR-0026 itself already applied
when it rejected stretching `Module`/`SourceModel` to cover artifacts (see
that doc's "Alternatives considered and rejected").

## Proposed design (draft — not decided, revised after a first pass got the selector wrong)

### Why "key the variant off a real `Environment` document" was wrong

The first draft of this doc modelled `AssetVariantModel.environment` as a
`References(PlatformKind.ENVIRONMENT)` field — i.e. a variant's selector
had to *be* a real Environment document's `meta.name`. Rejected on review:

- **Environment identity and "deploy tier" are already two different,
  deliberately decoupled things in this schema.** `ADR-0034`/
  [deployment-tier-resource-sizing.md](../design/deployment-tier-resource-sizing.md)
  settled this already, for the structurally identical problem of
  "something needs to vary dev/qas/prod": it adds **no schema, no fixed
  tier vocabulary** — a tier is a plain opaque `properties` value
  (`deploy_tier: standard`), decoupled entirely from which `Environment`
  document is attached. Forcing asset selection through Environment
  identity would reintroduce exactly the coupling that decision rejected.
- **It doesn't even hold up against this repo's own fixture.** This
  solution's real `config/environments/` only declares `prd.yaml`
  (`meta.name: prd`) — not `prod`. The motivating folder layout above uses
  `prod/`. A hard identity reference would force every asset author to
  either rename their folders to match a document's `meta.name` exactly,
  or rename the `Environment` document to match the folder — neither is a
  real constraint anywhere else in the schema today.
- **A deployment is reachable to *several* Environment documents at once**
  (`DeploymentSpecModel.environments`, a tenant's own folded in first) —
  an identity-reference selector has no principled answer for "which one
  names the variant," forcing a new, invented precedence rule
  ("last reachable environment wins") with no precedent anywhere else in
  the codebase to justify it.
- **Mechanism B tokens (`${var:}`/`${secret:}`/`${feature:}`) are the
  wrong tool too, not just a worse version of the same idea.**
  [value-token-resolution.md](../design/value-token-resolution.md) is
  explicit and settled: that whole mechanism resolves **only at deploy
  time**, by deliberate design, for consistency across every kind that
  uses it (not a technical limitation for the build-time-safe cases — a
  conscious choice not to special-case one kind). But `sync_asset()` has
  to run during `build run`, not `deploy run` — the files must already be
  on disk by the time a provisioner's `.tf`/Ansible code runs. Embedding
  `${var:KEY}` in `source_path` and resolving it at build time would be a
  one-off exception to an already-decided, cross-cutting rule — the kind
  of inconsistency that same doc explicitly rejected once already ("a
  `dns`/`networks`-only build-time resolver would be inconsistent... one
  shared resolver... belongs at deploy time").

### What other systems actually do here (checked before designing further)

Every comparable tool treats "which environment am I" as a **free-form,
unchecked label**, never a cross-referenced identity lookup:

- **Terraform**: `terraform.workspace` is a bare string a `.tf` author
  interpolates directly (`"${path.module}/assets/${terraform.workspace}"`)
  — nothing validates it against a registry of "real" workspaces.
- **Kustomize**: an overlay is just a directory (`overlays/prod/`) selected
  by path at build time (`kustomize build overlays/prod`) — no identity
  concept, structural convention only.
- **Helm**: `-f values-prod.yaml` is filename convention, not a schema
  reference.
- **GitHub Actions / GitLab CI `environment:`**: a free-form string label
  on a job — may optionally match a *protection rule* name, but nothing
  requires it to match any other declared document.

The common thread: the selector is always an **opaque label a human
chose**, resolved by simple equality/interpolation against whatever the
current run already knows about itself — never a validated reference to a
separately-modelled identity. This matches ADR-0034's own choice, not just
coincidentally — it's the same shape of problem.

### Revised direction: template `source_path` against the deployment's already-resolved `properties`

No `variants[]` list, no per-asset Environment reference at all. One
`source_path`, with `{name}`-style segment tokens (same token syntax
`path_conventions.py`'s `compile_pattern()` already uses for *matching* —
reused here for the opposite direction, *filling*), substituted against
`ResolvedWorkspaceGraph.properties` — the deep-merged, already-build-time-
available, opaque properties layer (`Workspace` → each reachable
`Environment`, tenant's folded in first → `Deployment`, later wins) that
[build-time-value-categories.md](../design/build-time-value-categories.md)
Q3 already computes, once, before the provisioner loop, for every build.
Entirely local, no network/secret involved — the same build-time-safety
boundary `properties`/`variable_refs` already have today.

**Checked directly against `build_controller.py`'s real `build_run()`,
not assumed**: `properties = merge_workspace_environment_deployment_properties(...)`
(line 499) runs, and `graph = build_resolved_workspace_graph(...)` (line
522) carries that result, both strictly *before* `for step in
ordered_by_depends_on(...)` (line 547) — the loop that would call
`sync_asset()`. So `graph.properties` is always a plain, already-merged
`dict[str, Any]` by the time any step runs — nothing left to resolve. This
holds for a reason stronger than "it happens to run first": unlike
`variables`/`secrets`/`features` (each a `{key, store, value}` entry needing
a *lookup* — `constant`/`environment`/an integration-backed store —
before a real value exists), `properties`/`custom` have no `store`
indirection at all. They are raw YAML dict content, deep-merged in memory
(`deep_merge()`, pure dict logic) — "resolved" here only ever meant
"merged," never "looked up from somewhere else," so there was never a
timing question for the literal case to begin with.

The one real edge case this surfaces: nothing stops a user writing
`properties: {deploy_tier: "${var:TIER}"}` — a Mechanism-B token embedded
*inside* a properties value, which this doc's own "Alternatives considered"
#2 already established resolves only at deploy time. If a workspace ever
did that, `graph.properties["deploy_tier"]` at build time would still be
the literal, unresolved string `"${var:TIER}"` — `sync_asset()` would fill
it in verbatim and then fail loudly on the resulting path not existing
on disk (not a silent wrong-file copy, since no such path is ever real) —
but with a confusing low-level error, not a clear "this property still has
an unresolved token" diagnostic. Worth a dedicated check
(`has_value_tokens()` already exists, `common_models.py`) before using a
property's value to fill a path segment — tracked in Remaining Work below,
not designed further here since no real workspace does this today.

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

```yaml
# config/environments/prd.yaml — already exactly this key, ADR-0034's own real example
spec:
  properties:
    deploy_tier: production
```

```python
class AssetSpecModel(PlatformBaseModel):
    description: str | None = Field(None, description="Optional description for documentation purposes")
    remote: Annotated[PlatformName, RemoteReference()] | None = Field(
        None, description="Remote this asset's files live under (SourceModel's own field/meaning, reused "
        "unchanged). None means this solution's own repository."
    )
    source_path: str = Field(
        description="Path to the file or directory, relative to the resolved remote's root. May contain "
        "'{name}' segment tokens, filled from the deployment's resolved spec.properties at build time "
        "(same syntax path_conventions.py's compile_pattern() uses for matching, reused here for filling)."
    )
```

No fixed vocabulary for the token name — `{deploy_tier}` above is just
whatever `properties` key a workspace already uses (ADR-0034's own
precedent), `{ring}`/`{region}`/anything else works identically. A token
with no matching `properties` key is a clear build-time error (`sync_asset()`
raising, same error class `SourceSyncError` already uses for a missing
`source_path`) — not a silent empty-string substitution.

### Worked example — `{customer}`/`{region}`/`{deploy_tier}` together

Extends the "Real evidence" shape above with a second segment (`region`) to
show the fill isn't limited to one token, and that each fill value can come
from a *different* document in the merge chain — `properties` is already
deep-merged before `sync_asset()` ever sees it, so the asset design itself
does not care which layer a given key came from.

```yaml
# tenant.yaml — Contoso's own properties, folded in first (ADR-0024)
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: contoso
spec:
  properties:
    customer: contoso
```

```yaml
# environments/dev.yaml and environments/prd.yaml — each environment's own region + tier
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: dev
spec:
  properties:
    region: westeurope
    deploy_tier: dev
---
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  properties:
    region: northeurope
    deploy_tier: prod
```

```yaml
# asset.yaml — one template, three segments, all opaque properties keys
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: openapi
spec:
  source_path: "assets/{customer}/{region}/{deploy_tier}/openapi"
```

```yaml
# workspace.yaml (excerpt) — the pool, then the step's selection from it
spec:
  assets: [openapi]
  execution:
    - name: publish_api
      provisioner: terraform_apim
      targets: [apim]
      assets: [openapi]
```

```yaml
# deployment-prd.yaml (excerpt) — Contoso, production, northeurope
spec:
  workspace: apim_workspace
  tenant: contoso
  environments: [prd]
```

For this deployment, `graph.properties` is already
`{customer: "contoso", region: "northeurope", deploy_tier: "prod", ...}`
by the time the `publish_api` step runs. `sync_asset()` fills
`"assets/{customer}/{region}/{deploy_tier}/openapi"` into
`"assets/contoso/northeurope/prod/openapi"`, resolves it against the
solution's own repository (`remote: null`), and copies whatever is there
into `build_path/assets/openapi/`. A sibling `deployment-dev.yaml` naming
`environments: [dev]` instead resolves the exact same template to
`"assets/contoso/westeurope/dev/openapi"` — no change to the asset
document, the workspace, or the step; only the deployment's reachable
`Environment` changes, which is the whole point of keeping the fill source
opaque and deployment-resolved rather than hardcoded per variant.

Solutions already using the hub/spoke path-convention machinery
([path-conventions.md](../design/path-conventions.md)'s
`DeploymentLayersModel.segments: dict[str, str]`) have an equally opaque,
already-resolved string mapping available per deployment — worth allowing
as a second, merged-in source of fill values later if a real case needs
it, but not required for the common case above; left an open question
below rather than designed now; `properties` alone already covers the
motivating evidence.

`configuration.spec.paths` ([path-conventions.md](../design/path-conventions.md))
remains a good complementary fit, unchanged from the first draft's
intent, just now understood as **validation, not selection**: once a
real on-disk `assets/contoso/prod/openapi/` exists, a solution can
optionally declare a `PathConventionModel` over `assets/**` so `strata
validate` catches a misnamed/misplaced asset directory structurally — a
separate, additive check, not part of resolving which directory to copy.

### `WorkspaceSpecModel.assets` (new field) — the pool, same shape as `providers`/`namespaces`

```python
assets: list[Annotated[PlatformName, References(PlatformKind.ASSET)]] | None = Field(
    None, description="Asset document names this workspace may materialise into a build (Phase 1 existence "
    "check, same pattern as 'namespaces'/'firewalls'/'dns_zones'/'networks')."
)
```

Not a `ProvisionerModel` field after all (this doc's second draft) —
`WorkspaceSpecModel` already has an established, repeated pattern for
"the pool of things this workspace may use": `providers`/`namespaces`/
`firewalls`/`dns_zones`/`networks` are all flat name lists here, each
Phase-1-checked via `References(PlatformKind.X)`, and `resources` is the
same pattern inline. `assets` is one more pool of the identical shape —
no reason to special-case it onto `ProvisionerModel` instead.

### `ProvisioningStepModel.assets` (new field) — the selection, same shape as `targets`

```python
assets: list[PlatformName] | None = Field(
    None, description="Asset names (from this workspace's spec.assets) this step needs materialised into "
    "the build output before its provisioner runs.",
)
```

Cross-checked in `WorkspaceSpecModel.validate_execution()` — today that
method already builds one `target_names` set folding together
`resources`/`namespaces`/`dns_zones`/`networks`/`firewalls` and rejects any
`step.targets` entry outside it. `assets` gets the identical treatment as
a **second**, independent set (not merged into `target_names` — an asset
is materialised input, not a provisioning target a step "acts on" the way
`targets` are defined, so keeping the two checks/sets separate matches
what they actually mean):

```python
asset_names = set(self.assets or [])
for step in self.execution:
    for asset in step.assets or []:
        if asset not in asset_names:
            errors.append(f"Execution step '{step.name}' references undefined asset '{asset}'")
```

This is exactly the shape the first draft was missing: a workspace
declares *what it may use* (cross-document existence, Phase 1), a step
declares *what it, specifically, needs this run* (same-document existence,
also Phase 1) — identical two-layer shape `targets`/`resources` already
established, just one more pool.

### Sync mechanism (revised, 2026-10-11 — resolved once up front, not lazily per-step)

A new `sync_asset()` in `strata/controllers/source_sync.py` (or a sibling
module), structurally parallel to `sync_source()`: fill `source_path`'s
`{name}` tokens from `graph.properties`, resolve `remote` via the
already-built `resolve_remote()`, copy the result (file or directory,
`shutil.copy2`/`copytree` exactly as today) into the build output, and
return the destination `Path` — the exact same signature `sync_source()`
already has.

Original plan (first draft of this section) called this **from the step
loop**, once per `step.assets` entry, with an explicit no-op/idempotency
rule for two steps naming the same asset. Revised after designing the
projection below: since `strata.assets` (next section) needs to be
computed *before* the step loop even starts — the same timing
`properties`/`tenant`/`version` already have on `ResolvedWorkspaceGraph`
(`resolved_context.py`) — there is no reason to defer the copy itself to
step-loop time either. `build_controller.py` now:

1. Statically scans every `step.assets` across `workspace.spec.execution`
   (plain declarative data — no need to actually run a step to know this)
   into one `set[str]` of asset names genuinely needed by *this* build.
2. Calls `sync_asset()` exactly once per name in that set, before the step
   loop, right alongside where `properties`/`graph` are assembled.
3. Lists the returned destination directory's filenames
   (`sorted(p.name for p in dest.iterdir())` if a directory, `[dest.name]`
   if a single file) and stores `ResolvedAssetInfo(path, files)` keyed by
   name — see "`strata.assets` projection" below.

This removes the earlier no-op/idempotency concern structurally (each name
in the set is resolved exactly once, by construction) rather than by
making `sync_asset()` defensively idempotent. The step loop itself no
longer calls `sync_asset()` at all — by the time any step runs, every
asset it names is already on disk, the same guarantee steps already have
for `graph.properties`.

### Destination convention

`build_path / "assets" / <asset-name>/` — keyed by asset name only, **not**
path-mirrored the way `sync_source()` preserves a provisioner's own
relative repo layout (that convention exists solely to keep sibling
Terraform modules' relative `source = "../.."`-style references working,
docs/design in `source_sync.py`'s own module docstring — an asset has no
such sibling-composition need, and only one variant is ever resolved per
build, so there's no collision to avoid either). A fixed, name-keyed
location is predictable enough for a provisioner's own code to reference
directly (e.g. Terraform: `"${path.module}/../../assets/openapi"`),
matching ADR-0025's existing rule: strata supplies input, it does not
rewrite or template the consumer's code to discover that input by some
other means.

No new `${asset:}` token, and nothing written into `resolved.yaml` for
this — an asset's build-output path is not a *value* consumed by name
inside a document field; it is a filesystem location the provisioner's
own code already knows how to reach once the destination convention is
documented. `strata.assets` (below) is additive, read-only metadata about
that same filesystem location — it does not replace or change this
convention, a consumer can still always just reach for the known relative
path directly.

### `strata.assets` projection (new, 2026-10-11)

[unified-terraform-projection.md](../design/unified-terraform-projection.md)
(ADR-0035) graduated to `docs/design/` after this doc's first draft, and
`sync_asset()`/`build_strata_payload()` were designed completely
unconnected until now: one copies files to `build_path/assets/<name>/`,
the other projects every *document* category (`workspace`/`providers`/
`resources`/.../`properties`/`custom`) into `var.strata` — fifteen keys,
none of them an asset. A provisioner's `.tf` code could reach the copied
files only by hardcoding the destination convention's relative path and,
to know *which* files actually landed there, would need its own
`fileset()`/`for_each` glob — strata already knows this, just didn't say
so.

**`ResolvedAssetInfo`** (`resolved_context.py`, new, alongside
`ResolvedModule`) — the same "controller resolves paths/remotes, the
Integration never touches `DocumentIndex`/remotes directly" split that
type's own docstring already establishes for namespace modules:

```python
@dataclass(frozen=True)
class ResolvedAssetInfo:
    """One asset's build-time resolution (docs/work/asset-management.md) —
    computed once by build_controller.py before the step loop, exactly
    like ResolvedModule.source_path is for namespace modules. Carries no
    remote/DocumentIndex access itself; terraform_projection.py only ever
    reads the plain fields below."""

    path: str                         # build-relative, e.g. "assets/openapi"
    files: list[str] = field(default_factory=list)  # sorted filenames at that path
```

**`ResolvedWorkspaceGraph.resolved_assets: dict[str, ResolvedAssetInfo]`**
(new field, default `{}`) — keyed by asset name, populated by
`build_controller.py`'s step 3 above (not by `terraform_projection.py`,
which never touches a remote or the filesystem — ADR-0021 D2).

**`_strata_assets()`** (`terraform_projection.py`, same shape as every
other category function — a plain read of already-resolved graph data,
zero I/O):

```python
def _strata_assets(graph: ResolvedWorkspaceGraph) -> dict[str, Any]:
    """`strata.assets` — name -> {path, files}, resolved once by
    build_controller.py before the step loop (ResolvedAssetInfo). Unlike
    every other category, keyed only by assets at least one execution
    step actually references this build — see this function's own
    "Deliberate deviation" note in docs/work/asset-management.md."""
    return {
        name: {"path": info.path, "files": info.files}
        for name, info in graph.resolved_assets.items()
    }
```

Wired into `build_strata_payload()` as a sixteenth key, `"assets":
_strata_assets(graph)`, and into `strata_variables.tf`'s generated type
right alongside `modules`:

```hcl
assets = map(object({
  path  = string
  files = list(string)
}))
```

```hcl
local.strata.assets.openapi.path   # "assets/openapi", relative to the build root
local.strata.assets.openapi.files  # ["openapi.json", "openapi.xml"]
```

Nothing secret-shaped here — a filename is not a value needing
`strata_secrets` treatment, so no `strata_locals.tf` reassembly is
involved for this category, same as `providers`/`resources`/etc. that
happen to hold no secret-shaped leaves in a given build.

**Deliberate deviation from "every category always emits the full
workspace pool, empty `{}` when unused"**: `strata.assets` is keyed only
by names at least one `execution` step's `step.assets` actually
references *in this build* — not the full `workspace.spec.assets` pool,
unlike `namespaces`/`firewalls`/`dns_zones`/`networks` (which project
every pool entry unconditionally, since those are pure YAML data, always
safe to include). An asset entry requires real filesystem resolution
(`resolve_remote()` + a directory that must actually exist); eagerly
resolving every *declared* pool entry regardless of whether this build's
steps need it would turn a harmless, currently-unused `assets: [openapi,
some_other_asset]` pool declaration into a hard build failure the moment
`some_other_asset`'s variant doesn't exist on disk yet for this
deployment — strictly worse than today's reference-scoped laziness. This
mirrors the open question already tracked in Remaining Work #1 (should a
missing variant be a hard error always, or only when reachable) — this
design answers it for the *pool* vs. *referenced* distinction specifically,
by only ever resolving what's referenced.

## Alternatives considered and rejected (draft reasoning — revisit before deciding)

1. **Key a variant off a real `Environment` document identity**
   (`References(PlatformKind.ENVIRONMENT)`, this doc's own first draft) —
   rejected, see "Why... was wrong" above: contradicts ADR-0034's already-
   decided opaque-properties approach to the identical dev/qas/prod
   problem, doesn't hold up against this repo's own `prd` vs. `prod`
   naming mismatch, and has no principled answer when a deployment is
   reachable to more than one Environment document at once.
2. **Embed `${var:KEY}`/Mechanism-B tokens in `source_path`, resolved at
   build time** — rejected: that token mechanism is deploy-time-only by
   deliberate, already-settled cross-cutting design
   ([value-token-resolution.md](../design/value-token-resolution.md)), not
   a technical limit for the build-time-safe case specifically; making an
   exception for this one new field would reopen a question that doc
   already closed for consistency's sake. `properties`-templating (chosen
   direction, above) gets the same "fill from an already-resolved,
   build-time-safe value" result through the mechanism that's actually
   build-time by design (`build_value_references()`'s `properties` merge).
3. **Embed the file-set content directly on `Workspace`/`Provisioner`**
   (no standalone kind at all) — rejected: the real motivating case names
   a *reusable* file family (the same `openapi` asset plausibly attaches
   to more than one provisioner, or is shared across workspaces for the
   same customer) — identical reasoning to why `artifact`/`tenant`/
   `provider` are standalone, independently-referenced kinds rather than
   embedded fields. The standalone `kind: asset` document stays; only
   *which workspace/step uses it* moved (see next point).
4. **Attach the asset reference to `ProvisionerModel`** (this doc's second
   draft) — rejected on review: `WorkspaceSpecModel` already has an
   established, repeated two-layer pattern for "declare the pool, then
   each execution step selects from it" (`namespaces`/`firewalls`/
   `dns_zones`/`networks`/`resources` as the pool, `ProvisioningStepModel
   .targets` as the per-step selection, cross-checked in
   `validate_execution()`). Putting `assets` on `ProvisionerModel` instead
   would have given assets a different shape from every other
   workspace-pool concept for no real reason — `WorkspaceSpecModel.assets`
   (the pool) + `ProvisioningStepModel.assets` (the selection) matches the
   existing pattern exactly instead of inventing a second one.
5. **Stretch `kind: artifact` to also cover files** — rejected, see
   "Why this is not the same as `kind: artifact`" above.

## Deliberately out of scope (for this draft)

- **Version pinning** (a `kind: version`-style `pins.assets` overlay,
  parallel to `pins.artifacts`) — no real evidence yet that an asset
  variant's `source_path`/`remote` needs point-in-time pin/audit tracking
  the way an image tag does; an asset variant is already pinned by its own
  declared `source_path` (whatever is on disk there *is* the version).
  Revisit if a real case needs it.
- **Credentialed remote fetch for assets** — already fully solved by the
  existing `remote`/`resolve_remote()`/`SourceIntegration` mechanism
  (`docs/work/remotes.md`); nothing asset-specific needed here.
- **Structural validation of a variant's on-disk shape** (e.g. "must
  contain an `openapi.json`") — no evidenced need; a provisioner's own
  code is the only thing that knows what it expects to find.

## Related Decisions

- [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) —
  `WorkspaceSpecModel`'s existing pool-of-names pattern
  (`namespaces`/`firewalls`/`dns_zones`/`networks`), reused unchanged for
  `assets`.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  `ProvisioningStepModel.targets`, the precedent `ProvisioningStepModel
  .assets`'s own cross-check in `validate_execution()` mirrors directly.
- [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) —
  `SourceModel`'s field shape, reused here.
- [ADR-0022](../decisions/0022-strata-build-run.md) / [build-command.md](build-command.md) —
  the build pipeline this plugs into (`sync_source()`, the step loop).
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) —
  why assets are copied byte-for-byte, never templated, and why the
  destination is a documented convention rather than a new indirection
  token.
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) /
  [artifact-references.md](../archive/artifact-references.md) — the
  adjacent, already-built "named external reference" kind, and why its
  shape doesn't cover this gap.
- [ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md) /
  [deployment-tier-resource-sizing.md](../design/deployment-tier-resource-sizing.md) —
  the precedent this design's `{name}`-from-`properties` templating
  follows directly: no schema, no fixed vocabulary, an opaque
  deployment-resolved value.
- [value-token-resolution.md](../design/value-token-resolution.md) — why
  Mechanism B (`${var:}`-style embedded tokens) is deploy-time-only by
  design, and therefore not the right tool for a build-time file copy.
- [path-conventions.md](../design/path-conventions.md) — `compile_pattern()`'s
  `{name}` token syntax (reused here for filling, not matching), and
  `DeploymentLayersModel.segments` as a possible future second source of
  fill values.
- [remotes.md](remotes.md) — `resolve_remote()`, the prerequisite this
  design reuses unchanged.
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) /
  [unified-terraform-projection.md](../design/unified-terraform-projection.md) —
  the finished `var.strata` projection; `strata.assets` (this doc's
  "`strata.assets` projection" section) is a sixteenth category following
  that design's own established shape, exposing `ResolvedAssetInfo`'s
  `path`/`files` for only the asset names this build's execution steps
  actually reference.

## Remaining Work / Open Questions

1. Decide the design above for real (currently a draft, not reviewed) —
   in particular, whether a missing `{name}` token in `graph.properties`
   should be a hard build error always, or only when the asset is
   actually reachable from the deployment being built (defensive-only
   otherwise).
2. A dedicated diagnostic (reusing `has_value_tokens()`) when a
   `properties` value used to fill a `{name}` segment still contains an
   unresolved `${var:}`-style token, instead of letting it surface only as
   a confusing "path does not exist" failure (see the edge case noted in
   "Revised direction" above — no real workspace hits this today, but the
   failure mode is unclear if one ever does).
3. Whether `DeploymentLayersModel.segments` should be a second, merged-in
   source of fill values alongside `properties` (left open above — not
   needed for the motivating evidence, which only needs `properties`).
4. `AssetModel`/`AssetSpecModel` — new model file, `PlatformKind.ASSET`
   enum member, service, index registration (same boilerplate every
   existing kind already has).
5. A small `fill_pattern(template, values)` helper (the inverse of
   `path_conventions.compile_pattern()` — substituting `{name}` tokens
   from a mapping instead of matching them), raising clearly on an unknown
   token rather than leaving it literal in the path.
6. `WorkspaceSpecModel.assets` (pool, Phase 1 existence check) +
   `ProvisioningStepModel.assets` (selection) + the new cross-check in
   `validate_execution()` (second `asset_names` set, same shape as the
   existing `target_names` one).
7. `sync_asset()` + wiring into `build_controller.py`, called once per
   name in the statically-scanned `step.assets` set, before the step loop
   (see "Sync mechanism" above — revised 2026-10-11 from the original
   per-step-lazy plan).
8. `ResolvedAssetInfo` (`resolved_context.py`) + `ResolvedWorkspaceGraph
   .resolved_assets` + `_strata_assets()` (`terraform_projection.py`) +
   the `strata_variables.tf` template's new `assets` type entry — see
   "`strata.assets` projection" above, the concrete design for this item
   (superseding the previous draft's open "whether to" framing).
9. Decide whether a missing on-disk `source_path` (after filling) is a
   build-time error only (most likely, matching `sync_source()`'s own
   `SourceSyncError` today) or also a `strata validate` Phase 2 warning
   (would need a real filesystem check at validate time, which Phase 2
   does not currently do for any other kind — confirm real appetite before
   adding it).
10. Write the JSON Schema / docs/config/asset.md once the shape above is
    no longer draft.
11. Graduate this doc to `docs/design/asset-management.md` once built and
    end-to-end tested, per `docs/work/README.md`'s own lifecycle — collapse
    this section and "Alternatives considered" into a `## History` section
    at that point.

## Changelog

- 2026-10-10: Created. Written from a real, currently-needed project gap:
  OpenAPI contract files (and similarly, images/data files) that differ
  per environment and must land in a provisioner's build output without
  living inside its own (committed) IaC source tree.
- 2026-10-10: Reworked variant selection after review — an
  Environment-identity reference was the wrong tool (conflicts with
  ADR-0034's already-decided opaque-`properties` precedent for the
  identical dev/qas/prod problem, and doesn't hold up against this repo's
  own `prd`-not-`prod` naming). Checked Mechanism B embedded tokens too —
  wrong tool for a different reason (deploy-time-only by deliberate,
  already-settled design). Checked other systems (Terraform workspaces,
  Kustomize overlays, Helm `-f` filename convention, CI `environment:`
  labels) — all treat this as a free opaque label, never an identity
  reference, reinforcing the `properties`-templating direction.
- 2026-10-10: Moved the asset reference off `ProvisionerModel` onto
  `WorkspaceSpecModel.assets` (the pool) + `ProvisioningStepModel.assets`
  (the per-step selection), after noticing `WorkspaceSpecModel` already
  has an established, repeated pool-then-select pattern for exactly this
  shape (`namespaces`/`firewalls`/`dns_zones`/`networks` as pools,
  `ProvisioningStepModel.targets` as the per-step selection, both
  cross-checked in `validate_execution()`) — `assets` now follows that
  same pattern instead of inventing a provisioner-level variant of it.
  The `{name}`-segment-token-from-`properties` fill mechanism (previous
  entry) is unchanged by this — it still resolves the one concrete path
  for whichever asset a step names, now triggered per `step.assets` entry
  instead of per provisioner.
- 2026-10-10: Verified the "is `properties` actually resolved by the time
  a step copies files?" question directly against `build_controller.py`'s
  real `build_run()` rather than assuming: the `properties` merge (line
  499) and `graph` assembly (line 522) both run strictly before the step
  loop (line 547) that would call `sync_asset()` — confirmed, not assumed.
  Also surfaced one real edge case while checking: `properties`/`custom`
  have no `store` indirection at all (unlike `variables`/`secrets`/
  `features`), so there was never a genuine timing risk for the literal
  case — but a `${var:}` token embedded *inside* a properties value
  (always deploy-time-only, per an earlier entry above) would still be
  unresolved at build time and fail obscurely rather than with a clear
  diagnostic; added to Remaining Work rather than designed further, since
  no real workspace does this today.
- 2026-10-11: Checked this doc against
  [unified-terraform-projection.md](../design/unified-terraform-projection.md)
  (ADR-0035), which graduated to `docs/design/` the day after this doc was
  first written. Today `sync_asset()` and `build_strata_payload()` are
  genuinely unlinked — an asset is copied to a known directory, but
  nothing about it (its path, or the filenames the resolved variant
  actually contains) is projected into `var.strata` the way every
  document category is. Added "Open consideration" (new section, above
  "Alternatives considered") proposing a sixteenth `strata.assets`
  category, same shape as every other category, for a provisioner that
  wants to read the file list as an ordinary Terraform value instead of
  globbing a hardcoded relative path — tracked as an open question
  (Remaining Work #4), not designed further, since no real provisioner
  has needed this yet.
- 2026-10-11: Turned the "Open consideration" into a concrete design —
  `ResolvedAssetInfo` (`resolved_context.py`), `ResolvedWorkspaceGraph
  .resolved_assets`, `_strata_assets()` (`terraform_projection.py`), and
  the `strata_variables.tf` type's new `assets` entry, following the
  `ResolvedModule.source_path` precedent (orchestrator resolves, the
  Integration only ever reads already-resolved plain data). This forced a
  real revision to "Sync mechanism": computing `strata.assets` needs the
  asset copy to have already happened *before* the step loop starts, the
  same timing `properties`/`tenant` already have — so `sync_asset()` is no
  longer called lazily per `step.assets` entry inside the loop; instead
  `build_controller.py` statically scans every step's `assets` up front
  into one name set and resolves each exactly once, before the loop,
  which also deletes the earlier no-op/idempotency concern structurally
  rather than patching it. Also made explicit that `strata.assets`
  deliberately deviates from "every category always emits the full
  workspace pool" — it's keyed only by names this build's steps actually
  reference, not the full `spec.assets` pool, because (unlike every other
  category) an entry here requires a real directory to exist on disk.

