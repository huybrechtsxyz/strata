# Cross-Document Value References — `${value:kind.name.path}` — Design

- Status: **implemented — all 7 phases done (2026-09-29)**. `${value:kind.name.path}`
  is a fully working 5th Value-token kind: recognized syntactically,
  resolved against the loaded `DocumentIndex` (`value_references.py`),
  merged into deploy-time delivery (`resolve_values()`), and checked at
  `strata validate` time (`semantic_checks.py`'s `_check_value_references()`).
  Live-migrated into `.v2-cfg`'s real `environments/c0062-env.yaml` as
  proof (see Phase 6 below) — `strata validate .v2-cfg` passes clean,
  12/12, with a real `${value:tenant.c0062.meta.name}` reference in place.
- Date: 2026-09-29
- Related: [value-token-resolution.md](value-token-resolution.md) (the
  existing `${var:}`/`${secret:}`/`${feature:}`/`${output:}` mechanism this
  extends), [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (the original Value-token decision), [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)
  (`(kind, name)` document identity — this design's addressing scheme),
  [deploy-command.md](deploy-command.md) (`${output:}`'s own dedicated
  design, the closest precedent for a token kind resolved outside
  `value_tokens.py` itself)

## Problem — real, concrete duplication found in `.v2-cfg`

Building `.v2-cfg` (docs/_gap_v1.md's coverage-check fixture) surfaced a
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
`output` (docs/_gap_v1.md gap #11, [value-token-resolution.md](value-token-resolution.md)):

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
  path entirely (mirrors the `output_claimed` precedent, docs/_gap_v1.md
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
- `docs/_gap_v1.md` — **not** a gap entry (this is a net-new feature no
  version of v1 ever had — v1's real `ValueSourceModel` discriminated
  union predates even the `var`/`secret`/`feature` design this extends,
  per [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)).
  No entry needed there; this doc is the record.

## Open questions for review (explicitly not decided here)

1. ~~**Is `resolve_document_value_references()` a `value_controller.py`
   function or its own module?**~~ **Resolved (2026-09-29, Phase 5):** its
   own module, `value_references.py` — not because the logic grew large
   (it didn't), but because implementation surfaced a real, concrete
   reason: `semantic_checks.py` (imported by `solution_context.py`) and
   `value_controller.py` (which imports `solution_context.py` for the
   `SolutionContext` type) both need this function, so having it live in
   `value_controller.py` created a genuine circular import once
   `semantic_checks.py` needed it too. See that phase's changelog entry
   for the full root-cause writeup.
2. **Should `build run` also gain the ability to consume
   `resolved.values`** (today discarded — `TerraformIntegration.
   default_output()`'s own `del resolved`, a pre-existing, separately-
   tracked narrow gap) **so a `value:`-bearing field renders resolved even
   before `deploy run`?** Deliberately out of scope here — Mechanism B is
   deploy-time-only for every kind today, and changing that is a bigger,
   separate decision than adding a 5th kind to it. Still open; not
   revisited by Phases 0-5.
3. ~~**Severity of `strata validate`'s new check**~~ **Resolved (2026-09-29,
   Phase 5):** always an error, as recommended below — confirmed directly
   in `resolve_document_value_references()`'s implementation, which only
   ever calls `diagnostics.error(...)`, never `.warning()`, for any of its
   five failure modes.

## Implementation Plan

Phased so each step is independently shippable and independently testable
— matching this doc family's own established convention
([value-token-resolution.md](value-token-resolution.md)'s "Full Solution"
plan). Ordered by dependency, not by size: 0-1 are the prerequisite fix
this design depends on (its own gap, not new work invented for `value:`);
2-6 are the new kind itself.

### Phase 0 — Guardrail test (no dependencies, do this first) — ~~IMPLEMENTED (2026-09-29)~~

Added `test_every_infra_integration_declares_a_resolved_value_delivery_mechanism()`
(`tests/strata/integrations/test_integrations_capabilities.py`), walking
`InfraIntegration.__subclasses__()` **recursively** (matching
`test_commands_exit_codes.py`'s `_all_error_types()` shape exactly) and
asserting each registered class sets `ENV_VAR_PREFIX` or declares
`Capability.CONTAINER`. Two real complications found and fixed during
implementation, not hit in the design:

1. **Test-fixture pollution** — this exact test file already has a
   module-level `_Bare(InfraIntegration)` fixture (its own docstring: "a
   minimal concrete InfraIntegration... Bicep's real... behaviour") that
   would have failed the guard immediately, a false positive. Fixed by
   filtering to `cls.__module__.startswith("strata.")` — excludes any
   test-only fixture, present or future, without a per-class allowlist.
2. **Lazy import** (`registry.py`'s own documented reason —
   `import_module()` only runs when `registry.get()`/`_resolve_class()` is
   actually called) — a class never imported never registers as a
   `__subclasses__()` entry at all, so the walk found zero classes until
   every `registry._KNOWN` entry was force-loaded (class only, never
   instantiated, via `registry._resolve_class()`) first.

A companion test, `test_every_infra_integration_type_is_a_known_real_integration()`,
names the three covered integrations explicitly so a future fourth one is
visible in a diff, not just implicitly covered. Passes today (22 tests in
the file, all green); full check suite green (mypy `src` clean — the
project's own convention never mypy-checks `tests/`, ruff `src tests`
clean, import-linter 1 kept/0 broken, pytest 1256 passed). Zero behavior
change to production code — pure test addition.

### Phase 1 — Universal Terraform-delivery resolution reach (the Provider gap fix) — ~~IMPLEMENTED (2026-09-29)~~

Implemented per the design below, with one real deviation found during
implementation: kept `build_dns_networks_firewalls_payloads()` and
`build_configuration_payloads()` as **two** functions, not one — a large
existing test suite (`test_integrations_terraform_projection.py`) imports
`build_configuration_payloads()` directly and asserts its exact category
set, so folding both into a brand-new single function would have meant
rewriting those tests' intent rather than extending it; the "one function"
goal is satisfied in spirit (deploy_controller.py's own two call sites are
unchanged, `build_configuration_payloads()` itself now iterates all ten
non-claimable categories generically instead of a curated 5) without a
gratuitous rename.

- `_build_providers_payload()` (`terraform_projection.py`) now includes
  `configuration`/`custom` (empty dict when unset) — the confirmed real
  gap itself, verified live against `.v2-haven`'s real
  `hetzner-eu-de.yaml` (which sets both, with plain literals today).
- `build_configuration_payloads()` extended from 5 to 10 categories:
  `workspace`/`providers`/`resx_<type>`/`topologies`/`namespaces`/`flags`/
  `variables`/`properties`/`custom`/`tenant`. `dns`/`networks`/`firewalls`
  keep their unchanged, separate per-name claiming rule (gap #12) via
  `build_dns_networks_firewalls_payloads()`.
- **One design assumption corrected during implementation**: "flags/
  variables/workspace/namespaces genericizing is a true no-op... since
  none carry a token-bearing field" doesn't mean these categories stay
  *absent* — `workspace` is unconditionally present in every real solution
  (a workspace document always has a non-empty `name`) and `providers` is
  present whenever any provider is configured (virtually always). "No-op"
  here means what actually matters: resolving these categories' values
  changes nothing, because none of them realistically carry a
  `${var:}`/`${secret:}`/`${feature:}` token — not that they contribute an
  empty dict. Test assertions were written against this corrected
  understanding, not the original wording.
- `deploy_controller.py` needed **zero logic changes** — it already called
  `build_configuration_payloads(graph)` generically; only its explanatory
  comments (referencing "five" categories) were updated to "ten".
- **Tests**: 3 new (`test_providers_category_includes_configuration_and_
  custom_when_set` in `test_integrations_terraform_projection.py`;
  `test_deploy_run_resolves_provider_configuration_tokens_via_tf_var` in
  `test_deploy_controller.py`, end-to-end with a real `${var:}`+`${secret:}`
  token, confirming the on-disk `providers.auto.tfvars.json` stays
  literal/unresolved while `TF_VAR_providers` carries the resolved value);
  4 existing tests updated for the wider category set (`workspace`/
  `providers` are unconditionally present in every fixture). Full check
  suite green: mypy (107 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1258 passed).
- **Docs**: logged as `docs/_gap_v1.md` gap #17 (resolved); `.v2-haven`'s
  real `hetzner-eu-de.yaml` provider document cited directly as the
  concrete evidence, not just a hypothetical.

Original design (for reference — see deviation note above):

- Replace `build_dns_networks_firewalls_payloads()` +
  `build_configuration_payloads()`'s curated-8-category pairing
  (`terraform_projection.py`) with one function iterating **all** of
  `build_platform_projection()`'s 13 categories generically. Keep the
  existing per-category `TF_VAR_<name>` delivery shape and the
  `resources_by_category` → `resx_<type>` per-resource-type split
  (a real Terraform `variable "resx_compute" {}`-per-type constraint,
  unrelated to this fix) — only the *selection* of which categories
  participate changes, from a hand-picked subset to all of them.
- Keep gap #12's per-name step-ownership/claiming rule exactly as-is for
  `dns`/`networks`/`firewalls` (the only three categories with per-name
  addressability) — the other ten remain broadcast-only, `${output:...}`
  still rejected outright for them (unchanged from today's Phase 6 rule).
- `deploy_controller.py`: update the delivery loop to call the new unified
  function instead of the two it calls today.
- **Tests**: a new test proving `providers` now resolves and delivers
  (the one confirmed real gap — mirror an existing `properties`/`tenant`
  Phase 6 test's shape); a regression test proving `flags`/`variables`/
  `workspace`/`namespaces` genericizing is a true no-op (still contribute
  nothing to deliver, since none carry a token-bearing field); every
  existing dns/networks/firewalls/properties/custom/tenant/topologies/
  resx_`<type>` test must keep passing unchanged.
- **Docs**: log this as a new numbered gap in `docs/_gap_v1.md` once
  implemented (per the design's own earlier note — flagged, not logged,
  until now); update the Per-Kind Status table's `Provider` row.

### Phase 2 — `${value:...}` syntax recognition — ~~IMPLEMENTED (2026-09-29)~~

Implemented exactly as designed, no deviations. `value_tokens.py`: added
`"value"` to `VALUE_TOKEN_KINDS` and both the `VALUE_TOKEN_PATTERN`/
`_RESOLVE_VALUE_TOKEN_PATTERN` kind alternations — no regex redesign, the
`key` group's existing `[A-Za-z0-9_.-]+` already permitted the extra dots.
Module and `resolve_value_tokens()` docstrings updated to describe the 5th
kind's kind-agnostic split (this module recognizes syntax only; resolving
`kind.name.path` against a real `DocumentIndex` is Phase 3+'s job).

One real, expected interim consequence confirmed and tested (not a design
change): `environment_service.py`'s `unresolved_value_tokens()` has no
`kind == "value"` skip branch yet (that's Phase 5), so any document
containing a `${value:...}` token today is honestly flagged with
`unsupported_value_token_kind` at `strata validate` time — the exact same
interim shape `${output:}` had before gap #9/#12 wired it in. Confirmed
via a new test (`test_value_token_is_rejected_cleanly_pending_phase_5`)
rather than left undocumented. Similarly, `resolve_value_tokens()` (the
deploy-time resolver) raises the same "did not resolve to a value" error
an undeclared `var`/`secret` key would if it ever reaches a `${value:...}`
token, since nothing populates a `"kind.name.path"` entry in its flat
`values` dict yet — also confirmed via a new test.

- **Tests**: 5 in `test_utils_value_tokens.py` (`extract_value_tokens()`/
  `find_malformed_value_tokens()`/`validate_value_tokens()` recognize
  `${value:kind.name.path}` syntactically; a bare `${value:onlyonesegment}`
  is confirmed syntactically well-formed at this phase, not a malformed-
  token error — the `key` group doesn't parse `kind.name.path` internally,
  only Phase 3's resolver will; `resolve_value_tokens()` raises the
  expected interim error) + 1 in `test_services_environment.py` (the
  `unsupported_value_token_kind` interim behavior above). Full check
  suite green: mypy (107 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1264 passed). `.v2-haven`/`.v2-cfg` unaffected — grep confirmed
  zero real documents contain `${value:...}` text today.

Original design (for reference — implemented with zero deviation):

- `value_tokens.py`: add `"value"` to `VALUE_TOKEN_KINDS` and the
  `VALUE_TOKEN_PATTERN`/`_RESOLVE_VALUE_TOKEN_PATTERN` kind alternation.
  No regex redesign — the `key` group's existing `[A-Za-z0-9_.-]+` already
  permits the extra dots.
- **Tests**: `extract_value_tokens()`/`find_malformed_value_tokens()`
  recognize `${value:kind.name.path}` syntactically (Phase 1 shape-only —
  no existence/resolution yet). A bare `${value:onlyonesegment}` (missing
  `name`/`path`) is syntactically well-formed at this phase (the regex
  doesn't parse `kind.name.path` internally, only Phase 3's resolver does)
  — confirm this is surfaced later as `value_reference_invalid_path`, not
  a Phase 1 malformed-token error, and document that split explicitly so
  it isn't mistaken for an oversight.

### Phase 3 — Resolution primitive — ~~IMPLEMENTED (2026-09-29)~~

Implemented per the design below, in `value_controller.py` (Open Question
1 resolved: kept it there, not a new module — the walk/path-traversal
logic stayed small enough that a sibling module would have been pure
ceremony). `resolve_document_value_references(index: DocumentIndex) ->
tuple[dict[str, str], Diagnostics]` walks every document in the index for
`${value:...}` candidates and, per the "Resolution model" section above,
checks kind → name/existence → path → scalar-type → target-is-literal, in
that order.

**One real refinement found during implementation, against this design's
own looser Phase 2 wording**: Phase 2's note said a bare
`${value:onlyonesegment}` would surface "later as
`value_reference_invalid_path`". Once the actual check order was
implemented (kind validity checked *first*, before segment count, since
it's the cheaper and more fundamental question), the honest answer turned
out to be different: `"onlyonesegment"` genuinely isn't a real
`PlatformKind` value, so it is `value_reference_unknown_kind`, not
`value_reference_invalid_path` — a `${value:...}` token with too few
segments only produces `value_reference_invalid_path` when its first
segment *is* a valid kind but nothing follows it (e.g. a bare
`${value:tenant}`). Corrected in the function's own docstring and tested
as two separate, distinct cases rather than left to silently disagree
with the design.

Two other resolution-model claims were also directly exercised, not just
asserted: an *empty* path (`${value:tenant.c0062}`, kind+name with no path
at all) needs no dedicated handling — walking zero path segments returns
the whole document dict, which then fails the scalar check exactly like
any other whole-sub-object reference, confirming "referencing a whole
sub-object is out of scope" falls out for free. And the
"cycles/chains are impossible by construction" claim was tested for a
same-kind chain specifically (a target field whose own literal is itself
`${value:another...}`), not just a `${var:}`-mixed one — both are
rejected as `value_reference_target_not_literal` for the identical
reason.

- **Tests**: 14 new, in `test_value_controller.py` — one per documented
  failure mode (`value_reference_unknown_kind` ×2, the bare-single-segment
  and the genuinely-bogus-kind cases; `_invalid_path` ×2, the bare-kind-
  no-name case and a real-but-nonexistent field path;
  `_unknown_document`; `_not_scalar` ×2, a real sub-object path and the
  empty-path case; `_target_not_literal` ×2, `${var:}`- and
  `${value:}`-chained), plus the `kind: solution` edge case (always
  `_unknown_document`, never a crash), plus a happy-path resolution
  matching this doc's own `tenant.c0062.meta.name` example exactly, plus a
  nested-`spec.*`-field variant, a source/location-attribution check, and
  a no-tokens-anywhere clean-pass regression. Full check suite green:
  mypy (107 files), ruff, import-linter (1 kept, 0 broken), pytest (1278
  passed).

Original design (for reference — implemented with the one refinement
noted above):

- New `resolve_document_value_references(index: DocumentIndex) -> tuple[dict[str, str], Diagnostics]`
  (`value_controller.py`, per Open Question 1's leaning — revisit only if
  it grows large enough to justify splitting out). Walks every document in
  the index for `${value:...}` candidates, and per the "Resolution model"
  section above: parses `kind.name.path`, resolves `kind`/`(kind, name)`/
  path/scalar-type/target-is-literal, in that order (cheapest, most
  common failure first), producing one `Diagnostics` entry per violation
  with the five documented error codes.
- **Tests**: one per documented failure mode
  (`value_reference_unknown_kind`, `_unknown_document`, `_invalid_path`,
  `_not_scalar`, `_target_not_literal`), plus the `kind: solution` edge
  case (always `_unknown_document`, never a crash), plus a happy-path
  resolution matching this doc's own `tenant.c0062.meta.name` example
  exactly.

### Phase 4 — Wire into `resolve_values()` — ~~IMPLEMENTED (2026-09-29)~~

Implemented exactly as designed, no deviations. `value_controller.py`'s
`resolve_values()` now calls `resolve_document_value_references(context.
controller.index)` once, unconditionally (not filtered by or dependent on
`deployment_name`/`keys` — merges every `${value:...}` reference found
anywhere in the whole solution, since this token kind is never declared in
an Environment the way `var`/`secret`/`feature` are), merging its
`dict[str, str]` into the returned `ValueResolution.values` and its
`Diagnostics` into `.diagnostics`. `deploy_controller.py`'s own
`resolved.values` consumption (`tokens = {**resolved.values,
**visible_outputs}`, already generic/blind to which mechanism produced an
entry) needed **zero changes** — the new entries simply flow through the
same flat map every existing delivery mechanism (Terraform `TF_VAR_`,
Helm/Compose secret-tracking resolvers) already reads.

- **Tests**: 3 new in `test_value_controller.py` — a `${value:...}` entry
  appearing in `.values` alongside a real declared `keys` entry (proving
  the merge is additive, not a replacement); an unresolvable
  `${value:...}` surfacing in `.diagnostics` without raising, exactly like
  an unresolvable declared key already does; and the design's own
  `tenant.c0062.meta.name` example resolved end-to-end through
  `resolve_values()` itself, not just the Phase 3 primitive directly. Full
  check suite green: mypy (107 files), ruff, import-linter (1 kept, 0
  broken), pytest (1281 passed). `.v2-haven`/`.v2-cfg` unaffected — no
  real document uses `${value:...}` yet.

Original design (for reference — implemented with zero deviation):

- `value_controller.py`'s `resolve_values()`: call Phase 3's function
  once, merge its `dict[str, str]` into the returned `ValueResolution.values`
  and its `Diagnostics` into `ValueResolution.diagnostics`.
- **Tests**: `resolve_values()`'s own test file gains a case proving a
  `${value:...}` entry appears in the returned `.values`, and that an
  unresolvable one surfaces in `.diagnostics` without raising.

### Phase 5 — Validate-time check — ~~IMPLEMENTED (2026-09-29)~~

Implemented per the design below, plus one real, unplanned refactor found
during implementation: wiring `_check_value_references()` (`semantic_checks.py`)
to call Phase 3's `resolve_document_value_references()` — which lived in
`value_controller.py` — created a genuine circular import.
`solution_context.py` imports `semantic_checks.py` (`run_semantic_checks()`);
`value_controller.py` imports `solution_context.py` (for the
`SolutionContext` type its *other* functions need); so
`semantic_checks.py` importing straight from `value_controller.py` closed
the loop: `solution_context -> semantic_checks -> value_controller ->
solution_context`. None of mypy/ruff/`get_errors` caught this (imports are
only resolved at runtime) — only actually running `pytest` surfaced
`ImportError: cannot import name '...' from partially initialized module`.

**Fix**: extracted `resolve_document_value_references()` (and its two
private helpers, `_iter_document_strings()`/`_walk_value_reference_path()`,
and the `_VALUE_PATH_MISSING` sentinel) out of `value_controller.py` into
a new sibling module, `value_references.py` — it only ever needs a
`DocumentIndex`, never a `SolutionContext`, so it has no dependency on
`solution_context.py` at all, and both `value_controller.py` and
`semantic_checks.py` now import it directly with no cycle. This also
resolves Open Question 1 with a concrete, evidence-based answer, not just
a style preference: the function belongs in its own module after all —
not because it "grew large enough" (it didn't), but because two
same-layer callers needing it, one of which already depends on the
other's would-be home module, made a shared home mandatory.

- `environment_service.py`'s `unresolved_value_tokens()`: added the
  `kind == "value"` skip branch, exactly as designed — it no longer
  reports `unsupported_value_token_kind` for this kind at all (a real,
  necessary behavior change from Phase 2's own interim state, confirmed
  via an updated test replacing the one that had asserted the old interim
  rejection).
- `semantic_checks.py`: new `_check_value_references(index)`, wired into
  `run_semantic_checks()` as the 9th check, exactly as designed — reuses
  `resolve_document_value_references()` directly (now from
  `value_references.py`), discarding its resolved-values output and
  keeping only the `Diagnostics`.
- **Tests**: 4 new end-to-end `strata validate` tests in
  `test_semantic_checks.py` (a resolving reference passes; an unresolvable
  one is caught with the right `value_reference_unknown_document` code;
  neither ever produces `unsupported_value_token_kind` anymore, the exact
  interim behavior Phase 2 had; the base "everything passes" control case
  is unaffected). 1 existing test in `test_services_environment.py`
  rewritten (not just patched) to assert the new, correct behavior —
  `${value:...}` now passes `unresolved_value_tokens()` unconditionally,
  including one that would fail the real `_check_value_references()`,
  since this function genuinely has no opinion on it either way anymore.
  Full check suite green: mypy (108 files — the new module), ruff,
  import-linter (1 kept, 0 broken), pytest (1285 passed).

Original design (for reference — implemented with the one real deviation
noted above, a module split neither the design nor Phase 3 anticipated):

- `environment_service.py`'s `unresolved_value_tokens()`: add the
  `kind == "value"` skip branch (do not run the Environment-declared-keys
  path for it at all).
- `semantic_checks.py`: new `_check_value_references(index)` — reuses
  Phase 3's `resolve_document_value_references()` directly so `strata
  validate` and real deploy-time resolution share one implementation.
  Wire into `run_semantic_checks()` as the 9th check.
- **Tests**: end-to-end `strata validate` tests (`test_semantic_checks.py`
  shape) — a valid cross-document reference passes; each of the five
  failure modes is caught at validate time, before any deploy.

### Phase 6 — Documentation and verification — ~~IMPLEMENTED (2026-09-29)~~

Per request ("design, plan, and implement phase 6, and do a full review of
the implementation"). Did the documentation/re-validation work below, plus
a genuine line-by-line review of Phases 0-5's real code (not just a
re-read of this doc), which found and fixed real, previously-unnoticed
staleness:

- **Stale docstrings fixed** (found during the review, not part of the
  original Phase 6 bullet list): `value_tokens.py`'s module docstring and
  `resolve_value_tokens()`'s own docstring both still said resolving
  `${value:...}` was "Phase 3+"/"not yet implemented" — stale since Phase
  3/4 actually landed. Corrected to describe the real, current behavior
  (a `${value:...}` token resolves exactly like any other kind once
  `resolve_values()` has merged its entry — Phase 4 — into the flat
  `values` map `resolve_value_tokens()` reads). Also corrected the module
  docstring's reference to "`value_controller.py`'s job" — the resolver
  moved to `value_references.py` during Phase 5's circular-import fix, and
  this comment had not been updated to match. One test's own docstring had
  the same staleness (still describing the pre-Phase-3/4 world) — fixed
  and paired with a new positive-case test proving the low-level
  `resolve_value_tokens()` correctly substitutes a `${value:...}` token
  once its key is populated (the negative "raises when key missing" case
  already existed; the positive case had never actually been exercised
  directly on this function, only indirectly via `resolve_values()`'s own
  tests).
- **A real, confirmed limitation found and documented, not silently
  worked around**: a `${value:...}` (or any other kind's) token embedded
  inside a `store: constant` variable/secret/feature's own `value` field
  is never resolved — confirmed directly in `value_controller.py`:
  neither `_resolve_store_value()` nor `build_value_references()`'s
  `_value_for()` ever calls `resolve_value_tokens()` on a constant
  store's raw value, so it would pass `strata validate` cleanly (the
  generic whole-document walk sees it) but then reach every consumer as
  the literal, unresolved token string. Not a bug in this design or
  specific to `value:` — every kind shares it, and it is pre-existing,
  not introduced here. Found while attempting this doc's own Phase 6
  migration proof below, which is exactly why it surfaced now rather than
  earlier: the original worked example (see "Proposed syntax" above)
  assumed both of `.v2-cfg`'s duplicated `"c0062"` occurrences could be
  replaced uniformly; only one of the two actually can.
- **Live migration proof, per direct request** ("you can update v2.cfg
  tenant for instance to validate"): `.v2-cfg`'s real
  `environments/c0062-env.yaml` had exactly the duplication this design's
  own "Problem" section describes — `spec.properties.environment_info.
  customer_code` and `spec.variables[].value` (a `store: constant`
  variable) both hand-typed as the literal `"c0062"`, duplicating
  `tenants/c0062.yaml`'s own `meta.name`. Migrated the `properties`
  occurrence to `${value:tenant.c0062.meta.name}` — a real, working
  target, since `properties` is one of the ten broadcast-delivered
  categories `docs/_gap_v1.md` gap #17 (Phase 1) already made resolve
  correctly at deploy time. Left the `variables[].value` occurrence as a
  literal, with an explanatory comment, per the limitation found above —
  substituting it would have silently broken, not fixed, that field.
  `strata validate .v2-cfg` confirmed clean, 12/12, both before and after.
- Updated [value-token-resolution.md](value-token-resolution.md)'s
  "Syntax" bullet (previously stale, still said "proposed, design-only")
  and the "Value Supply Mechanisms" overview section (written 2026-09-25,
  predating this design entirely — added a cross-reference rather than
  rewriting its historical text).
- Re-validated `.v2-cfg` (clean, 12/12) and `.v2-haven` (confirmed via
  the local dev build, not the installed release — exactly the same known
  44 `malformed_value_token` errors, zero new `value_reference_*` codes,
  zero regressions) using the actual current source rather than the
  globally-installed `strata` CLI (a stale released version) — ran via
  `python -c "...; from strata.commands.cli import main; main()"`.
- Full check suite green throughout: mypy (108 files), ruff, import-linter
  (1 kept, 0 broken), pytest (1286 passed, 1 new test from the docstring
  review above).

Original Phase 6 bullet list (for reference — all done):

- Update [value-token-resolution.md](value-token-resolution.md)'s syntax
  bullet and Per-Kind Status table framing (already partially done —
  confirm still accurate once Phases 0-5 land).
- Full check suite (mypy/ruff/import-linter/pytest) after every phase, not
  just at the end — matching this whole project's established discipline.
- Re-validate `.v2-cfg`/`.v2-haven` — confirm zero new findings for
  either. Optionally migrate `.v2-cfg`'s own `c0062` duplication (this
  design's original motivating example) to
  `${value:tenant.c0062.meta.name}` as a real, live proof the feature
  works end to end — not required for the design to be "done," but the
  most direct validation available.

## Changelog

- 2026-09-29: Created. Real duplication found while building `.v2-cfg`
  (`tenants/c0062.yaml` vs `environments/c0062-env.yaml`, the literal
  `"c0062"` typed independently in both) prompted the request; design only,
  per explicit request to review before any implementation given the
  breadth of this feature's reach across the schema.
- 2026-09-29: Added the "Dependency: universal resolution reach" section,
  per direct request that the replacement mechanism (not just validation)
  work anywhere in `root.spec.*` for all five kinds
  (`var`/`secret`/`feature`/`output`/`value`). Answering a follow-up
  question about today's actual resolution reach found a real, concrete
  gap motivating this as a named requirement rather than an assumption:
  `ProviderSpecModel.configuration`/`.custom` passes validation cleanly but
  is never projected into any Terraform artifact at all
  (`_build_providers_payload()` only ever emits `type`/`region`/
  `display_name`) — full write-up moved to
  [value-token-resolution.md](value-token-resolution.md) (the doc that
  already owns Terraform delivery), cross-referenced from here rather than
  duplicated, with an explicit sequencing note that this design should land
  after (or alongside) that fix, not before it.
- 2026-09-29: **Reviewed the design**, per direct request ("review the
  design"). Found and fixed one real inconsistency: "Where it's computed"
  gave `resolve_document_value_references()` the signature
  `-> dict[str, str]`, while "Impact map" gave it
  `-> tuple[dict[str, str], Diagnostics]` for the same proposed function —
  reconciled to the tuple form (the one both `resolve_values()`'s merge
  and `_check_value_references()`'s validation need), with `Diagnostics`
  now explicitly merged into `ValueResolution`'s own existing
  `diagnostics` field rather than a second, undescribed error path. Also
  documented a real edge case found while re-checking the existence rule
  against `SolutionController._load_manifest()`'s own docstring:
  `kind: solution` can never resolve via `${value:...}` because the
  solution manifest's own kind is deliberately never indexed (same fact
  gap #3 already had to account for) — not a bug, but worth stating
  explicitly rather than leaving it to be discovered as a surprise.
  Re-verified the "cycles are impossible by construction" claim against
  several concrete cases (self-reference, a target containing a
  *different*-kind token, a partial-token string) — held in every case
  checked. No other issues found; design considered ready.
- 2026-09-29: Added the "Implementation Plan" — 7 phases (0: guardrail
  test, no dependencies; 1: the Provider-gap Terraform-delivery fix this
  design depends on; 2-6: the `${value:...}` kind itself, syntax through
  validate-time wiring), each independently shippable and testable,
  matching this doc family's established phased-delivery convention. Per
  request to create an implementation plan following the design review.
- 2026-09-29: **Implemented Phase 0** (guardrail test), per request
  ("design, plan, and implement phase 0"). Found and fixed two real
  complications during implementation, neither anticipated in the design:
  (1) this exact test file already has a module-level `_Bare` fixture
  subclassing `InfraIntegration` that would have false-positived the guard
  — fixed with a `__module__.startswith("strata.")` filter, excluding any
  test fixture by construction rather than by allowlist; (2) registered
  integrations are lazily imported (`registry.py`'s own documented
  reason), so the walk found zero classes until every `registry._KNOWN`
  entry was force-loaded first. 2 new tests; full check suite green (mypy
  `src` clean, ruff `src tests` clean, import-linter 1/0, pytest 1256
  passed). Pure test addition — zero production code changed.
- 2026-09-29: **Implemented Phase 1** (the Provider-gap Terraform-delivery
  genericization), per request ("design, plan, and implement phase 1").
  Confirmed the real gap directly against source first:
  `_build_providers_payload()` never read `ProviderSpecModel.
  configuration`/`.custom`, live in `.v2-haven`'s real `hetzner-eu-de.yaml`.
  Fixed that function to include both; extended
  `build_configuration_payloads()` from its curated 5-category subset to
  all 10 non-claimable categories. One deliberate deviation from the
  original one-function sketch: kept `build_dns_networks_firewalls_
  payloads()` and `build_configuration_payloads()` as two functions, not
  one, to avoid rewriting an existing test suite's exact-category-set
  assertions — `deploy_controller.py` needed zero logic changes either
  way. One design assumption corrected during implementation: `workspace`/
  `providers` turned out to be *unconditionally present* (not absent) in
  every real solution once genericized — "no-op" means their values don't
  change under resolution, not that they contribute nothing. 3 new tests,
  4 existing tests updated for the wider category set. Full check suite
  green: mypy (107 files), ruff, import-linter (1 kept, 0 broken), pytest
  (1258 passed). Logged as `docs/_gap_v1.md` gap #17 (resolved).
- 2026-09-29: **Implemented Phase 2** (`${value:...}` syntax recognition),
  per request ("design, plan, and implement phase 2"). Implemented exactly
  as designed, zero deviations: added `"value"` to `VALUE_TOKEN_KINDS` and
  both regex kind alternations in `value_tokens.py`. Confirmed and tested
  one real, expected interim consequence rather than leaving it
  undocumented: `unresolved_value_tokens()` has no `kind == "value"` skip
  branch yet (Phase 5's job), so a `${value:...}` token in a real document
  is honestly flagged `unsupported_value_token_kind` today — the same
  interim shape `${output:}` had before its own later phases wired it in.
  6 new tests (5 syntax-recognition in `test_utils_value_tokens.py`, 1
  interim-rejection in `test_services_environment.py`). Full check suite
  green: mypy (107 files), ruff, import-linter (1 kept, 0 broken), pytest
  (1264 passed). `.v2-haven`/`.v2-cfg` confirmed unaffected (grep: zero
  real documents contain `${value:...}` text today).
- 2026-09-29: **Implemented Phase 3** (the resolution primitive), per
  request ("design, plan, and implement phase 3"). New
  `resolve_document_value_references()` in `value_controller.py` (Open
  Question 1 resolved: kept it there rather than a new module). One real
  refinement found during implementation, corrected against this design's
  own looser Phase 2 wording: a bare `${value:onlyonesegment}` actually
  surfaces as `value_reference_unknown_kind` (the segment genuinely isn't
  a valid `PlatformKind` value), not `value_reference_invalid_path` as
  Phase 2's note loosely suggested — `_invalid_path` is reserved for a
  *valid* kind with no name segment (e.g. a bare `${value:tenant}`). Also
  directly exercised, not just asserted: the empty-path case
  (`${value:tenant.c0062}`) needs no dedicated handling, since it falls
  through to the same scalar check any whole-sub-object reference hits;
  and the "cycles impossible by construction" claim holds for a same-kind
  chain (`${value:}` targeting another unresolved `${value:}`), not just a
  mixed-kind one. 14 new tests in `test_value_controller.py`. Full check
  suite green: mypy (107 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1278 passed). Phases 4-6 (wiring into `resolve_values()` and
  `strata validate`, documentation) remain unimplemented.
- 2026-09-29: **Implemented Phase 4** (wiring into `resolve_values()`),
  per request ("design, plan, and implement phase 4"). Implemented exactly
  as designed, zero deviations: `resolve_values()` now calls
  `resolve_document_value_references(context.controller.index)`
  unconditionally, merging into `ValueResolution.values`/`.diagnostics`.
  Confirmed `deploy_controller.py` needed zero changes — its own
  `resolved.values` consumption was already generic. 3 new tests. Full
  check suite green: mypy (107 files), ruff, import-linter (1 kept, 0
  broken), pytest (1281 passed). Phases 5-6 (the `strata validate` check,
  documentation) remain unimplemented.
- 2026-09-29: **Implemented Phase 5** (the `strata validate` check), per
  request ("design, plan, and implement phase 5"). Found and fixed a real
  circular import not anticipated by the design or Phase 3/4: wiring
  `semantic_checks.py`'s new `_check_value_references()` to
  `resolve_document_value_references()` (then living in
  `value_controller.py`, per Phase 3's own choice on Open Question 1)
  created `solution_context -> semantic_checks -> value_controller ->
  solution_context` (`value_controller.py` needs `SolutionContext` for its
  *other* functions; `solution_context.py` needs `semantic_checks.py` for
  `run_semantic_checks()`). Neither mypy nor ruff nor `get_errors` caught
  it — only running `pytest` surfaced the real `ImportError`. Fixed by
  extracting the function (plus its two private helpers and sentinel)
  into a new sibling module, `value_references.py`, which only needs
  `DocumentIndex` and has no dependency on `solution_context.py` at all —
  both `value_controller.py` and `semantic_checks.py` now import it
  directly. This retroactively answers Open Question 1 with real evidence
  rather than a style preference: the function needed its own module not
  because it grew large, but because two same-layer callers needed it
  and one of them already depended on the other's original home.
  `environment_service.py`'s `unresolved_value_tokens()` gained the
  designed `kind == "value"` skip branch. 4 new end-to-end tests in
  `test_semantic_checks.py`; 1 existing test in
  `test_services_environment.py` rewritten (not patched) to assert the
  new, correct behavior instead of the old Phase-2-interim one. Full check
  suite green: mypy (108 files, the new module), ruff, import-linter
  (1 kept, 0 broken), pytest (1285 passed). `${value:...}` now resolves
  at deploy time AND is checked at validate time — only Phase 6
  (documentation/final verification) remains.
- 2026-09-29: **Implemented Phase 6 and did a full implementation
  review**, per request ("design, plan, and implement phase 6 and do a
  full review of the implementation... you can update v2.cfg tenant for
  instance to validate"). The review (a real line-by-line pass over
  Phases 0-5's code, not a re-read of this doc) found two things worth
  recording: (1) two stale docstrings in `value_tokens.py` (module-level
  and `resolve_value_tokens()`'s own) still described `${value:...}` as
  unresolved/"Phase 3+", stale since Phase 3/4 landed — fixed, plus a new
  positive-case test proving direct substitution once a key is populated;
  (2) a real, confirmed, pre-existing limitation (not specific to
  `value:` — every kind shares it): a Value token embedded in a
  `store: constant` variable/secret/feature's own `value` is never
  resolved by `_resolve_store_value()`/`build_value_references()`. This
  surfaced directly from attempting the live migration proof: `.v2-cfg`'s
  real `environments/c0062-env.yaml` duplicates `"c0062"` in *two* places
  (`spec.properties.environment_info.customer_code` and
  `spec.variables[].value`) — only the first could be safely migrated to
  `${value:tenant.c0062.meta.name}`; the second was left a literal, with
  an explanatory comment, rather than silently broken by substituting it.
  `strata validate .v2-cfg` (run via the local dev source, not the stale
  globally-installed release) confirmed clean, 12/12, both before and
  after. `.v2-haven` re-confirmed unaffected: same known 44
  `malformed_value_token` errors, zero new `value_reference_*` codes.
  Also updated `value-token-resolution.md`'s "Syntax" bullet (still said
  "proposed, design-only") and its 2026-09-25 "Value Supply Mechanisms"
  overview (added a cross-reference rather than rewriting historical
  text), and marked both of this doc's own resolved Open Questions (1 and
  3) accordingly. Full check suite green: mypy (108 files), ruff,
  import-linter (1 kept, 0 broken), pytest (1286 passed). **All 7 phases
  of this design are now implemented.**
