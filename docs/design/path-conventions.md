# Path Conventions — `Configuration.spec.paths` — Design

- Status: **implemented (2026-09-30)** — the `pattern`/`filename_pattern`
  split is fully re-implemented (was one combined full-path `pattern` in
  the 2026-09-29 first pass). All 4 phases done: model, structural path
  matching, `DeploymentLayersModel` cross-check, documentation.
- Date: 2026-09-29 (redesigned and re-implemented 2026-09-30)
- Related: [docs/work/gap_fit_v1.md](../work/gap_fit_v1.md) gap #7 (the `paths` remainder —
  `security`/`zones`/`remotes`/`integrations` already resolved),
  `deployment_model.py`'s `DeploymentLayersModel` (now cross-checked, was
  **inert in v2** before this design), `tenant_model.py` (already solved
  `zones` differently — the same "convert cleanly, not 1:1" discipline
  applies here), [solution-loading-and-phase2-validation.md](../work/solution-loading-and-phase2-validation.md)
  (the Phase 2 check registry this adds one more entry to)

## Problem — real, concrete evidence from `config-deploy`

Read directly (`e:\sources\config-deploy\config\paths.yaml`, 2026-09-29):
v1 declares one "path convention" per real directory family —
`tenant-path`, `provider-path`, `control-path`, `hub-path` — each a
`pattern` template (`"deploy/hubs/{hub}/{spoke}/{customer}/{ring}/{environment}"`)
that every file under its `scope` (`"deploy/hubs/**"`) must match. Enforced
today via exactly **one** enabled policy: `enforce-path-conventions`
(`type: path_convention`, phase `validate`, `enforcement: warn`, no
`configuration:` override — checks every declared convention).

Two consumers depend on this that v2 has zero equivalent for:

1. **Structural authoring correctness** — a file at the wrong depth/shape
   for its kind (a customer's `tenant.yaml` filed one directory too deep, a
   provider file not named after its region) is currently undetectable in
   v2. Nothing walks the real, on-disk file layout at all.
2. **`DeploymentLayersModel`** (`deployment_model.py`) — modelled already
   (`follows`/`segments`) because "10 of 15 real deployments declare it,"
   but its own docstring says outright: **"Inert in v2 ... nothing resolves
   or validates it yet."** A real leaf deployment
   (`deploy/hubs/z00/s01/c0224/dev/deployment.yaml`) declares:
   ```yaml
   layers:
     follows: hub-path
     segments:
       hub: z00
       spoke: s01
       customer: c0224
       ring: dev
       environment: na
   ```
   `follows: hub-path` names a `spec.paths` entry; `segments` are that
   convention's per-segment captures. Nothing in v2 checks either field
   means anything real.

## What's already solved differently, not a gap here

Before modeling anything, checked whether `paths.yaml`'s two `validate:`
sub-cases need a v2 equivalent at all — following gap #7's own precedent
(`zones` turned out to already be superseded by `TenantSpecModel.
geographies`, not a gap to port 1:1):

- **`provider-path`'s `{region}` check** (`kind: yaml`, `expression:
  "spec.providers[*].regions[]"` — the captured region must be a real,
  registered provider region) — **already covered**, more precisely, by
  `_check_providers()`/`ProviderService` today: a `Provider` document's
  `spec.properties.region` is already cross-checked against its
  `ProviderConfig`'s real `spec.regions[*].name`
  (`test_provider_region_invalid_for_its_config_is_caught`). A generic
  YAML-expression evaluator would only re-derive a check v2 already does
  better (against the real typed registry, not a same-file embedded list
  v1 used before promoting providers to a standalone kind).
- **`hub-path`'s `{customer}` check** (`kind: path`, `expression:
  "customers/{customer}/tenant.yaml"` — the captured customer code must
  name a real tenant file) — **already covered** by ordinary reference
  validation, provided the deployment declares `spec.tenant` (the real
  leaf does: `tenant: c0224`): `DeploymentSpecModel.tenant`'s
  `References(PlatformKind.TENANT)` annotation already makes this a
  Phase 1 existence check, keyed on identity (ADR-0015), not a
  file-existence probe.

Conclusion: **the generic `validate:` sub-block (`kind: yaml`/`kind: path`
expressions) is deliberately not ported.** Every real instance of it
already has a better, more direct v2 equivalent. Porting it anyway would
mean building a small YAML-expression/JSONPath interpreter for a feature
whose only two real call sites are already redundant.

What's left, and genuinely missing, is the **structural** shape check
itself (does this file's real location match the declared `pattern`) plus
making `DeploymentLayersModel` real.

## Proposed design

### 1. `PathConventionModel` (new), `ConfigurationSpecModel.paths` (new field)

```python
class PathConventionModel(PlatformBaseModel):
    """One directory-family naming convention (docs/work/gap_fit_v1.md gap #7)."""

    name: PlatformName = Field(description="Convention name, referenced by DeploymentLayersModel.follows")
    scope: str = Field(
        description="Directory prefix this convention governs, e.g. 'deploy/hubs/**'. Deliberately narrower "
        "than a full glob engine (see 'Deliberately out of scope') — always '<relative-dir>/**'."
    )
    pattern: str = Field(
        description="Directory template with {segment} captures, e.g. 'deploy/hubs/{hub}/{spoke}/{customer}/"
        "{ring}/{environment}'. Matched against a document's containing directory only, relative to the "
        "solution root — the filename itself is a separate, independent match (see filename_pattern)."
    )
    filename_pattern: str | None = Field(
        None,
        description="Optional {segment}-capable template matched against just the filename (not the whole "
        "path), e.g. 'tenant.yaml' or '{region}.yaml'. None (default) accepts any filename under a directory "
        "that matches 'pattern' — not every real convention cares what the file is called (see 'Why pattern "
        "and filename_pattern are split' below).",
    )
    resolves: Literal["tenant", "layers"] | None = Field(
        None,
        description="'tenant': the single {code}-shaped capture (from 'pattern' or 'filename_pattern', "
        "whichever declares it) is cross-checked against the real Tenant document filed there (meta.name). "
        "'layers': this convention's segments are DeploymentLayersModel's resolution target — see "
        "'segments' below. None: structural shape check only, no further meaning.",
    )
    segments: list[PathSegmentModel] | None = Field(
        None, description="Per-segment name + validation pattern. Required when resolves == 'layers'."
    )
    enforcement: Literal["warn", "deny"] = Field(
        "warn",
        description="'warn' (default, matches the one real enabled policy today) reports a mismatch without "
        "failing validate; 'deny' fails it. Deliberately modeled directly here rather than through a "
        "separate policy document — see 'Deliberately out of scope'.",
    )


class PathSegmentModel(PlatformBaseModel):
    name: str = Field(description="Segment name, matching a {name} capture in pattern or filename_pattern")
    pattern: str = Field(description="Regex the captured value must match, e.g. '^[a-z]{3}$' for a 3-letter ring")
    description: str | None = Field(None, description="What this segment represents")
```

### Why `pattern` and `filename_pattern` are split

An earlier pass had one field matching a document's **full** path,
filename included — abandoned after two real problems surfaced:

1. **Not every filename is fixed, or even relevant.** v1's own real
   `hub-path`/`control-path` conventions never mention the filename at all
   (they match only the containing directory — a deployment could
   plausibly be named anything under that directory and the convention
   wouldn't care). Requiring a literal filename in one combined pattern
   would have forced every convention to spell one out even when nothing
   about the design needs it.
2. **`provider-path`'s real case needs the *opposite* split**: its capture
   (`{region}`) lives entirely in the filename (`providers/westeurope.yaml`)
   — the directory itself (`providers/`) has no capture at all. A single
   combined pattern can express this, but conflates two genuinely
   independent questions ("what directory shape" vs. "what filename
   shape") into one string.

Splitting them answers both: `pattern` describes the directory shape
(optional captures), `filename_pattern` independently describes the
filename shape (optional captures, and optional itself — omitted means
"any filename accepted here"). Both use the identical `{name}`-token
templating (`strata/utils/path_conventions.py`'s one `compile_pattern()`),
just matched against a different substring of the real relative path —
not two separate mechanisms.

`PathSegmentModel.pattern` is deliberately a **static regex only** — it
cannot look up a value from anywhere else in the model. That is exactly
the capability the dropped `validate: {kind: yaml, expression: ...}`
sub-block provided (see "What's already solved differently" above), and
this design does not re-introduce a general mechanism for it. If a future
real case needs a segment cross-checked against a *dynamic* value from
another document (not just "is it shaped like 3 lowercase letters") — e.g.
"`{ring}` must be one of this tenant's declared rings" — the answer is the
same one this codebase already uses everywhere else for a conditionally-
meaningful cross-reference: write one small, dedicated function in
`semantic_checks.py` (or the owning service) for that *specific* check,
the same way `_check_providers()`/`ProviderService` cross-check a
`Provider`'s region against its real `ProviderConfig`, or
`TenantService.validate_geographies_against_provider_configs()` does for
geographies. It would consume the segment value `match_directory()`/
`match_filename()` already captured via its regex group, then look up
whatever real field is authoritative — never a generic expression string
declared in the schema itself. No such concrete need exists yet (see the
two real `validate:` cases above — both turned out to already be covered
elsewhere), so no extension point is being spec'd speculatively here;
this paragraph exists only so a future reader doesn't reach for "add a
`validate:` field back" as the first idea.

`ConfigurationSpecModel.paths: list[PathConventionModel] | None`, following
the exact `providers`/`topologies`/`security` precedent (optional, `None` =
no conventions declared = nothing checked). No `additional_paths: bool` —
a file simply outside every declared `scope` is unchecked, not rejected;
there is no "closed universe" concept here the way there is for
`topologies`/regions.

### Worked examples — one per `resolves` value

All three converted directly from the real `config-deploy/config/
paths.yaml` entries (minus the dropped `validate:` sub-block).

**`resolves: tenant`** — one directory capture, cross-checked against the
real `Tenant` document filed at that path, nothing else. `filename_pattern`
pins the literal filename `tenant-path` cares about (real usage: every
tenant file really is named `tenant.yaml`, so this is a plain literal, not
a capture — but it could be a looser regex, e.g. `"^(tenant|config)\.yaml$"`,
if an author genuinely varied it):

```yaml
# kind: configuration, spec.paths
- name: tenant-path
  scope: "customers/**"
  pattern: "customers/{code}"
  filename_pattern: "tenant.yaml"
  resolves: tenant
```

```yaml
# customers/c0062/tenant.yaml — passes: {code} captures "c0062",
# which must equal this file's own meta.name.
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0062
spec:
  display_name: Acme
  geographies: [europe]
```

A `meta.name: c0099` at that same path would be caught — the folder says
`c0062`, the document disagrees.

**`resolves: layers`** — multiple directory captures, each independently
validated against its own declared `segments[].pattern`, and (Phase 3)
cross-checked against any `Deployment.spec.layers.segments` that names
this convention. No `filename_pattern` — matches v1's own real
`hub-path`, which never cared what the deployment file itself was named:

```yaml
# kind: configuration, spec.paths
- name: hub-path
  scope: "deploy/hubs/**"
  pattern: "deploy/hubs/{hub}/{spoke}/{customer}/{ring}/{environment}"
  resolves: layers
  segments:
    - name: hub
      pattern: "^[a-z][a-z0-9-]*$"
      description: "Acme hub — one physical/logical infrastructure hub"
    - name: spoke
      pattern: "^[a-z][a-z0-9-]*$"
    - name: customer
      pattern: "^[a-z][a-z0-9]{4}$"
    - name: ring
      pattern: "^[a-z]{3}$"
    - name: environment
      pattern: "^[a-z0-9]{1,4}$"
```

```yaml
# deploy/hubs/z00/s01/c0224/dev/deployment.yaml — path captures
# {hub: z00, spoke: s01, customer: c0224, ring: dev, environment: dev}
# (or whatever the real leaf's final directory segment is), each checked
# against its own segment pattern above.
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: deploy-hub-z00-spoke-s01-customer-c0224-ring-dev
spec:
  layers:
    follows: hub-path
    segments:
      hub: z00
      spoke: s01
      customer: c0224
      ring: dev
      environment: na   # must still match ^[a-z0-9]{1,4}$ — it does
  tenant: c0224
```

A hand-typed `segments: {ring: development, ...}` (7 chars, fails
`^[a-z]{3}$`) would be caught at the model's own Pydantic level — the
schema, independent of path matching. Where the *path* check adds value:
`ring: dev` declared but the real directory is actually `deploy/hubs/z00/
s01/c0224/tst/deployment.yaml` — schema-valid on its own, only the
path-vs-declared-`segments` cross-check (Phase 3) catches the drift.

**`resolves: None`** — pure structural shape check, no further meaning.
Also the motivating real case for `filename_pattern` needing its own
captures: `{region}` lives entirely in the filename here — the directory
(`providers/`) has no capture at all:

```yaml
# kind: configuration, spec.paths
- name: provider-path
  scope: "providers/**"
  pattern: "providers"
  filename_pattern: "{region}.yaml"
  # resolves omitted — the {region} capture means nothing further to v2
  # (already cross-checked elsewhere; see "What's already solved
  # differently" above). Only the file's shape/location is enforced.
```

```yaml
# providers/westeurope.yaml — passes: directory matches the literal
# "providers", filename captures region="westeurope".
# providers/azure/westeurope.yaml would fail it (wrong directory depth),
# even though nothing here ever inspects {region}'s actual value.
```

### 2. Pattern-matching mechanics (new: `strata/utils/path_conventions.py`)

`scope` is deliberately restricted to `"<relative-dir>/**"` — every real
instance is this shape, and a plain string-prefix check
(`relative_path.startswith(scope.removesuffix("**"))`) covers it without a
general glob engine.

`pattern` and `filename_pattern` are matched against two different
substrings of a document's real relative path, split via its final `/`:
`pattern` against everything before it (the containing directory,
possibly nested), `filename_pattern` (when set) against everything after
it (the bare filename). Both compile through the same templating: each
`{name}` token becomes a named regex group matching one path segment
(`[^/]+`, i.e. never crossing a `/`) — `"customers/{code}"` ->
`^customers/(?P<code>[^/]+)$` for a directory, `"{region}.yaml"` ->
`^(?P<region>[^/]+)\.yaml$` for a filename. A missing `filename_pattern`
means the filename is never even split out from the path — the directory
check is skipped straight to its own end anchor, since there is nothing
further to constrain. Three functions:

```python
def compile_pattern(pattern: str) -> re.Pattern[str]: ...
def match_directory(relative_path: str, pattern: str) -> re.Match[str] | None:
    """Matches `pattern` against everything before the final '/' in relative_path
    (the whole string, if there is no '/' — a document directly at the solution root)."""
def match_filename(relative_path: str, filename_pattern: str) -> re.Match[str] | None:
    """Matches filename_pattern against everything after the final '/' in relative_path."""
```

`_check_document_against_path_convention()` (`semantic_checks.py`, section
3 below) is the one real call site combining both: a convention matches
only when its directory pattern matches AND (no `filename_pattern`, or
`filename_pattern` also matches) — captures from both halves merge into
one `dict` (a segment name must not appear in both, though no real
convention needs that and it is not specially guarded against; see Open
Question #4).

**Edge case: a document with no `/` in its relative path** (filed
directly at the solution root). The split has nothing to split on, so the
whole string is the "directory" half and the "filename" half is empty.
No real convention's `scope` (always `"<dir>/**"`) can ever match a
root-level file anyway, so this only matters if a future `scope` ever
did — noted here so that case has a defined, non-crashing answer rather
than an implicit one discovered mid-implementation.

### 3. Semantic check (`semantic_checks.py`, new `_check_paths()`)

Wired into `run_semantic_checks()` beside the other Configuration-backed
checks, same "exactly one Configuration document, else skip" rule
`_check_workspace_topology_components()`/the new `security` check already
use. Needs each document's real file path relative to the solution root —
`IndexEntry.source` is already absolute, so `run_semantic_checks()` gains a
`root: Path` parameter (`solution_context.py`'s one call site already has
`self.controller.root` on hand, same as it already threads `solution`
through for `_check_remotes()`).

For every indexed document (`index.all()`):

1. Compute `relative = entry.source.relative_to(root).as_posix()`.
2. Find the first convention whose `scope` prefix matches. No match ->
   skip (unscoped file, nothing to check).
3. In scope but the directory (`pattern`) doesn't match, or it matches but
   the filename doesn't match a set `filename_pattern` -> one finding
   (`warning()` or `error()`, per that convention's own `enforcement`),
   code `path_convention_mismatch`.
4. In scope and both match -> when `resolves == "tenant"`: the captured
   `{code}`-named group (from whichever half declared it) must equal a
   real `TenantModel.meta.name` indexed at exactly this path (cross-checks
   the file's actual content against its own location, catching a
   copy-pasted `meta.name` that doesn't match the folder it was filed
   under). When `resolves == "layers"`: check each declared
   `segments[].pattern` against the corresponding capture (a segment whose
   real path value doesn't match its own declared regex is the same class
   of finding as v1's real, empirically-found "environment folder > 4
   chars breaks the AKS pool name limit" case).

### 4. `DeploymentLayersModel` cross-check (extends `_check_deployments()`)

When a `Deployment` document's `spec.layers.follows` names a real,
`resolves: layers` convention: if `spec.layers.segments` is already
declared, it must agree with the values captured from the deployment
document's own real path — specifically, from `match_directory()` against
that convention's `pattern` (a `layers`-resolving convention's segments
are always directory captures in every real case; see the worked examples
above), compared element-by-element. This is validation only, matching
this codebase's existing convention that Phase 2 checks never mutate an
already-validated model (`WorkspaceService.validate_topology_references()`
et al. all only produce `Diagnostics`). Auto-populating
`spec.layers.segments` when absent, and any actual *consumer* of
`DeploymentLayersModel` (tagging, naming, etc.), stay out of scope —
nothing in v2 reads `layers` for anything today, so there is nothing yet
to feed.

## Deliberately out of scope

- **The generic `validate:` sub-block** (`kind: yaml`/`kind: path`
  expressions) — see "What's already solved differently" above. Both real
  instances are already redundant with existing, more precise v2 checks.
- **The full policy engine** (`policies.yaml`'s `type`/`phase`/
  `enforcement` dispatch covering `tenant_zone`, `naming_pattern`,
  `cve_max_severity`, `checkov`, `cost_threshold`, `resource_type_
  restrictions`, `ai_review`, `script`, `opa`, etc.) — `path_convention` is
  the one real, active policy type; building a generic phase-dispatching
  policy framework to host just this one is disproportionate. `enforcement`
  is modeled directly on `PathConventionModel` instead (see design above) —
  a deliberate, narrower v2-native shape, same discipline as `Integration`
  becoming a standalone kind instead of an embedded list.
- **Auto-populating `DeploymentLayersModel.segments` from the real path**
  when absent — v1's real, primary behavior, but with no v2 consumer of
  `layers` yet to justify writing derived data back onto a validated model
  (a pattern this codebase does not otherwise use). Revisit once something
  actually reads `spec.layers` (tagging, node-pool naming, etc.).
- **`provider-path`'s filename-vs-`meta.name` check** — v1's own comment
  concedes this "still relies on review," not automated there either;
  not introduced here.
- Everything else `paths.yaml`'s own comment block documents as still
  unadopted in the real repo (`lifecycle`, `cost`, `drift` — gap #7's
  remaining `audit`/`promotions` items, unrelated to `paths` specifically).

## Impact map

- `configuration_model.py` — new `PathConventionModel` (with `pattern` +
  `filename_pattern`)/`PathSegmentModel`, new `ConfigurationSpecModel.paths`
  field.
- `strata/utils/path_conventions.py` (new file) — `compile_pattern()`/
  `match_directory()`/`match_filename()`, pure string/regex logic, no
  Pydantic dependency (same layer as `path_safety.py`).
- `semantic_checks.py` — new `_check_paths()`; `_check_deployments()`
  extended with the `layers` cross-check; `run_semantic_checks()` gains a
  `root: Path` parameter.
- `solution_context.py` — its one `run_semantic_checks(...)` call site
  passes `self.controller.root`.
- `docs/work/gap_fit_v1.md` gap #7 — mark the `paths` sub-item resolved once
  implemented, same as `security`'s own entry.


## History

- `segments` is required (schema-level) whenever `resolves: layers` - a layers-resolving convention with no declared segments could never be cross-checked against anything.
- An unknown `layers.follows` name is an error only when a single `Configuration` document exists to check it against; absent/ambiguous Configuration still skips the whole check (same "nothing declared, nothing to check" rule used elsewhere in this codebase).
- `scope` stays a plain string-prefix (`"<dir>/**"`) - no real counter-example in any available real workspace; revisit only if one appears.
- The same `{name}` appearing in both `pattern` and `filename_pattern` is deliberately left unguarded - no real convention has ever needed a segment name in both halves.
- v1's real `hub-path`/`control-path` conventions match only the containing directory (no filename); this implementation's `pattern` always matches the full relative path including filename - simpler and unambiguous, at the cost of a migrated `layers`-resolving convention needing an explicit trailing filename segment it didn't need in v1.
