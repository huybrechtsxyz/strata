# Cross-Document Value References — `${value:kind.name.path}` — Design

- Status: **implemented — all 7 phases done (2026-09-29)**. `${value:kind.name.path}`
  is a fully working 5th Value-token kind: recognized syntactically,
  resolved against the loaded `DocumentIndex` (`value_references.py`),
  merged into deploy-time delivery (`resolve_values()`), and checked at
  `strata validate` time (`semantic_checks.py`'s `_check_value_references()`).
  Live-migrated into `.v2-cfg`'s real `environments/c0062-env.yaml` as
  proof (see Phase 6 below) — `strata validate .v2-cfg` passes clean,
  12/12, with a real `${value:tenant.c0062.meta.name}` reference in place.
  **2026-10-01: the one real limitation Phase 6 found and documented — a
  `${value:...}` token inside a `store: constant` variable/secret/feature's
  own `value` field was never resolved — is now fixed** (see the new
  entry below); `.v2-cfg`'s own `variables[].value` occurrence, left as a
  literal at the time for exactly this reason, can now be migrated too.
- Date: 2026-09-29
- Related: [value-token-resolution.md](value-token-resolution.md) (the
  existing `${var:}`/`${secret:}`/`${feature:}`/`${output:}` mechanism this
  extends), [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (the original Value-token decision), [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)
  (`(kind, name)` document identity — this design's addressing scheme),
  [deploy-command.md](../work/deploy-command.md) (`${output:}`'s own dedicated
  design, the closest precedent for a token kind resolved outside
  `value_tokens.py` itself)

## Problem — real, concrete duplication found in `.v2-cfg`

Building `.v2-cfg` (docs/work/gap_fit_v1.md's coverage-check fixture) surfaced a
real authoring problem: the literal string `c0062` appears independently,
by hand, in two separate documents that both already agree it's the same
tenant:

```yaml
# tenants/c0062.yaml
meta:
  name: c0062          # <-- the canonical name
spec:
  display_name: "GSK"
```

```yaml
# environments/c0062-env.yaml
spec:
  properties:
    environment_info:
      customer_code: "c0062"   # <-- duplicated by hand
  variables:
    - key: customer_code
      store: constant
      value: "c0062"           # <-- duplicated again, same file
```

Nothing keeps these in sync. Rename the tenant, or fix a typo in one, and
the other two are now silently wrong — exactly the class of problem the
existing `References()` marker (ADR-0015) already solves for *identity*
references (`workspace.spec.providers: [azure-westeurope]` must name a
real Provider), but `References()` only checks **existence**, never
**value** — it can't say "and also, copy that document's own name/field
in here instead of retyping it."

## Proposed syntax

A 5th Value-token kind, alongside the existing `var`/`secret`/`feature`/
`output` (docs/work/gap_fit_v1.md gap #11, [value-token-resolution.md](value-token-resolution.md)):

```
${value:kind.name.path.to.field}
```

Normalized to the *existing* `${kind:key}` grammar (colon after the kind,
matching `var`/`secret`/`feature`/`output` exactly) rather than the
dot-after-`value` shorthand floated when this was first raised — one
consistent token grammar, not a second one. Worked example from the
problem above:

```yaml
# environments/c0062-env.yaml — after
spec:
  properties:
    environment_info:
      customer_code: "${value:tenant.c0062.meta.name}"
  variables:
    - key: customer_code
      store: constant
      value: "${value:tenant.c0062.meta.name}"
```

- `kind` — one of `PlatformKind`'s values (`common_models.py`) —
  `tenant`, `provider`, `resource`, `workspace`, `environment`, etc.
- `name` — the target document's `meta.name` (`(kind, name)` is exactly
  ADR-0015's own document identity — ***this reuses that identity
  scheme, not a new one***).
- `path` — a dot-separated path from the target document's own root
  (`meta.name`, `spec.display_name`, `spec.properties.tier`, ...) —
  deliberately **not** implicitly rooted at `spec.` the way some
  shorthand addressing schemes are, so `meta.*` fields (a document's own
  canonical name/description) are referenceable without a special case.

This parses unambiguously with the *existing* token-key regex
(`VALUE_TOKEN_PATTERN`'s `key` group is already `[A-Za-z0-9_.-]+` —
already permits dots, added for `output:step.key`) because `PlatformName`
(`common_models.py`) is `^[a-z][a-z0-9_-]*$` — **names can never contain a
dot** — so `kind`/`name` are always exactly the first two dot-separated
segments, and everything after is the path, with no grammar ambiguity to
resolve.

## Why a new kind, not a bigger `References()`

`References()` (`reference_fields.py`) already answers "does `(kind,
name)` exist" for a field whose *entire value* is a name (e.g.
`workspace.spec.providers[]`). It cannot answer this design's question —
"copy some other document's field value in here, possibly as part of a
larger string" (`"${value:tenant.c0062.meta.name}-prd"` interpolated
alongside a literal suffix, matching exactly how `${var:}`/`${secret:}`
already compose inside one string) — because a `References()`-marked
field's runtime value stays the typed name itself (`"c0062"`), never gets
*replaced* by something else. Extending `References()` to also carry a
resolved value would conflate two different jobs (existence-checking a
field the schema itself defines vs. arbitrary embedded string
substitution) that the existing `var`/`secret`/`feature`/`output` split
already keeps apart on purpose.

## Resolution model

### A key simplification found while designing this: cycles are impossible by construction

The MVP restriction below (a `${value:...}` target must already be a pure
literal — no token of *any* kind present in it) has a useful side effect:
if document B's targeted field itself contained `${value:A...}`, B would
fail the "target is a pure literal" check and be rejected as an invalid
target *before* any lookup graph could form. A cycle (A references B
references A) is therefore not just detected and rejected — it cannot
exist in the first place under this design, so **no cycle-detection code
is needed at all** for the MVP scope below. This is a deliberate,
name-checked scope cut (see "Deliberately out of scope" below), not an
oversight — chained/transitive `${value:}` resolution is real,
useful, and cut for exactly this reason.

### What "resolves" means here — one flat map, computed once, solution-wide

1. **Existence + type check (Phase 2, `strata validate`/`build run`)**: for
   every `${value:kind.name.path}` token found anywhere in the loaded
   solution (via the *already-generic* whole-document string walk
   `unresolved_value_tokens()`/`find_malformed_value_tokens()` already
   perform — no new walking code needed, only a new branch for this
   kind):
   - `kind` must be a real `PlatformKind` value.
   - `(kind, name)` must exist in the `DocumentIndex` (`index.get(kind,
     name)` — the exact same lookup `references.py`/`semantic_checks.py`
     already use everywhere). **Edge case, confirmed on review**:
     `kind: solution` will *always* fail this check, by construction, not
     a bug — the solution manifest's own kind is deliberately never
     indexed (`SolutionController._load_manifest()`'s own docstring: "Its
     own kind is not indexed", the same fact gap #3's Phase 2 check
     already had to account for). `${value:solution.*...}` therefore
     always reports `value_reference_unknown_document`, same as any other
     genuinely-missing document — no special-case handling needed, but
     worth documenting so it doesn't look like an oversight when someone
     hits it.
   - `path`, walked through the target document's `model_dump(by_alias=True,
     mode="json")` (dict/list traversal, integer segments index into
     lists), must resolve to something.
   - The resolved leaf must be a scalar (`str`/`int`/`float`/`bool`), not a
     `dict`/`list` — referencing a whole sub-object is out of scope (see
     below).
   - The resolved leaf, if a string, must contain **no** `${...}` token of
     any kind (var/secret/feature/output/value) — the "target must already
     be a pure literal" rule that also rules out cycles, above.
   - Each violation is its own diagnostic (`value_reference_unknown_kind`,
     `value_reference_unknown_document`, `value_reference_invalid_path`,
     `value_reference_not_scalar`, `value_reference_target_not_literal`).
2. **Substitution (deploy time, matching every other kind's own
   "Mechanism B is always deploy-time" rule, [value-token-resolution.md](value-token-resolution.md)'s
   Overview)**: once (1) passes, build one flat `dict[str, str]` — key
   `"kind.name.path"` (the exact string that already appears after
   `value:` in the token — no re-encoding), value the literal found above
   — and merge it into the *same* `ValueResolution.values` flat map
   `var`/`secret`/`feature`/`output` entries already live in
   (`resolved_context.py`). Every existing consumer of `resolved.values`
   (Terraform `TF_VAR_`/`env` delivery, Helm/Compose secret-tracking
   resolvers, `resolve_value_tokens()`'s own flat-dict lookup) needs
   **zero changes** — `resolve_value_tokens()` already resolves by `key`
   alone, indifferent to which kind prefixed it (confirmed directly in its
   own docstring: *"`kind` only matters to the token's author, not to
   resolution"*). This is the same reason `output:step.key` needed no
   changes to the substitution function itself when it was added.

### Where it's computed: `value_controller.py`'s `resolve_values()`, not a new module

`resolve_values(context, deployment_name, keys)` already has everything
needed — `context.controller.index` (the whole `DocumentIndex`) — to walk
every loaded document for `${value:...}` tokens and build the flat map
above, regardless of `deployment_name`/`keys` (this resolution is
solution-wide, not deployment-scoped — a real, deliberate difference from
`var`/`secret`/`feature`, which are only ever declared inside a reachable
Environment). Recommended: a new sibling function,
`resolve_document_value_references(index: DocumentIndex) -> tuple[dict[str, str], Diagnostics]`
(signature corrected on review — an earlier draft of this section omitted
`Diagnostics`, inconsistent with the "Impact map" section below, which
always needed it; `ValueResolution` already has its own `diagnostics`
field for exactly this purpose, so the second element merges there, not
into a second, parallel error-reporting path), called once inside
`resolve_values()`, its `dict[str, str]` merged into the returned
`ValueResolution.values` and its `Diagnostics` merged into
`ValueResolution.diagnostics` — one call site, reused by both `build_run()`
and `deploy_run()` (both already call `resolve_values()`), though only
`deploy_run()` currently does anything with `resolved.values` at all (see
"Impact map" below).

## Deliberately out of scope (this design; revisit only with real evidence, ADR-0003's own policy)

- **Transitive/chained resolution** (A's target field itself contains
  `${var:}`/`${output:}`/another `${value:}`) — the "target must be a pure
  literal" rule above rejects this outright. Revisit only if a real
  document needs to reference a field whose own value depends on
  something not yet known at validate time.
- **Referencing a whole sub-object/list** (`${value:tenant.c0062.spec}`
  as a whole, not one scalar field) — no real use identified yet, and
  "embed a token that expands to a whole YAML mapping inline in a string
  field" is a different, bigger problem (would need its own non-string
  substitution path, not `resolve_value_tokens()`'s per-string
  replacement).
- **Referencing across a remote-qualified document**
  (`@remote/name` — [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)'s
  own still-unimplemented feature, `DocumentRef.remote` always `None`
  today per `solution_controller.py`) — inherits that limitation for free,
  not a new one.
- **Wildcard/array-producing paths** (e.g. "every module in this
  namespace") — one path resolves to exactly one scalar, matching how
  `${var:KEY}`/`${output:step.key}` are both single-value lookups too.
- **`build run`-time substitution** — Mechanism B (embedded tokens) is
  deploy-time-only by design, for every kind, not just this one
  ([value-token-resolution.md](value-token-resolution.md)'s Overview,
  section B). `${value:...}` follows the same rule for consistency, even
  though — unlike secrets — nothing about a `value:` target is actually
  sensitive; the *reason* it's deploy-time is architectural consistency
  with the other three kinds sharing the same substitution pass, not a
  safety requirement specific to this kind.

## Dependency: universal resolution reach (2026-09-29)

**Requirement, stated directly (per request): resolution — not just
validation — must reach anywhere in `root.spec.*`, for all five kinds
including this proposed one, not a curated allowlist of fields/categories.**
Full write-up, and the real gap that motivated stating it explicitly
(`ProviderSpecModel.configuration`/`.custom` never projected into any
Terraform artifact at all, despite passing validation cleanly), lives in
[value-token-resolution.md](value-token-resolution.md)'s own "Decision
(2026-09-29): resolution reach must match validation reach" section — not
repeated here in full to avoid two copies of the same reasoning drifting
apart.

**Why `value:` inherits this for free, rather than needing its own fix**:
because `${value:...}` substitution reuses the exact same
`resolve_value_tokens()`/flat-`values`-map mechanism the other four kinds
already use (this doc's own "Resolution model" section above), fixing the
Terraform-delivery curation gap for `var`/`secret`/`feature`/`output`
automatically extends the same reach to `value:` — no separate
genericization work is needed for this 5th kind specifically. **Sequencing
matters**: this design should land *after*, or alongside, that fix, not
before it — implementing `value:` against today's curated delivery list
would silently inherit the same Provider-shaped blind spot (a
`${value:...}` token resolving correctly in the flat map but never reaching
a rendered Terraform artifact if its containing field is one of the
categories the curation gap drops).

## Impact map — every place this touches, so nothing here becomes dead code

Organized by how much changes, smallest first.

### No change needed (confirmed, not assumed)

- `resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`/
  `resolve_value_tokens_tracking_secrets()`/`resolve_value_tokens_renaming_secrets()`
  (`value_tokens.py`) — all resolve by flat `key` lookup already, blind to
  `kind`. A `value:` token's key (`"kind.name.path"`) is just another
  string key in the same dict these already consume.
- Every per-integration delivery mechanism (`terraform_projection.py`'s
  `TF_VAR_` payloads, `HelmIntegration.resolve_module_values()`,
  `ComposeIntegration.resolve_compose_values()`) — all consume
  `resolved.values`/the merged `tokens` dict, already populated upstream;
  none of them care which kind produced an entry.
- `references.py`'s generic `References()` walker — untouched; this is a
  parallel mechanism, not a replacement (see "Why a new kind" above).

### Small, additive change to an existing function

- **`value_tokens.py`**: add `"value"` to `VALUE_TOKEN_KINDS` and the
  `VALUE_TOKEN_PATTERN`/`_RESOLVE_VALUE_TOKEN_PATTERN` kind alternation
  (`var|secret|feature|output` → `var|secret|feature|output|value`) — the
  `key` group's existing `[A-Za-z0-9_.-]+` pattern already permits the
  extra dots a `kind.name.path` key needs; no regex redesign.
- **`environment_service.py`'s `unresolved_value_tokens()`**: add a
  `kind == "value"` early branch that skips the Environment-declared-keys
  path entirely (mirrors the `output_claimed` precedent, docs/work/gap_fit_v1.md
  gap #16) — `value:` existence is a `DocumentIndex` lookup, not an
  Environment concern, so this function should not judge it at all;
  validation moves to a dedicated new check instead (below).
- **`value_controller.py`'s `resolve_values()`**: call the new
  `resolve_document_value_references()` and merge its result into the
  returned `ValueResolution.values` (see "Where it's computed" above).

### New code

- **`resolve_document_value_references(index: DocumentIndex) -> tuple[dict[str, str], Diagnostics]`**
  (new function, `value_controller.py` or a new sibling module if it grows
  large enough to warrant one) — walks every document in the index for
  `${value:...}` candidates (reusing `find_malformed_value_tokens()`'s
  existing generic string-walk shape), resolves each per the "Resolution
  model" section above, returns the flat map plus one diagnostic per
  violation.
- **`semantic_checks.py`**: new `_check_value_references(index)` — a 9th
  Phase 2 check (after gap #3's `_check_remotes()`, which is the closest
  existing precedent for "a solution-wide, not deployment-scoped, check").
  Wired into `run_semantic_checks()` unconditionally (no `resolved`/
  `solution` parameter needed beyond the `index` already passed to every
  other check) — this check does not care which deployment(s) exist, only
  whether every `${value:...}` reference anywhere in the whole solution is
  well-formed and points at a real, literal, scalar field. Likely reuses
  `resolve_document_value_references()`'s own Diagnostics output directly,
  so `strata validate` and the real deploy-time resolution share one
  implementation, not two.

### Documentation

- **This document** — the full design.
- [value-token-resolution.md](value-token-resolution.md) — add `value` to
  the "Syntax" bullet's kind list and the Per-Kind Status table's framing
  (a `value:` row would read differently from the others — "global, not
  per-kind" — since this kind isn't scoped to specific model fields the
  way `DnsRecordModel.value`/`SubnetModel.cidr` are; it's valid in any
  string field any other kind already walks generically).
- `docs/work/gap_fit_v1.md` — **not** a gap entry (this is a net-new feature no
  version of v1 ever had — v1's real `ValueSourceModel` discriminated
  union predates even the `var`/`secret`/`feature` design this extends,
  per [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)).
  No entry needed there; this doc is the record.

## Deferred

- Whether `build run` should also gain the ability to consume
  `resolved.values` (today discarded by `TerraformIntegration.
  default_output()`'s own `del resolved`) so a `value:`-bearing field
  renders resolved even before `deploy run` — Mechanism B is deploy-time-
  only for every kind today; changing that is a bigger, separate decision
  than adding a 5th kind to it.

## History

- Motivated by a real, concrete duplication found while building `.v2-cfg`:
  the same literal `"c0062"` typed independently in both `tenants/c0062.yaml`
  and `environments/c0062-env.yaml`.
- Scoping "universal resolution reach" (this design must work anywhere in
  `root.spec.*`, not just specific model fields) surfaced a real
  prerequisite gap: `ProviderSpecModel.configuration`/`.custom` passed
  validation but was never projected into any Terraform artifact at all —
  fixed as part of [value-token-resolution.md](value-token-resolution.md)'s
  own delivery generalization, not duplicated here.
- `kind: solution` can never be a `${value:...}` target — the solution
  manifest is deliberately never indexed, the same constraint other
  cross-document checks already have to account for.
- "Cycles are impossible by construction" was verified directly against
  several concrete cases (self-reference, a target containing a
  different-kind token, a partial-token string, a same-kind chain) — held
  in every case checked, not just asserted.
- `resolve_document_value_references()` needed its own module
  (`value_references.py`), not because the logic grew large, but to break
  a real circular import: `semantic_checks.py` (imported by
  `solution_context.py`) and `value_controller.py` (which imports
  `solution_context.py` for the `SolutionContext` type) both needed the
  function. Neither mypy, ruff, nor static analysis caught the cycle —
  only running the test suite surfaced the real `ImportError`.
- A bare `${value:onlyonesegment}` surfaces as `value_reference_unknown_kind`
  (the one segment isn't a valid `PlatformKind`); `value_reference_invalid_path`
  is reserved for a valid kind with no name segment at all (e.g. a bare
  `${value:tenant}`).
- A real, pre-existing limitation surfaced via a live `.v2-cfg` migration
  proof: a Value token embedded inside a `store: constant`
  variable/secret/feature's own value was never resolved by
  `_resolve_store_value()`/`build_value_references()`. Fixed for the
  deploy-time path only (`_resolve_store_value()` now resolves a
  `${value:...}` token already present in a constant's value) — reusing
  the existing mechanism rather than adding a parallel `store: value` kind.
  Deliberately left unchanged for the build-time path
  (`build_value_references()`), matching the existing "build renders
  literally, deploy resolves" split (ADR-0022 D4).

