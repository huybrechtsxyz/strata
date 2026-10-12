# Dynamic Configuration Validation — Work

- Status: draft — design sketch D1-D8. **D7 (no merge), D8
  (applied-schema reporting) and D2's working name `attributeschema`
  decided 2026-10-12/13**; D6 (bespoke vs. generic) still gates
  everything. A full code review (2026-10-13) found every factual/source
  claim accurate; its design-gap findings are tracked as Tier 4
  (Q11-Q15). Nothing built, no ADR.
- Last updated: 2026-10-13

## Overview

Strata has roughly eighteen models carrying a loosely-typed passthrough
blob: `spec.properties`, `spec.configuration`, `spec.custom`, each typed
`dict[str, Any] | None`. These exist on purpose — `resource` deliberately
never abstracted the real cloud resource type (ADR-0004), and the same
restraint is why `provider`/`module`/`namespace`/`firewall`/`environment`
all carry a passthrough escape hatch. The cost is that **everything inside
one of those blobs is unvalidated**: Pydantic does not recurse into an
`Any`-typed dict, so a typo, a missing required key, or a malformed value
reaches the provisioner untouched and fails at `terraform plan` at best, or
at runtime at worst.

This doc asks whether a blob whose shape *is* known should be declaratively
schema-checkable by strata — and, if so, where that schema lives and what
language it is written in.

**This is not a new idea in this codebase.** A narrow version of exactly
this mechanism already exists and is enforced today (see below). The
question is whether to generalize it, and how far.

### Where the demand came from

Directly out of [access-management.md](../archive/access-management.md)
(archived 2026-10-12). That doc proposed four new kinds
(`identity`/`role`/`grant`/`accesspackage`) for access management. Checking
it against the real consumers showed the opposite: `cfg-deploy`
already does workload-identity access management today, as an ordinary
`environment.spec.properties.workload_identity_config` blob, and it works.
What it lacks is validation — the corresponding Terraform component
(`iac-int-deployment/core/terraform/components/workload-identity/`) carries
five `validation` blocks and a `lifecycle.precondition` in its own
`variables.tf`, enforcing things strata could have caught earlier. The
component's own comment makes the case:

> *"A malformed subject is accepted by Entra ID and by Terraform; it only
> surfaces as pods failing to obtain a token at runtime, with nothing in the
> plan or the apply log to point at. Rejecting it here turns a production
> runtime failure into a plan error."*

Access management is therefore one *instance* of this general problem, not
a domain deserving its own kinds. Other instances almost certainly exist;
none have been catalogued yet (see Open Questions).

## Current Design / Progress

Nothing is designed or built for the general case. What follows is the
**existing narrow mechanism**, verified by reading the real source — it is
the natural starting point and also shows the limits to be designed past.

### What exists today

A `providerconfig` document can declare, per resource type, a schema for
`resource.spec.configuration`
(`src/strata/models/provider_config_model.py`):

```python
class ProviderConfigSchemaField(PlatformBaseModel):
    pattern: str                      # regex every value must match
    required: bool = True
    description: str | None = None

class ProviderConfigResourceModel(PlatformBaseModel):
    name: PlatformName
    additional_configurations: bool = False     # allow unlisted fields?
    configuration: dict[str, Union[str, ProviderConfigSchemaField]] | None
```

It is genuinely enforced, not aspirational.
`ResourceService._validate_configuration_schema()`
(`src/strata/services/resource_service.py`) is reached from
`validate_against_provider_config()`, which `semantic_checks.py` calls for
every resource whose `spec.properties.provider_type` resolves to a loaded
`providerconfig`. It emits four diagnostic codes:

| Code                              | Raised when                                                       |
| --------------------------------- | ----------------------------------------------------------------- |
| `configuration_field_not_allowed` | Field absent from schema while `additional_configurations: false` |
| `configuration_pattern_mismatch`  | `re.match(pattern, str(value))` fails                             |
| `configuration_field_missing`     | A `required` schema field is absent from the document             |
| `invalid_schema_pattern`          | The schema's own regex does not compile                           |

The same registry pattern gates two sibling cases with the same
`additional_*` escape-hatch convention: `additional_regions`
(`ProviderService`) and `additional_resources` (`ResourceService`).

So strata already has: a registry kind holding schemas, a per-entry
open/closed switch, pattern-based value checks, required-field checks, and
diagnostics wired into `validate`. That is a real foundation.

### Why it does not cover the motivating case

Five concrete limits, all verified against the source:

1. **One field, one kind.** It validates `resource.spec.configuration` and
   nothing else. There is no equivalent for `spec.properties` or
   `spec.custom` on any kind, and nothing at all for `environment`,
   `workspace`, `module`, or `namespace`. The motivating blob
   (`workload_identity_config`) lives in `environment.spec.properties`, so
   today's mechanism cannot reach it even in principle.
2. **Flat key namespace.** The schema is `dict[str, pattern]` over
   top-level keys only. `workload_identity_config.identities.<name>.namespace`
   is three levels deep and cannot be expressed.
3. **No arbitrary-key maps.** The schema is a fixed key set. The real blob
   is keyed by user-chosen workload names (`orch`, `api`), where the *key
   itself* needs validating — the Terraform component validates exactly
   that (identity-key charset, plus a collision check after `_` is
   rewritten to `-`).
4. **Regex-only type system.** Everything is stringified via
   `str(field_value)` before matching. The model's own docstring concedes
   the consequence: *"There is no native boolean type — use
   `pattern: "^(true|false)$"`"*. No int, list, map, or enum, and no way to
   say "this must be a list of exactly N CIDRs".
5. **No cross-field invariants.** The real case needs
   "`enable_workload_identity: true` requires a non-empty `identities` map
   *and* a non-blank `oidc_issuer_url`" — an invariant currently split
   across a YAML comment and a Terraform stack root. A per-field pattern
   cannot express a relationship between two fields, let alone between a
   `features` entry and a `properties` subtree.

### The tension worth naming early

[policy-engine-architecture.md](policy-engine-architecture.md) resolved
(2026-10-07) that v2 builds policy checks **bespoke, indefinitely — no
generic `PolicyModel`/engine planned**, following a standing precedent of
"model the one real check, don't port the generic engine". A general schema
language for arbitrary passthrough blobs is, structurally, a generic
engine. Either this is a genuine exception (schema-shape checking is not
policy evaluation), or the same restraint applies here and the answer is a
small number of bespoke checks instead. **This is the first question to
settle**, because it decides whether most of the Open Questions below are
even in scope.

## Proposed Design (2026-10-12) — sketch, nothing agreed

A first concrete shape, in response to "should this be
`configuration.spec.{feature}` or a dedicated kind?". **Recommendation: a
standalone kind, and nothing on `Configuration` at all.**

### D1. Standalone kind, not an embedded list on `Configuration`

This exact question already has two accepted answers in this repo, both the
same way: [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md)
promoted `Topology` out of `Workspace`, and
[ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md)
promoted `ProviderConfig`/`TopologyConfig` out of `Configuration`.
`configuration_model.py`'s own module docstring states the reasoning in
terms that apply to schemas verbatim:

> *"v1 (and this codebase's own earlier pass) embedded the full registry
> entries directly on `ConfigurationSpecModel`, which doesn't scale: a
> platform with many provider/topology types would need one shared,
> ever-growing file with no per-type ownership or reviewable diffs."*

A schema registry has exactly that growth profile — one entry per
validated blob, authored by whoever owns that blob. Embedding it would
re-create the monolith ADR-0014 just dismantled.

**Further: probably nothing is needed on `Configuration` even as a pointer
list.** `spec.providers`/`spec.topologies` exist as registration
allow-lists, but the actual `providerconfig` lookup in `semantic_checks.py`
goes straight to the document index
(`index.get(PlatformKind.PROVIDERCONFIG, provider.spec.properties.type)`),
not through that list. A schema is selected by *what it targets*, not by
name, so the check must scan all schema documents in the index regardless
— a registration list would add a second place to forget to update. Open
question: is "declared but unregistered" a state worth being able to
express?

### D2. Naming

**Working name decided 2026-10-13: `attributeschema`.** Adopted so the
rest of the design has a stable term to refer to; *final* ratification is
still deferred until Q5 and Q3 settle (see the caveat at the end of this
section, and the explicit revisit action recorded in Q5).

**Convention, from the 19 existing `PlatformKind` values** (`solution`,
`configuration`, `providerconfig`, `topologyconfig`, `provider`, `resource`,
`dns`, `network`, `firewall`, `module`, `namespace`, `workspace`,
`integration`, `tenant`, `environment`, `deployment`, `version`, `artifact`,
`asset`):

1. Single lowercase word, no separator, **no camelCase** — without
   exception.
2. **Singular.**
3. A **noun naming what the document is**, never what it contains or how it
   is processed. The only compound forms are `providerconfig`/
   `topologyconfig`, i.e. `<thing>config` for a registry governing `<thing>`.

That disqualifies most candidates before taste enters:

| Candidate                         | Verdict                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema`                          | **Ruled out on evidence.** `scaffold_controller.py` exports `model_json_schema()` one file per kind into `.strata/schemas/`, and the scaffolded `.vscode/settings.json` points `yaml.schemas` at them. A `schema` kind yields `.strata/schemas/schema.json` — the JSON Schema *for* the schema kind. Genuine collision, not a hypothetical one. |
| `block` / `attributes`            | Name the **content**, not the document. A `kind: block` document is not a block, it describes one. `attributes` is also plural, breaking rule 2.                                                                                                                                                                                                |
| `extraFields`                     | camelCase (rule 1), and backwards: the fields are the real payload, not "extra".                                                                                                                                                                                                                                                                |
| `dynamicConfig` / `dynamicSchema` | camelCase (rule 1). "Dynamic" also names the **mechanism**, not the thing — and is misleading, since from the author's seat the document is a wholly static declaration.                                                                                                                                                                        |
| `userdefined`                     | An adjective, not a noun (rule 3). Says nothing about what the document is.                                                                                                                                                                                                                                                                     |

**The document is a schema.** The only real question is the qualifier, and
`<thing>schema` is preferred over `<thing>config` here on ADR-0014's own
stated criterion — it picked `Config` over `Class` as "most obvious to a
general audience", and by that same test "schema" beats "config" for a
thing that literally is one.

| Candidate         | Reads as                          | Assessment                                                                                                                |
| ----------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `attributeschema` | "the schema for these attributes" | **Adopted as the working name (2026-10-13).** Ties to `spec.attributes`, the field most likely to survive design churn.   |
| `blockschema`     | "the schema for a block"          | Runner-up. Shorter, but `block` is a term invented in this sketch, exists only as `target.block`, and may not survive Q5. |
| `propertyschema`  | "the schema for properties"       | Too narrow — the design targets `configuration` and `custom` as well.                                                     |
| `attributeconfig` | registry governing attributes     | Fits `<thing>config` precisely, but loses the obviousness of "schema".                                                    |

**Caveat: naming is partly premature.** The right qualifier depends on Q5
(how far coverage extends) and Q3 (schema language). If `block` ceases to
be a field, `blockschema` becomes incoherent; `attributeschema` is the
more durable bet, which is why it is adopted over this doc's original
`blockschema` suggestion. The name is cheap to change now and expensive
once a `PlatformKind` value ships — so it stays a *working* name until
Tier 2 settles, with Q5 carrying the explicit action to revisit it.

### D3. Binding — `kind` + `block` alone is a regression

The sketch binds an entry with `kind` + `block`. That is not enough, and
missing it would make the new mechanism weaker than the existing one:
today's schema is keyed by **`(provider_type, resource_type)`**, both read
out of the document's own content, because an Azure storage account and an
AWS VM are both `kind: resource` with completely different valid
configuration.

So a binding needs an optional **selector**: field path → required value.

```yaml
spec:
  target:
    kind: resource
    block: configuration
    selector:                       # optional; omitted means "every document of this kind"
      spec.properties.provider_type: azure
      spec.properties.resource_type: storage_account
```

For the motivating case the selector is simply absent (every `environment`
document). This generalizes today's hard-coded two-level lookup into one
mechanism without losing it.

### D4. Attributes — `children` is necessary but not sufficient

`children` handles fixed, named nesting. It cannot express the shape that
actually motivated this work: `identities:` is a map keyed by **user-chosen
names** (`orch`, `api`), where the *key itself* needs validating. Two
distinct concepts are needed:

- **`children`** — a fixed set of named sub-attributes.
- **`entries`** — the single shape every value under an arbitrary key must
  match, with `key_pattern` constraining the keys themselves.

A `type` field is also required. Today everything is `str()`-ified and
matched by regex, which is why `provider_config_model.py` has to say *"There
is no native boolean type — use `pattern: "^(true|false)$"`"*. `pattern`
then stays optional and string-only, as the sketch has it.

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: attributeschema
meta:
  name: workload-identity-config
spec:
  target:
    kind: environment
    block: properties
  allow_extra_attributes: true        # `properties` is deliberately open
  attributes:
    - name: workload_identity_config
      type: object
      required: false
      children:
        - name: oidc_issuer_url
          type: string
          pattern: '^https://'
        - name: identities
          type: map
          key_pattern: '^[a-z0-9]+([_-][a-z0-9]+)*$'
          entries:
            type: object
            children:
              - name: namespace
                type: string
                pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
                max_length: 63
              - name: service_account
                type: string
                pattern: '^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$'
                max_length: 253
              - name: role_assignments
                type: map
                required: false
                entries:
                  type: object
                  children:
                    - name: scope
                      type: string
                      pattern: '^/subscriptions/'
                    - name: role
                      type: string
                      pattern: '.+'
```

**Acid test.** That reproduces **five of the seven** checks the real
Terraform component hand-writes in HCL (namespace charset+length, service
account charset+length, identity-key charset, scope prefix, role
non-empty). It does **not** reproduce two of them, and no per-attribute
schema language will:

- the **key-collision** check (distinct identity keys must stay distinct
  after `_` is rewritten to `-`) — a cross-entry invariant;
- the **cross-field** invariant (`enable_workload_identity: true` requires
  non-empty `identities` *and* non-blank `oidc_issuer_url`) — spans
  `features` and `properties`.

Those stay bespoke, which is the honest and probably correct outcome: the
schema covers shape, not semantics.

### D5. Relationship to the existing `providerconfig` mechanism

Three options; **(b) recommended**:

- **(a) Coexist.** New kind covers other blocks, `providerconfig` keeps its
  own. Two concepts doing one job — the worst outcome.
- **(b) One engine, two front-ends.** Reimplement
  `ResourceService._validate_configuration_schema()` on the shared
  evaluator while keeping `providerconfig`'s existing YAML shape and its
  four diagnostic codes. No breaking change, no duplicated logic; ADR-0014
  stands untouched.
- **(c) Deprecate `providerconfig.spec.resources[].configuration`.**
  Cleanest end state, breaking change, needs its own ADR.

### D6. The bespoke-vs-generic objection, restated

This design is a generic engine, which cuts against
[policy-engine-architecture.md](policy-engine-architecture.md)'s standing
resolution. The argument for treating it as a genuine exception: that
resolution concerns **policy evaluation**, where nearly every check needs a
heterogeneous external data source (SBOM, CVE scanner, Checkov, Infracost,
Terraform plan JSON, an LLM). A shape check needs **nothing but the
document already in the index**. And strata has already shipped a miniature
version of precisely this engine (ADR-0013/0014) without it metastasizing.
Still a judgement call, and still the first thing to settle.

### D7. No merge — schemas are evaluated conjunctively

**Decided 2026-10-12.** The choice was between merging every schema
targeting the same `(kind, block, selector)` into one effective schema (the
way `environment` merges), or not merging at all and checking each
independently. **No merge:** every applicable schema is looped and applied
in full, and all must pass. Getting a selector right is the engineer's
responsibility (see D8 for the safety net that makes that workable).

First, what the `environment` precedent actually does, since it is the
reason merging looks attractive. `merge_environment_models()`
(`src/strata/services/environment_service.py`) is **shallow, key-level,
last-wins** — `variables.update({v.key: v for v in spec.variables or []})`.
There is no deep merge and no field-by-field override: a whole
`VariableStoreModel` replaces a whole `VariableStoreModel`. Applied to
schemas, the analogous rule would be "last schema wins for a whole
top-level attribute, children included" — genuinely simple, and *not* the
recursive merge this doc previously worried about.

So merging is implementable. It is still the wrong choice, for four
reasons:

1. **Environment's merge order is meaningful; a schema's would not be.**
   Callers merge Tenant environments before a Deployment's own — an
   explicit specificity hierarchy ("merged in BEFORE a deployment's own",
   per `TenantSpecModel.environments`). Schema documents have no such
   hierarchy. `DocumentIndex.all_of()` returns entries *sorted by name*, so
   a merge would at least be deterministic — but alphabetical order as
   **override precedence** is semantically arbitrary, and worse than
   non-determinism because it looks stable and is not: renaming a schema
   document from `a-identity.yaml` to `z-identity.yaml` would silently
   change which pattern wins.
2. **Merging lets a schema weaken another schema.** Under last-wins,
   adding a document can *relax* a platform-team-authored constraint —
   loosen a pattern, flip a `required`, open a closed block — and nothing
   reports it. For a guardrail feature that is a governance hole.
   Conjunctive evaluation is monotonic: adding a schema can only ever
   tighten, never loosen.
3. **Diagnostics stay honest.** A conjunctive failure names the real
   document on disk that rejected the value. A merged failure names a
   synthesized schema that exists in no file, which the VS Code extension
   cannot jump to.
4. **Nothing to build.** No merge algorithm to specify, implement, order,
   or test.

**Consequence to accept.** `allow_extra_attributes` cannot stay a
*block-level* property: with no merge, one document's
`allow_extra_attributes: false` would reject attributes legitimately
declared by another document targeting the same block. Narrow it to apply
**within an attribute's `children`/`entries`**, where exactly one document
owns the subtree and the question is unambiguous. Block-level closure is
then unavailable in the new kind — acceptable, because the only real case
that uses it (`resource.spec.configuration`) keeps it through
`providerconfig.additional_configurations`, which D5(b) preserves
unchanged.

**One check this makes necessary.** Two documents targeting the same
`(kind, block, selector)` and declaring the *same* top-level attribute name
is almost certainly an authoring mistake, and can be outright unsatisfiable
(two incompatible patterns on one value, with no merge to resolve them).
That is cheaply detectable at load time and should be a diagnostic rather
than a silent contradiction.

### D8. Report which schemas were applied

**Decided 2026-10-12.** D7 makes selector correctness the engineer's job,
which is only safe if the result is visible. The failure mode that matters
is not a wrong pattern — that produces a loud error — but a **selector
that matches nothing**: the schema looks active, validation passes, and no
guardrail ran. Silence is indistinguishable from success.

Strata already has two separate channels, used for different things, and
this needs both:

| Channel                  | Goes to                                                              | Use here                                         |
| ------------------------ | -------------------------------------------------------------------- | ------------------------------------------------ |
| `log.info()` (structlog) | stderr, only under `--verbose` (`commands/options.py`)               | One line per (document, schema) actually applied |
| `diagnostics.info()`     | envelope `diagnostics[]`, counted in `summary.info`                  | Too noisy for per-pair tracing — see below       |
| `diagnostics.warning()`  | envelope `diagnostics[]`, never fails validation (`base_service.py`) | A schema that matched **zero** documents         |

Concretely:

1. **Per application — `log.info()`, not a diagnostic.** One structured log
   line per (document, schema) pair, on stderr, visible with `--verbose`.
   Precedent: `version_pins.py` and `sbom_controller.py` already report
   this way. A diagnostic per pair would scale as documents x schemas
   (dozens of environments x several schemas = hundreds of entries) and
   would inflate `summary.info` enough to drown real findings.
2. **Zero-match — `diagnostics.warning()`.** A schema document whose
   selector matched no document in the solution is almost always a typo in
   a field path or value. WARNING rather than ERROR because a solution
   legitimately may not contain the targeted kind yet (a schema shipped
   ahead of the documents it guards); warnings never fail validation, so
   this stays safe to emit unconditionally.

Deliberately **not** decided here: whether the applied-schema set also
belongs in the `validate --output json` envelope's `data` payload for
tooling (the VS Code extension could surface "this block is guarded by
schema X"). Plausible, but speculative with no consumer asking — left to
whoever builds the extension side.

## Related Decisions

- [ADR-0004](../decisions/0004-resource-model-design-decisions.md) — why
  `resource` keeps a loosely-typed passthrough instead of modeling cloud
  resource types.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  `Topology` promoted out of `Workspace`; the first instance of the
  embedded-vs-standalone reasoning D1 relies on.
- [ADR-0013](../decisions/0013-configuration-topology-registry.md) /
  [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md)
  — the registry-kind pattern the existing `providerconfig` schema
  mechanism is built on, and the precedent D1 follows.
- [access-management.md](../archive/access-management.md) — archived; the
  evidence review that produced this doc.
- [policy-engine-architecture.md](policy-engine-architecture.md) — the
  bespoke-not-generic resolution this proposal is in tension with.

## Remaining Work / Open Questions

Ordered by what they block, not by when they were raised.

### Tier 1 — gating (no design work is worth doing until these are answered)

- **Q1. Bespoke or generic?** See "The tension worth naming early" and D6.
  If the answer is "same restraint applies", this whole design is replaced
  by two or three hand-written checks and everything below is moot.
- **Q2. Is there real demand beyond one example?** Only
  `workload_identity_config` has been identified. Catalogue the actual
  passthrough blobs across `cfg-int-deployment` and `haven` and count how
  many have a known, stable, repeated shape. A mechanism justified by a
  single consumer blob is not justified. **This is answerable today with
  evidence rather than argument, and is the cheapest question on the
  list — do it first.**

### Tier 2 — shape-defining (change what gets built, not whether)

- **Q3. What schema language?** Regex-only is demonstrably too thin (limits
  2-5 above). JSON Schema would handle nesting, types and arbitrary-key
  maps (`additionalProperties`/`patternProperties`) for free, but is a
  large dependency surface and emits diagnostics in its own vocabulary,
  not strata's. A strata-native subset (D4's sketch) avoids that at the
  cost of building it. D4 assumes the native subset; that assumption is
  not yet justified.
- **Q4. Should the schema be derived rather than authored?** The consuming
  Terraform root module's `variables.tf` *is already* a schema, with types,
  defaults and `validation` blocks — hand-authoring a second copy in YAML
  guarantees drift. Note v1 reportedly rejected any declared key missing
  from the root's `variables.tf` (per `cfg-int-deployment`'s own
  `stacks/customer/environment.yaml` comment) — **unverified against v1's
  real source; must be checked before being relied on.** Deriving would
  only ever work for Terraform, never Helm/Compose, so at best it is a
  supplement, not the mechanism.
- **Q5. How far does coverage extend?** `configuration` only, or also
  `properties`/`custom`/`variables`/`features`? D4's acid test fails the
  cross-field invariant precisely because it cannot see `features`.
  Widening the target may not actually help (that invariant is semantic,
  not shape) — worth confirming rather than assuming.
  **Required action on resolution (closes Q14):** revisit D2's working
  name. If `block` survives as a `target` field, `blockschema` becomes
  viable again; if coverage narrows to `properties` alone,
  `propertyschema` does. Do not let `attributeschema` ship unexamined
  just because it was the working term.
- **Q6. Confirm D1** (standalone kind, nothing on `Configuration`).
  Argued from the ADR-0011/ADR-0014 precedent and recommended, but not
  ratified. *Supersedes the earlier open question "where does a schema
  live?", whose candidate list D1 has already worked through.*

### Tier 3 — detail (settle during implementation, not before)

- **Q7. Which `type` values exist**, and what each permits beyond `pattern`
  (`max_length` appears in D4's example; `min`/`max`/`enum`/`format` are
  all plausible and all scope creep). Keep the initial set minimal and
  evidence-driven.
- **Q8. Is "declared but unregistered" a meaningful state?** D1 argues
  nothing is needed on `Configuration`, which removes the ability to
  disable a schema document without deleting it. Check whether anyone
  wants that.
- **Q9. Where does evaluation live?** `services/` (alongside
  `ResourceService`'s existing check) or a `controllers/semantic_checks.py`
  pass. D5(b) implies a shared evaluator both can call; the layering
  contract (ADR-0003) decides where it may sit.
- **Q10. Diagnostic path quality.** Today's codes are flat and
  field-scoped. A nested schema needs a path-shaped location
  (`properties.workload_identity_config.identities.orch.namespace`) that
  the VS Code extension's `fieldLocation.ts` heuristic can still map back
  to a YAML range.

### Tier 4 — code-review follow-ups (2026-10-13)

Raised by a full code review of this doc (every factual/source claim in it
was checked against the real codebase and holds up — no inaccuracies
found). These are design gaps/loose ends found during that review, not
factual corrections — tackle one at a time, in any order, before Tier 2
work starts.

- [ ] **Q11. D3's selector may be reinventing existing prior art.**
  [`references.py`](../../src/strata/controllers/references.py)'s
  `_walk()` already follows a dotted path (with `[]` list syntax) across an
  already-validated Pydantic document and yields the leaf value — close to
  what a `spec.properties.provider_type: azure` selector needs to
  evaluate. It's currently private and reference-specific (filters to
  string/list-of-string leaves only), and a second, unrelated dotted-path
  mechanism (`_set_path()`/`split_path()` in `terraform_projection.py`,
  for secret-path reassembly) already exists too — check whether D3 can
  reuse/adapt one of these instead of adding a third parallel
  implementation.
- [ ] **Q12. D7's "load-time duplicate-attribute check" has no concrete
  spec.** Asserted as necessary but not yet given a diagnostic code name,
  a severity, or a scope (checked only at the top-level attribute, or
  recursively into `children`/`entries` too?). Close this before Tier 2
  implementation starts.
- [ ] **Q13. D4's `required` semantics are ambiguous for map (`entries`)
  attributes.** Does `required: true` on a `type: map` attribute mean "the
  key must be present" or "the map must be non-empty"? The acid-test
  example doesn't exercise this distinction (`identities` isn't marked
  `required`) — pick one and say so explicitly.
- [x] ~~**Q14. Wire D2's naming back to Q5's resolution.**~~ **Closed
  2026-10-13.** `attributeschema` adopted as the working name, and Q5 now
  carries an explicit "revisit D2 on resolution" action rather than
  leaving it implied.
- [ ] **Q15. Consider stating D6's dividing line as a tentative answer,
  not just an open question.** "Policy evaluation needs heterogeneous
  external data (SBOM/CVE/Checkov/Terraform plan/an LLM); a shape check
  needs nothing but the document already in the index" is a clean,
  testable criterion already argued in D6 — strong enough to record as
  this doc's working answer to Q1 (revisit only if Q2's evidence-gathering
  disagrees), rather than leaving Q1 purely open.

### Resolved

- ~~**Merge semantics when two schema documents target the same
  `(kind, block, selector)`.**~~ **Resolved 2026-10-12 — D7: no merge.**
  Schemas are evaluated conjunctively; `allow_extra_attributes` narrows
  from block-level to attribute-level, and a load-time duplicate-attribute
  check becomes necessary.
- ~~**How are applied schemas made visible?**~~ **Resolved 2026-10-12 —
  D8**: `log.info()` per application under `--verbose`, plus a
  `diagnostics.warning()` for any schema matching zero documents.

## Changelog

- 2026-10-12: Created, superseding the archived
  [access-management.md](../archive/access-management.md). Framed the
  problem, documented the existing `providerconfig`-driven
  `resource.spec.configuration` mechanism (verified against real source)
  and its five concrete limits, and flagged the bespoke-vs-generic tension
  with `policy-engine-architecture.md` as the blocking first question.
- 2026-10-12: Added the Proposed Design sketch (D1-D6) — standalone
  `blockschema` kind rather than an embedded `Configuration` list (ADR-0011/
  ADR-0014 precedent), a `selector` binding without which the design would
  regress on today's `(provider_type, resource_type)` keying, and
  `children` + `entries`/`key_pattern` + `type` for nesting and
  arbitrary-key maps. Acid-tested against the real Terraform component:
  reproduces 5 of its 7 hand-written validations; the cross-entry and
  cross-field invariants are not expressible and stay bespoke.
- 2026-10-12: Resolved the merge-semantics open question as **D7: no
  merge** — schemas evaluated conjunctively. Grounded in what
  `merge_environment_models()` really does (shallow, key-level, last-wins,
  not a deep merge) and in `DocumentIndex.all_of()` being name-sorted,
  which would make a merge deterministic but give it semantically
  arbitrary precedence. Decisive argument is monotonicity: merging would
  let one schema silently weaken another. Knock-on effects recorded:
  `allow_extra_attributes` narrows to attribute scope, block-level closure
  stays only in `providerconfig`, and a load-time duplicate-attribute
  check becomes necessary.
- 2026-10-12: Added **D8: report which schemas were applied**, the safety
  net D7 requires now that selector correctness is the engineer's
  responsibility. Per-application tracing goes to `log.info()`/`--verbose`
  (stderr) rather than `diagnostics.info()`, which would scale as
  documents x schemas and drown real findings; a schema matching **zero**
  documents gets a `diagnostics.warning()`, since a silently-inert
  guardrail is the one failure this design cannot otherwise surface.
- 2026-10-12: Reworked **D2 (naming)** against the real `PlatformKind`
  vocabulary (all 19 values: single lowercase noun, singular, naming what
  the document *is*). Bare `schema` ruled out on evidence —
  `scaffold_controller.py` already exports one `model_json_schema()` file
  per kind into `.strata/schemas/`, so the name genuinely collides.
  camelCase candidates fail the convention outright; `block`/`attributes`/
  `extraFields` name the content rather than the document; `dynamic*`
  names the mechanism and misleads (the document is a static
  declaration). **Recommendation changed from `blockschema` to
  `attributeschema`** — it ties to `spec.attributes` rather than to
  `target.block`, a term invented in this sketch that may not survive Q5.
  Final ratification deferred until Tier 2 settles.
- 2026-10-13: Full code review — every factual/source claim in this doc
  (the 19 `PlatformKind` values, the four `providerconfig` diagnostic
  codes and their exact trigger conditions, `merge_environment_models()`'s
  shallow-last-wins behavior, `DocumentIndex.all_of()`'s name-sort,
  `Diagnostics.ok`/`.warning()` semantics, the ADR-0014/`configuration_
  model.py` quotes, the `.vscode/settings.json` `yaml.schemas` collision,
  and the `log.info()`/`--verbose` precedent) checked directly against
  source and confirmed accurate — no corrections needed. Added Tier 4
  (Q11-Q15): design gaps found during the review (D3's selector
  possibly reinventing `references.py`'s existing dotted-path walker,
  D7's duplicate-attribute check left unspecified, D4's `required`
  semantics on map attributes left ambiguous, D2 not explicitly wired to
  revisit once Q5 resolves, and D6's dividing line being strong enough to
  record as a tentative answer rather than purely an open question).
- 2026-10-13: **`attributeschema` adopted as the working name** (D2), so
  the rest of the design has a stable term. Final ratification stays
  deferred — the name is cheap to change now and expensive once a
  `PlatformKind` value ships. **Q14 closed** by recording the revisit as
  an explicit required action inside Q5's own resolution (including which
  alternative wins under which Q5 outcome) instead of leaving it implied.
