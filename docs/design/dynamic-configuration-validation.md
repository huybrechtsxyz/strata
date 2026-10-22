# Dynamic Configuration Validation (`attributeconfig`) — Design

- Status: current
- Last updated: 2026-10-22 (variables/features)

## Overview

Strata has roughly eighteen models carrying a loosely-typed passthrough
blob — `spec.properties`, `spec.configuration`, `spec.custom`, each typed
`dict[str, Any] | None`. These exist on purpose (`resource` deliberately
never abstracted the real cloud resource type, ADR-0004, and the same
restraint is why `provider`/`module`/`namespace`/`firewall`/`environment`
all carry one too) — but Pydantic never recurses into an `Any`-typed dict,
so everything inside one is unvalidated: a typo, a missing required key, or
a malformed value reaches the provisioner untouched. The motivating real
case (a Terraform workload-identity component) states the cost directly in
its own `variables.tf` comment: *"A malformed subject is accepted by Entra
ID and by Terraform; it only surfaces as pods failing to obtain a token at
runtime, with nothing in the plan or the apply log to point at. Rejecting
it here turns a production runtime failure into a plan error."*

`attributeconfig` is a standalone kind that declares a real JSON Schema
(2020-12) fragment and enforces it, automatically, against every matching
document's targeted `properties`/`custom`/`configuration` block — the
moment the schema ships, with no action required from whoever authored the
target document. It generalizes, and replaces, a narrower mechanism that
used to live embedded inside `providerconfig` (see "Relationship to
`providerconfig`" below).

## Current Design

### Document shape

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: attributeconfig
meta:
  name: <name>
spec:
  target:
    kind: <PlatformKind>              # e.g. environment, resource
    block: properties | custom | configuration | variables | features  # whichever block this kind actually has
    view: document | effective        # optional, default document — see "Evaluation semantics"
    selector:                         # optional; omitted = every document of this kind
      <field.path>: <required-value>  # equality only, no wildcards/regex
  schema:                             # verbatim JSON Schema (2020-12), passed through near-raw
    type: object
    properties: { ... }
```

### Scope

Covers `properties`/`custom` on every kind, `configuration` specifically where that's the blob's real field
name (`resource.spec.configuration`), and `variables`/`features` on `environment` — both are
`list[VariableStoreModel | FeatureStoreModel] | None`, not a `dict[str, Any]` blob like the other three,
so they are reshaped into the same `{key: value}` shape before evaluation (`reshape_block_value()`,
"Evaluation semantics" below). `secrets` is excluded **permanently, as a security boundary, not merely
unaddressed scope**: a failing schema's `ValidationError.message` can echo the offending value back in
`strata validate`'s plain-text output, and a `store: constant` secret's `value` *is* the literal secret —
validating it the same way would mean a secret's real value can end up in a CI log the moment an author's
pattern typo makes it fail. `secrets` is simply never a member of `target.block`'s `Literal`, so this is
enforced structurally, not by a runtime check that could be bypassed or forgotten.

`(kind, block)` pairs are validated at load, not merely accepted because
`block` is a member of a shared `Literal` — `AttributeConfigTargetModel`'s
`BLOCKS_BY_KIND` table (`attributeconfig_model.py`) pins exactly which
block(s) each kind's own top-level `*SpecModel` actually declares, checked
against those classes directly by a dedicated test so it cannot silently
drift. A pair naming a field the kind doesn't have (e.g. `resource` +
`properties` — `resource` only has `custom`/`configuration`) is rejected at
load (`block 'properties' does not exist on kind 'resource' (has:
configuration, custom)`) — before this check existed, such a pair passed
load-time validation and then silently evaluated every document of that
kind against `{}` forever, with no diagnostic ever pointing at the actual
mistake (the `attributeconfig` document's own `target.block`). This table only governs `view:
document` — `view: effective` always targets `kind: deployment` regardless of which block, including
`variables`/`features`, which `DeploymentSpecModel` has no field for at all (only `EnvironmentSpecModel`
does); its own validity rule (below) is independent of this table.

### Schema language

Real JSON Schema draft 2020-12, evaluated by the already-shipped
`jsonschema` library (now an explicit direct dependency, alongside
`referencing` — previously only transitive via
`cyclonedx-python-lib[json-validation]`). Authors write genuine JSON
Schema (`type`, `properties`, `patternProperties`, `required`,
`additionalProperties`, `if`/`then`, `dependentRequired`, ...) directly —
no strata-invented vocabulary.

**Hard security requirement.** The evaluator (`attributeconfig_evaluator.
py`'s `build_validator()`) always constructs
`Draft202012Validator(schema, registry=referencing.Registry())` — an empty,
non-retrieving registry — and never calls the bare `jsonschema.validate()`
convenience function. `spec.schema` is untrusted, author-supplied content;
a validator that resolves an unrecognized `$ref` by fetching it would let
that content make strata itself issue an outbound HTTP request to an
attacker-chosen URL while validating a document. Confirmed directly
against the real library (4.26.0): an external `$ref` passes
`check_schema()` uninspected (ref resolution is lazy, not performed at
compile time) but raises `referencing.exceptions.Unresolvable` the moment a
document is actually evaluated against it — reported as an invalid schema
(`attributeconfig_invalid`), never fetched.

Load-time schema validity is one call, `Draft202012Validator.
check_schema()`, uniform for every keyword in the fragment.

### Binding / selection

`target.kind` + `target.block` + optional `target.selector` (a
field-path-to-required-value equality dict; omitted = matches every
document of that kind). No name-based registration anywhere — the check
scans the document index directly (`_check_attributeconfigs()` in
`semantic_checks.py`) by what each schema targets, the same pattern
`providerconfig` already used.

**Push, not pull.** The alternative binding model — the target document
explicitly lists which schema(s) apply to it, defaulting to unrestricted —
was rejected: it breaks the automatic, un-opt-outable enforcement that
makes this feature worth building. Every pre-existing document (45+ real
`properties` documents across this repo's consumer fixtures) would stay
completely unchecked until someone remembered to opt in.

### Evaluation semantics

- **`target.view` (default `document`).** `document` evaluates every
  matching document's own `target.block` fragment in isolation, as above.
  `effective` — only valid for `target.kind: deployment` (rejected at load for anything else, since no merge
  exists for `configuration` or for any other kind) — instead evaluates the fully merged blob of every
  *deployable* (non-`partial`) deployment the selector matches. The merge source differs by block:
  `properties`/`custom` merge `workspace -> environments -> deployment`, via the same
  `merge_workspace_environment_deployment_properties()` build/deploy already use to produce what Terraform
  receives (`environment_service.py`); `variables`/`features` merge only the deployment's reachable
  environments, via `merge_environment_models()` (the same primitive `_check_deployment_value_tokens()`
  already uses) — neither has a workspace- or deployment-level field to merge in, since only
  `EnvironmentSpecModel` declares them. Either way, the result is reshaped the same way `document` view's
  `variables`/`features` are (`reshape_block_value()`) before the schema runs. This exists because a
  per-document schema misfires on a layered environment: a value left blank at one leaf because a layer
  above it (a tenant default, ADR-0024; a workspace default; another environment in the same merge) fills
  it in reads as genuinely missing under `document`, even though the deployment that actually resolves is
  perfectly valid — confirmed against a real recorded defect (a blank Terraform variable that silently
  disabled private endpoints while its sibling variable was set). A `partial: true` deployment is skipped
  outright — it is not deployable and may lack required fields by design, the same reasoning
  `_check_deployment_layers()` already applies elsewhere. An `effective`-
  view finding's message and location name the deployment whose merged
  blob failed (`merged blob of deployment '<name>'`), so it is never
  confused with a `document`-view finding on the same deployment document.
  Which layer actually set the offending value is not tracked (`deep_merge`
  keeps no provenance) — a known gap, not a bug.
- **No merge across schemas.** Every schema whose target and selector match a document is
  evaluated independently; all must pass. Monotonic — adding a schema can
  only ever tighten validation, never loosen it. An unset block is checked
  as `{}`, not skipped, so `required` still fires for a document that
  omits the block entirely. (This is a separate axis from `target.view`
  above, which picks *what data* a single schema checks — no-merge-across-
  schemas is about *how many* schemas apply to that data, and always
  applies regardless of view.)
- **Duplicate-attribute check** (load time, `_check_attributeconfig_
  conflicts()`): fires when two schemas target the same `(kind, block,
  view)`, declare the same top-level `properties` key, and their selectors
  are *compatible* — for every field path present in both selectors, the
  required values agree (`selectors_compatible()`; a selector-less
  selector is vacantly compatible with anything). `view` is part of the
  group key: a `document`-view and an `effective`-view schema on the same
  block check different data (one document's own fragment vs. a
  deployment's merged blob) and can never conflict. Diagnostic code
  `attributeconfig_conflict`, `.error()` severity.
- **Applied-schema visibility**: every `(document, schema)` application is
  logged via `log.info()` under `--verbose` (same channel as
  `version_pins.py`/`sbom_controller.py`); a schema matching zero
  documents (or, for `effective`, zero deployable deployments) gets
  `diagnostics.warning()`, code `attributeconfig_unused` —
  a silently-inert guardrail is otherwise indistinguishable from one that
  always passes.

### Diagnostics

`ValidationError.validator` maps to a stable code
(`attributeconfig_evaluator.translate_validation_error()`):

| `validator` keyword    | Code                                |
| ---------------------- | ----------------------------------- |
| `pattern`              | `attributeconfig_pattern_mismatch`  |
| `required`             | `attributeconfig_field_missing`     |
| `additionalProperties` | `attributeconfig_field_not_allowed` |
| `type`                 | `attributeconfig_type_mismatch`     |
| anything else          | `attributeconfig_validation_error`  |

Plus `attributeconfig_invalid` (schema fails to compile, or references an
unresolvable external resource), `attributeconfig_unused` (warning — a
schema's selector matched nothing), and `attributeconfig_conflict` (two
schemas claim the same attribute with overlapping selectors). Location
comes from `ValidationError.absolute_path`, dot-joined with `.` — never
`.json_path`, which quotes non-identifier property names in a way
`fieldLocation.ts` cannot parse back to a YAML range. Schema-level
findings (`attributeconfig_invalid`, `attributeconfig_unused`) attribute
to the `attributeconfig` document itself; per-document validation
failures attribute to the subject document being checked.

### Relationship to `providerconfig`

`providerconfig` previously carried its own embedded, regex-only schema
sub-feature for `resource.spec.configuration` — retired outright (ADR-0041)
in favor of ordinary `attributeconfig` documents keyed by
`target.selector: {spec.provider_type, spec.resource_type}`, with strictly
more power (real types, nesting, cross-field `if`/`then`) and nothing
lost. `providerconfig`'s resource-type/region name registry
(`ProviderConfigResourceModel.name`, `additional_regions`/
`additional_resources`) is a different, non-overlapping concern — an
enum/allow-list check on a *structured* field, never in `attributeconfig`'s
own blob-shaped scope — and stays permanently.

### Where it lives (layering)

The shared evaluator (`attributeconfig_evaluator.py`) lives in
`strata.services`, never `strata.controllers`, per the enforced
import-linter layering contract (`services` sits below `controllers`; a
lower layer can never import a higher one). `semantic_checks.py`
orchestrates (scans the index, calls the evaluator, assembles
diagnostics); it does not implement checks itself. Selector evaluation
(`evaluate_selector()`) mirrors `controllers.references._walk()`'s
traversal convention (segment-split + `getattr`, `[]`-suffix iterating a
list) without reusing it directly — `_walk()`'s own contract (multi-yield,
string-only leaves) doesn't fit a single scalar-equality check, and the
layering contract blocks direct reuse regardless (`references.py` lives in
`controllers/`, a layer the evaluator could never import).

### Known limits

Never expressible by this mechanism, standard or native schema language:

1. **Key-collision invariants** (e.g. two map keys that collide after a
   character rewrite) — need a transform-then-compare step no schema
   language expresses.
2. **Cross-document invariants** (one document's field must equal
   another document's field) — no schema targeting one document can
   constrain another.
3. **Cross-block invariants** (a `features` key gating a `variables` key, e.g. a feature flag that must be
   on for a variable to be required) — `target.block` is one block per schema; `variables`/`features`/
   `properties` are never composed into one object a schema is evaluated against. Same-block cross-key
   rules (two `variables` keys, or two `properties` keys) *are* expressible, via `if`/`then`, same as any
   other block.
4. **"These two keys are equal"** (an alias/copy-of invariant) — JSON Schema has no cross-property
   equality keyword; same limitation as #2, one level down.

Cross-field invariants *within* a single document's targeted block are
expressible via JSON Schema's `if`/`then`/`dependentRequired` — confirmed
against real consumer data, not a theoretical limitation.

A derived existence/typo check against a Terraform root's `variables.tf`
(the same shape as v1's real `terraform_input_validator.py`) is a
separate, complementary feature — it only ever answers "does this key
exist," never "does this key's value have the right shape." Not part of
this design.

Out of scope, no consumer asking yet: surfacing the applied-schema set in
`validate --output json`'s `data` payload for tooling (e.g. "this block is
guarded by schema X").

## Related Decisions

- [ADR-0040](../decisions/0040-attributeconfig-standalone-kind.md) — the
  standalone kind, push binding, JSON Schema choice, and the non-retrieving
  registry requirement.
- [ADR-0041](../decisions/0041-retire-providerconfig-embedded-schema.md) —
  retiring `providerconfig`'s embedded schema sub-feature in favor of this.
- [ADR-0004](../decisions/0004-resource-model-design-decisions.md) — why
  `resource` keeps a loosely-typed passthrough instead of modeling cloud
  resource types (the reason this problem exists at all).
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) /
  [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md)
  — the standalone-kind-over-embedded-list precedent this follows.

## History

- Originated from an archived `access-management.md` proposal (four new
  access-management kinds); checking it against real consumers found the
  opposite need — access management already works today as an ordinary
  `environment.spec.properties` blob, it just isn't validated. Real,
  repeated, shape-only demand was catalogued across two consumer
  fixtures (`aks_config`, `subnets`, `vnet_peers`, `appgateway_config`,
  `kv_platform_config`) beyond the original motivating example, which
  turned out to be unpopulated everywhere in both fixtures checked.
- Resolved generic-vs-bespoke in favor of generic, as a deliberate,
  narrow exception to `policy-engine-architecture.md`'s
  bespoke-indefinitely resolution: a shape check needs nothing but the
  document already in the index, unlike policy evaluation, which needs a
  heterogeneous external data source (SBOM/CVE/Checkov/Terraform
  plan/LLM) for nearly every real check. Every invariant the evidence
  gathering surfaced that this design still can't express
  (key-collision, cross-field-across-documents) is semantic, not shape —
  exactly the kind that stays bespoke either way.
- Rejected a strata-native schema language (an earlier sketch used
  `children`/`entries`/`key_pattern`/`type`) in favor of real JSON Schema
  2020-12 via the already-shipped `jsonschema` library — no new
  dependency, strictly more power (real booleans, arbitrary-key maps via
  `patternProperties`, nesting at every level). Going standard surfaced a
  real risk the native sketch never had: the library's default registry
  resolves an unrecognized `$ref` via a live network fetch rather than
  refusing, confirmed directly in `jsonschema`'s own source — mitigated
  by always constructing an empty, non-retrieving `referencing.Registry()`
  and never calling the bare `jsonschema.validate()` helper.
- Considered deriving schemas from a Terraform root's `variables.tf`
  (v1 has real, shipped logic along these lines). Verified directly
  against v1's actual source: it only ever derives variable existence and
  required-vs-optional, never a variable's value-shape/pattern content —
  exactly the information this design does *not* need help with. Recorded
  as a separate, worth-building complement, not pursued as a derivation
  source for `attributeconfig` schemas.
- Rejected a pull (opt-in) binding model — the target document explicitly
  listing which schemas apply to it — in favor of push (the schema scans
  the index for its own targets): a pull model would leave every
  pre-existing document unchecked until someone remembered to opt in,
  defeating the point of an automatic guardrail.
- Rejected one standalone kind per target kind (`environmentconfig`,
  `moduleconfig`, ... mirroring `providerconfig`/`topologyconfig`'s
  per-subject naming) in favor of one kind with a `target.kind` field —
  every such kind would do the identical job, differing only in which
  kind it targets, at a fraction of the model/service/scaffolded-schema/
  doc surface and with no per-future-kind companion tax.
- Rejected merging multiple matching schemas into one effective schema
  (the way `environment` merges) in favor of no-merge, conjunctive
  evaluation: `environment`'s own merge precedent is shallow/key-level/
  last-wins, and applied to schemas would let one schema silently weaken
  another's constraint with no way to notice — conjunctive evaluation is
  monotonic (adding a schema can only tighten, never loosen). This made a
  load-time duplicate-attribute check necessary (`attributeconfig_
  conflict`) for two schemas claiming the same top-level attribute with
  overlapping selectors.
- No-merge means selector correctness becomes the schema author's
  responsibility, which is only safe if visible: every `(document,
  schema)` application traces via `log.info()` under `--verbose`, and a
  schema matching zero documents warns rather than passing silently.
- Retired `providerconfig`'s embedded `resource.spec.configuration` schema
  sub-feature (`ProviderConfigSchemaField`, `.configuration`,
  `.additional_configurations`, and four diagnostic codes) once backward
  compatibility was confirmed not to matter (strata-v2 pre-release,
  `2.0.0a23`) — see ADR-0041. Its resource-type/region name-registry role
  is a different, non-overlapping concern and was kept.
- Verified, rather than assumed, two implementation details against the
  real installed libraries before relying on them: an external `$ref`
  raises `referencing.exceptions.Unresolvable` at evaluation time (not
  `Unretrievable`, and not at `check_schema()`'s compile time — ref
  resolution is lazy); and the VS Code extension's `fieldLocation.ts`
  already resolves a dot-joined `ValidationError.absolute_path`-shaped
  location correctly with zero code changes, confirmed by invoking the
  compiled module directly against a realistic diagnostic location, not
  just by reading its source.
- Added `target.view: document | effective` (2026-10-22,
  docs/work/environment-value-metadata.md's 5-phase plan, now folded in
  here) — a `document`-view `required` schema misfires on a layered
  environment, since `properties`/`custom` deep-merge `workspace ->
  environments -> deployment` (and a tenant's own base layer, ADR-0024)
  and a per-document schema only ever sees one fragment of that chain. A
  real cfg-int defect motivated it: a blank `vnet_private_endpoints_subnet_name`
  silently disabled private endpoints because nothing checked that a
  required value was actually present once every layer was applied. Scoped
  deliberately narrow — only `target.kind: deployment` +
  `target.block: properties`/`custom`, since that is the one `(kind,
  block)` pair with an existing, build/deploy-shared merge function
  (`merge_workspace_environment_deployment_properties()`) to evaluate
  against; extending to `variables`/`features` (a different, wider
  problem — see the work doc) was deliberately left for later, not
  bundled in. Required moving that merge function from `strata.
  controllers.value_controller` to `strata.services.environment_service`
  first, so `semantic_checks.py` could reach it without recreating the
  controller-layer import cycle `value_references.py` already documents.
  Also added, as a prerequisite found while building this: a load-time
  check that a schema's `(kind, block)` pair actually exists on that
  kind's spec model (see "Scope" above) — before this fix, a typo'd pair
  passed validation silently and evaluated every document of that kind
  against `{}` forever, with the finding blaming the documents rather
  than the actual mistake in the `attributeconfig` document itself.
- Extended to `variables`/`features` (2026-10-22, docs/work/
  environment-value-metadata.md's own 4-phase plan, now folded in here) —
  the deferred half of the previous entry, same-block cross-key rules only
  (the real recorded defect, `vnet_name`/`vnet_private_endpoints_subnet_name`,
  is a same-block pair). `secrets` was deliberately never added to `target.
  block`'s `Literal` at all: a failing schema's message can echo the
  offending value, and a `store: constant` secret's `value` *is* the
  literal secret — a structural exclusion, not unaddressed scope.
  Representation is the whole store entry (`model_dump(mode="json",
  by_alias=True)`), not just `.value` — both are typed `Any` on the real
  models, so a rule may need `store`/`type` alongside `value`. Building
  this found the shipped `(kind, block)` load-time check (previous entry)
  and the `view: effective` validity check actually contradicted each
  other for this specific extension: `DeploymentSpecModel` has no
  `variables`/`features` field at all (only `EnvironmentSpecModel` does),
  so the `(kind, block)` check would have rejected the exact pairing
  `effective` view needs, before the `effective`-view check ever got a
  chance to accept it — fixed by scoping the `(kind, block)` check to
  `view: document` only; `effective` view's validity has always been (and
  now explicitly is) an independent rule. Also found, by testing
  end-to-end rather than only at the model layer: widening the model
  ahead of wiring up the real merge made a genuine crash reachable for the
  first time (`deep_merge()` cannot merge a `list[VariableStoreModel]`,
  confirmed via a real repro) — closed with a temporary guard during the
  one release between the model change and the merge landing, removed
  once the real merge (`merge_environment_models()`, reused unchanged from
  `_check_deployment_value_tokens()`) was wired in.
