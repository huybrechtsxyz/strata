# Requirement, Interface, Injection, Grant, Value, and Translation — Lessons from v1's References Model

- Status: proposed
- Date: 2026-09-20
- Revised: 2026-09-21 — added **Value** as a fifth, distinct concept (the
  document-local `value`/`var`/`secret`/`feature` binding site), after
  confirming in conversation that Value and Requirement are easily confused by
  readers even though they answer different questions. Renamed from
  "Requirement, Interface, Injection, and Grant" accordingly.
- Revised: 2026-09-21 — added **Translation** as a sixth concept: v1 (per
  ADR-0001's own "no renaming happens" principle) has no mechanism for a
  pre-existing, team-owned provisioner module whose variable name for a
  concept differs from the platform's canonical name (e.g. platform `region`
  vs. a team's Terraform `location` for the same value). Confirmed as a real,
  not hypothetical, gap in conversation.
- Revised: 2026-09-21 — **decided Requirement (`spec.references`) will not
  exist as a field in v2 kind models.** Working through Requirement in
  isolation (see "V2 decision" section below) found both of its v1 jobs are
  better solved without it: scoping is derivable as `Injection = Interface ∩
  Environment`, and typo-catching is stronger when done as a direct Phase 2
  check against `Environment` instead of against a hand-authored, driftable
  list. This supersedes the "decide Job 1 vs Job 2 per kind" guidance below
  for Requirement specifically — there is no per-kind decision to make because
  the field itself is not being built.
- Revised: 2026-09-21 — worked through **Interface** on its own. Unlike
  Requirement, concluded Interface is **load-bearing, not optional** — the
  `Injection = Interface ∩ Environment` formula this ADR now depends on
  cannot work without it. Found a concrete v1 implementation bug (Interface is
  parsed, then discarded before shaping delivery) that v2 must not repeat
  structurally. See "V2 decision: Interface" below.
- Revised: 2026-09-21 — worked through **Injection**. Confirmed it as its own,
  third composition mode (intersection — neither union nor restriction),
  scoped to the Environmental channel only, with a new required check (a
  required-but-Environment-missing Interface variable must be a hard build
  error, not a silent gap) and an explicit note that Grant layers on top of
  Injection rather than replacing it. See "V2 decision: Injection" below.
- Revised: 2026-09-21 — worked through **Grant**. Kept it (unlike Requirement)
  as its own mechanism, on `stages[]` (invocation-scoped, not
  provisioner-scoped — the right home). Concluded it should be
  **derived-by-default from stage kind** (`plan` → deny secrets, `apply`/
  `destroy` → allow secrets) with a narrow allow/deny override for exceptions,
  not a mandatory hand-authored allowlist — and confirmed it is **deliberately
  secrets-only**: variables/features have no confidentiality reason to
  restrict, and withholding them would break `terraform plan` itself (which
  needs a value for every declared variable, sensitive or not, to compute a
  valid plan graph). Exact schema left for later — this records the
  conceptual model only. See "V2 decision: Grant" below.
- Revised: 2026-09-21 — worked through **Value**. Clarified it is a
  schema-time union baked directly onto a specific field (not a runtime
  path-targeting/overwrite mechanism). Found DNS's `output_key` is a third
  input channel (a prior stage's execution output — later even than
  Interface's build-time-only constraint), and deliberately deferred deciding
  whether to generalize it beyond DNS until a second real consumer exists —
  generalizing from one example would repeat Requirement's original mistake.
  See "V2 decision: Value" below.
- Revised: 2026-09-21 — recorded why universal Jinja templating was rejected
  in favor of `ValueSourceModel`'s discriminated fields (grounded in v1
  ADR-0073's own analysis of this exact question — static secret routing is
  the decisive blocker), and a three-pattern framework (always-literal /
  always-reference / either-or-union) for deciding, per field, whether it
  needs `ValueSourceModel` at all — with an explicit decision **not** to
  retrofit it speculatively onto existing fields (e.g. `Provider.spec.properties.region`,
  which already has a competing whole-file-swap mechanism from v1 ADR-0036).
- Revised: 2026-09-21 — reconnected **Translation** to the Injection formula
  designed afterward: `Interface ∩ Environment` is a literal by-name
  intersection, so an untranslated name mismatch (`location` vs `region`)
  would incorrectly trigger Injection's own required-but-missing hard-fail
  even when the value is genuinely satisfiable. Translation must run before
  the intersection, making it load-bearing for Injection's correctness in
  the team-owned-module case, not merely a convenience. Placement
  (provisioner/topology declaration, not `stages[]`) reconfirmed against the
  same provisioner-scoped-vs-invocation-scoped axis used for Grant.
- Revised: 2026-09-21 — **superseded `ValueSourceModel`** with a single
  unified Value syntax: v1's proven `${var:KEY}`/`${secret:KEY}`/
  `${feature:KEY}` embedded-token substitution (ADR-0075), reused verbatim
  rather than reinventing a new one. Triggered by a real need
  `ValueSourceModel` couldn't express (combining multiple sources into one
  string, e.g. a connection string). Surveyed real platform conventions
  (GitHub Actions, CloudFormation, Azure DevOps, Kubernetes, Ansible) before
  keeping v1's own syntax — chosen specifically because it never uses `{{`,
  avoiding the Helm/Jinja collision risk every `{{`-based alternative shares.
- Related: [v2 ADR-0001](0001-v1-schema-analysis-findings-for-v2.md), v1 ADR-0078
  (scoping-variables-and-features-to-provisioners), v1 ADR-0084
  (variables-secrets-features-delivery-model), v1 ADR-0063
  (team-owned-terraform-module-support), v1 ADR-0017
  (jinja2-template-engine), v1 ADR-0073
  (embedded-string-syntax-inventory-and-creep-prevention), v1 ADR-0075
  (unify-terraform-helm-value-expression-syntax)

## Context and Problem Statement

v2 already ported `spec.references` (`ProviderReferencesModel` — `variables`,
`secrets`, `features` key-name lists) onto `ProviderModel`, copied from v1's
schema. v1 designed this field **before** it had been exercised against real
multi-provisioner, multi-root platforms. Once it was used for real (a workspace
with one Terraform root that owns everything, vs. a workspace composing five-plus
independent, pre-existing Terraform roots each with its own input interface), v1
discovered the model was incomplete, and documented the findings in ADR-0078
(2026-09-11 → revised through 2026-09-15) and ADR-0084 (2026-09-20).

v2 has not yet built the kinds where this bites hardest (`resource`, `network`,
`module`, `dns`, and the provisioner/build/deploy layer), so this is the point to
design it correctly rather than discover the same gap later by repeating v1's
path.

## What v1 learned, after real implementation

### Six concepts; v1 modeled one and a half, plus a fifth that gets confused with the first, plus a sixth it never modeled at all

| # | Concept | Question it answers | v1 status |
|---|---|---|---|
| 1 | **Requirement** | "What does this component need, by name?" | Modeled in v1 (`spec.references`). **v2: rejected** — see "V2 decision" below |
| 2 | **Interface** | "What can this provisioner/root accept?" | Parsed (e.g. Terraform `variables.tf`) but **discarded** — computed by one code path, ignored by the one that emits values. **v2: load-bearing** — see "V2 decision" below |
| 3 | **Injection** | "What does it actually get?" | v1: never defined, defaults to "everything." **v2: `Interface ∩ Environment`** — see "V2 decision" below |
| 4 | **Grant** | "What is this run allowed to see?" | v1: secrets-only (`stage.secrets`), deploy-time, hand-authored. **v2: kept, secrets-only confirmed correct, derived-by-default** — see "V2 decision" below |
| 5 | **Value** | "At *this specific point* in the document, what literal/key supplies the value?" | Modeled per-kind, inconsistently (DNS/Module: flat fields; Network: nested `CidrSourceModel`) — see below |
| 6 | **Translation** | "The platform calls it X; this specific provisioner's code calls it Y — how do they connect?" | **Not modeled at all** — ADR-0001 explicitly states "no renaming happens" as policy, which holds only until a pre-existing, team-owned module's naming doesn't match |

(3) is the structural gap. Because it was never decided, `stage.secrets` ended up
silently standing in for it — "the secret allowlist is compensating for the
absence of a definition of injection" (v1 ADR-0084).

### Requirements compose by union; grants compose by restriction

- **Requirements compose by union.** If component A needs `x` and component B
  needs `y`, both must be satisfied — adding a component can only add needs.
- **Grants compose by restriction.** The narrowest applicable permission wins —
  adding a constraint can only remove.

These are opposite operations. v1's build-time injected set was derived as a
**union of requirements**, which structurally cannot narrow — there is no
subtraction in that formula. The observed symptom ("every provisioner sees every
environment key, and fails validation against its own narrower interface") was not
a bug to patch; it is what a union-only formula must produce. Any narrowing
mechanism needs its own, separate, restriction-composed field — it cannot be
retrofitted onto the requirements list.

### `references` ended up doing two unrelated jobs

v1 audited every consumer of `.references` and found it serves two jobs that
happen to share one field, and that this is load-bearing (not incidental) for
three kinds:

1. **Job 1 — provisioner input scoping.** `resource`/`module`/`provider`
   references are unioned into the provisioner's validation scope (this is the
   "Requirement" concept above). Optional; absence means unscoped.
2. **Job 2 — document-local value-binding namespace.** `dns`, `module`, and
   `network` documents let sibling fields reference a key *by name* (e.g. a DNS
   record's `var: api_endpoint`), and `spec.references` is the namespace those
   names resolve against. This is **mandatory** — used at build time to validate
   that every `var:`/`secret:`/`feature:` reference actually appears in
   `references`, and the build errors if it doesn't.

`module` documents do **both jobs with the same field**. Any future v2 kind that
lets sibling fields bind to a variable/secret/feature by name (following the DNS
flat-union or Network nested-object patterns already flagged in
[v2 ADR-0001](0001-v1-schema-analysis-findings-for-v2.md)) must decide up front
whether it reuses `references` for both jobs (as v1's `module` does) or keeps them
as two separate fields — silently drifting into "same field, two jobs" the way
v1 did makes the field's contract harder to reason about later.

### Value is not Requirement — they are easily confused, and must not be merged

Job 2 above ("document-local value-binding namespace") is really a distinct
fifth concept, **Value**, wearing the same field names (`var:`/`secret:`/
`feature:`) as Requirement. They answer different questions and must not be
collapsed into one mental model just because both deal in "variable/secret/
feature key names":

| | Requirement (`spec.references`) | Value (`var:`/`secret:`/`feature:` on a specific field) |
|---|---|---|
| Question | "What keys does this *document* need, in total?" | "What supplies the value for *this one field*, right here?" |
| Shape | A flat list of key names | A single key name (or a literal), attached to one specific field |
| Cardinality | One list per document | One per resolvable field — a document can have many |
| Without it | Nothing is declared — unscoped (Job 1) or nothing validates (Job 2) | A field has no value at all — the document doesn't compile/build |

Concretely:

```yaml
spec:
  references:
    variables: [internal_network_cidr]   # Requirement: "I need this key to exist"
subnets:
  - name: app-subnet
    var: internal_network_cidr           # Value: "plug that key's value in HERE"
```

`references` is the declared namespace; `var:`/`secret:`/`feature:` on a
specific field are pointers *into* that namespace at one point in the document.
A build-time check (v1's `DnsSpecModel.validate_references_declared()`) ties
them together: every Value binding's key must appear in the Requirement list,
or the build fails immediately (catching a typo before deploy, not after).

### The Value binding shape: v1 has two incompatible shapes for the same concept

Checking the actual field definitions (not just the ADR-0001 discrepancy note)
across the three v1 kinds that have this:

- **DNS** (`DnsRecordModel`) — flat: `value`/`var`/`secret`/`output_key` sit
  directly on the record, alongside `name`/`ttl`. Validator: exactly one of the
  four must be set.
- **Module** (`ModuleServiceEnvironmentModel`) — flat: `key`/`value`/`var`/
  `secret`/`feature` directly on the entry. Same "exactly one of" shape as DNS.
- **Network** (`SubnetModel`/`CidrSourceModel`) — nested: `value`/`var`/`secret`
  are pulled into a separate `CidrSourceModel` class, and `SubnetModel.cidr:
  CidrSourceModel` nests it one level deeper (`subnet.cidr.value` vs. the flat
  `record.value`/`entry.value` used everywhere else).

**Flat is the majority convention (2 of 3 kinds)** — Network is the outlier,
confirming ADR-0001's instinct that DNS's shape is the one to standardize on.
But there is a second, independent problem: all three kinds **hand-write their
own near-identical "exactly one of value/var/secret[/output_key/feature]"
validator** — a real DRY violation, separate from the shape inconsistency.

**Proposed v2 fix:** one shared base in `common_models.py`:

```python
class ValueSourceModel(PlatformBaseModel):
    """value/var/secret union. Inherit directly on the object representing the
    one thing being resolved — never nest under a named sub-field. Matches the
    majority v1 convention (DNS, Module), not the Network outlier."""

    value: str | None = Field(None, description="Literal value")
    var: str | None = Field(None, description="Variable key — resolved at build time")
    secret: str | None = Field(None, description="Secret key — resolved at deploy time")

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "ValueSourceModel":
        ...  # generalized over whichever optional fields the subclass adds too
```

- `DnsRecordModel(ValueSourceModel)` adds `output_key` (DNS-only — ADR-0001 #2
  is still open on whether to generalize this)
- Subnet/CIDR-list entries inherit it directly — flattened, no `cidr:` indirection
- `ModuleServiceEnvironmentModel(ValueSourceModel)` adds `feature`

**Superseded — see "V2 decision: one unified Value syntax" below.** Recorded
here for the historical reasoning (flat vs. nested, DRY violation); the
discriminated-union shape itself is not what v2 builds.

### V2 decision: Value is a schema-time union, not a runtime patch mechanism

Worth stating explicitly, since it's easy to picture Value as a generic
"target this field path, overwrite it with this value" system (like a JSON
patch or templating engine). It is not. The union lives **directly on the
field itself**, decided at schema-design time — `DnsRecordModel(ValueSourceModel)`
means the record's address field literally *is* `value | var | secret`; there
is no separate `key:`/path pointing at some other field to overwrite. Every
field that wants this behavior must be modeled that way explicitly by its own
kind's author; it cannot be retrofitted onto an arbitrary field from outside.

### V2 decision: one unified Value syntax — embedded `${var:}`/`${secret:}`/`${feature:}` tokens, not `ValueSourceModel`

**The trigger:** a real need surfaced that `ValueSourceModel` cannot express
at all — a field that combines *multiple* sources into one string (e.g. a
connection string: `postgresql://{user}:{password}@{host}/{db}`), not just a
choice of exactly one. `ValueSourceModel`'s discriminated union is
whole-field replacement only; it has no way to say "concatenate these."

**v1 already solved this — for a different pair of kinds.** Not hypothetical:
v1 ADR-0075 unified Terraform backend config and Helm values on
`${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` — a **partial regex
substitution** (works embedded anywhere inside a larger literal string, any
number of tokens, mixed types), with a routing rule: if a leaf's resolved
string contains **at least one** `${secret:...}` token (even mixed with
`var:`/`feature:` tokens), the whole leaf is secret-shaped — passed as
`--set-string`/`TF_VAR_*`, never written to disk. Statically parseable by a
simple regex (`EXPR_PATTERN`) — no code execution needed to classify a leaf,
the same static-secret-detection property that ruled out Jinja earlier in
this ADR.

**Decided: unify on this token syntax as the single Value mechanism, dropping
`ValueSourceModel`'s discriminated union entirely.** Every Value-binding field
becomes a plain `str`, optionally containing `${var:}`/`${secret:}`/
`${feature:}` tokens — a literal is simply a string with no tokens in it. One
mechanism handles both the single-reference case (`"${var:internal_network_cidr}"`)
and the composite case (the connection-string example) with no per-field
choice to make.

**Real-world syntax survey done before keeping v1's own** (not just reused
out of inertia):

| Platform | Syntax | Kind discrimination | Verdict for strata |
|---|---|---|---|
| GitHub Actions | `${{ secrets.X }}` / `${{ vars.X }}` / `${{ env.X }}` | Yes, by namespace | Most widely recognized, but starts with `{{` — collision risk with Helm/Jinja templating strata already uses elsewhere |
| AWS CloudFormation | `{{resolve:secretsmanager:id:key}}` | Yes, by service prefix | Bare `{{ }}`, same collision risk, no `$` to distinguish |
| Azure DevOps | `$(name)` / `${{ variables.name }}` | **No** — secret-ness is a separate declared flag, not part of the reference syntax | Doesn't give static secret detection from the token alone |
| Kubernetes | `valueFrom.secretKeyRef` | Yes, but structured YAML, not an embeddable string token | Can't express the composite/concatenation case at all |
| Ansible / Consul-template | `{{ var }}` (Jinja/Go-template) | No | Same Jinja collision problem already ruled out |
| **v1's own** | `${var:KEY}` / `${secret:KEY}` / `${feature:KEY}` | Yes, explicit prefix | **Never uses `{{`, zero collision with Helm/Jinja, already proven in strata's own codebase** |

**Chosen: keep v1's own `${var:}`/`${secret:}`/`${feature:}` syntax
verbatim.** GitHub Actions' `${{ }}` is more widely recognized, but strata
already uses `{{ }}`-based templating (Jinja) elsewhere (v1 ADR-0017), and
any `${{`-prefixed syntax still visually starts with the same two braces —
risking exactly the confusion v1's design deliberately avoided. Zero
collision risk wins over marginal recognizability here.

**Extends to `output_key`, closing that open question too:** generalizes as
a fourth token, `${output:KEY}` — same syntax, same static-parseability,
resolved against the preceding stage's outputs instead of `Environment`.
Still deferred in implementation (no second kind needs it yet), but the
*shape* it would take if generalized is now decided rather than open.

**Known cost, accepted:** fields that are natively non-string and
schema-validated (e.g. a disk `size: int`) lose Phase-1 type checking if they
need to become `str` to allow token embedding — validation of the resolved
value moves to after resolution (build time). Narrow impact: nearly every
Value-binding candidate identified so far (DNS record value, CIDR, env var
value) is already a string field; this is accepted as a small, known
trade-off, not treated as a blocker.

### `output_key`: a third channel, generalization deliberately deferred

`output_key` doesn't fit either of the two channels already documented
(Structural, Environmental) — it's a **third channel**: a value sourced from
a *preceding deployment stage's actual execution output* (e.g. a VM's public
IP from a Terraform `output` block), only knowable once that stage has run.
That's even later than Interface's build-time-only constraint — it's
deploy-time and execution-order-dependent.

Conceptually this isn't DNS-specific — "bind this field to a value a prior
stage produced" is a general need (a `Resource` tag, a `Module` env var, a
connection string could all want it), DNS just needed it first in v1. But
**generalizing it now would repeat Requirement's mistake** — designing a
shape before a second real consumer exists to validate it against. Deferred
deliberately: keep `output_key` DNS-only until a second kind genuinely needs
the same thing, then generalize from two real examples instead of one guess.

### Why not universal Jinja templating instead of `ValueSourceModel`?

Raised in conversation: since Jinja2 is already strata's templating engine
(v1 ADR-0017), why not support `{{ secret.db_password }}`-style expressions
directly in any schema field, everywhere, instead of a discriminated
`value`/`var`/`secret` union per kind? v1 already asked this **exact**
question (v1 ADR-0073, "Addition (2026-09-17): impact of converging every
expression on Jinja2 syntax") and rejected it, for reasons directly relevant
here:

1. **It breaks static secret routing.** v1's reference system needs to know,
   *without executing anything*, whether a given field is a secret — to
   route secret-bearing values to `--set-string`/`TF_VAR_*` (never written to
   disk) versus non-secret values (written to a plain resolved file), and to
   validate references at build time. Jinja's static analysis only sees
   top-level names (`{{ secret.db_password }}` → `{"secret"}`, not the key
   `db_password`), and dynamic forms (`{{ secret[name] }}`) are statically
   undecidable. `ValueSourceModel`'s `value`/`var`/`secret` are separate,
   statically-typed fields — no execution needed to know which one a leaf is.
   This is the same property Value bindings already rely on for the direct
   Phase 2 `Environment` cross-check (see the "V2 decision: Requirement"
   section above) — losing it would undo that.
2. **Helm collision.** `{{ }}` is Helm's own Go-template/Sprig delimiter;
   off-the-shelf charts ship literal `{{ }}` in `values.yaml`. Strata
   references in Jinja syntax would be indistinguishable from chart-native
   templating.
3. **New security surface.** Authored YAML becomes executable code (arbitrary
   Jinja logic, not just a value slot), and strata YAML is routinely consumed
   from other repositories. Mitigable with a sandboxed environment, but that
   is a new thing to own, not a free swap.

**Conclusion: keep the discriminated `ValueSourceModel` approach — do not
adopt Jinja surface syntax for schema fields.** If a kind genuinely needs
*computed* values (e.g. Network deriving a subnet CIDR from a base CIDR and
input parameters) that's a different, narrower need — v1's own answer for
that was a dedicated `ExpressionModel` with an explicit `kind:` discriminator
(`path`/`yaml`/`jinja`/`regex`), not one universal templating language. Not
designed here — a candidate for its own decision when Network is built.

### Three patterns for a field's value — decide per-field, don't retrofit speculatively

Working through candidate fields revealed strata already has (or needs) three
different patterns, not one, and conflating them would be a regression:

| Pattern | Shape | Example | Why |
|---|---|---|---|
| Always literal | plain `str`/`int`, no indirection | `Resource.spec.properties.resource_type` | A structural fact, never meant to vary by environment |
| Always a reference, never literal | plain `str`, but the string *is* a key name by convention | `AuthenticationModel.client_id`, `.access_key_id` | Credentials should never have a "just hardcode it here" escape hatch |
| Either literal or reference, explicit union | `ValueSourceModel` (`value`/`var`/`secret`) | `DnsRecordModel`, `ModuleServiceEnvironmentModel` | The field's value genuinely needs to vary by environment without duplicating the whole document |

The deciding question per field is **"does this value legitimately need to
vary by environment without duplicating the whole document?"** — not "could
this technically be a union." Applying the union pattern everywhere would
blur it with the other two, which exist for good reasons (structural facts
shouldn't be parameterizable; credentials shouldn't have a literal escape
hatch).

**A concrete tension this framework exposes, already latent in what's
built:** `Provider.spec.properties.region` is a real candidate for the
union pattern (region differs dev/staging/prod) — but
[ADR-0003](0003-provider-model-design-decisions.md)/v1 ADR-0036 already solve
that exact problem a *different* way: swap the whole provider **file** per
environment, not parameterize the field. Adding `ValueSourceModel` to
`region` now would create two competing mechanisms for the same problem.
**Not resolving this now** — recording it so it's a deliberate choice
(pick one) whenever Provider's environment-override story is actually
designed, not an accident of generalizing `ValueSourceModel` too eagerly.

**Decision: do not retrofit `ValueSourceModel` onto existing fields
speculatively.** Apply it kind-by-kind, field-by-field, driven by real need
as each kind is built or revisited — the same discipline already used to
defer `output_key`'s generalization.

### Translation: when the platform's name and the provisioner's name for the same concept differ

Confirmed as a real (not hypothetical) case: a workspace composes a
pre-existing, team-owned Terraform module (v1's `dispatcher_api`, per v1
ADR-0063 "team-owned-terraform-module-support"). The platform's canonical key
for a concept is `region`; that team's `variables.tf` — written independently,
without knowledge of platform naming conventions — calls the same concept
`location`. v1's own stated policy (ADR-0001: "References are just key name
lists... NO conversion/renaming happens... Key names stay consistent from
declaration through environment resolution") makes this **structurally
unrepresentable**: there is nowhere in v1's schema to say "these two names mean
the same value."

This is different from every concept above:

- Not **Requirement** — the platform still only needs to declare `region` once;
  it doesn't need two requirements.
- Not **Value** — a Value binding picks *which* canonical key supplies one
  field's value; it doesn't rename that key for a specific consumer.
- Not **Interface** — Interface is "what can this root accept" (its declared
  variable names, e.g. `location` itself); Translation is the missing bridge
  *between* the Requirement's name and the Interface's name.

**Why this didn't surface earlier:** it's invisible whenever the platform
authors (or at least names) both sides — a platform-owned Terraform module can
simply be written to call the variable `region`, matching the canonical name,
so there's nothing to translate. It only appears once a **pre-existing,
independently-owned** module is composed into the platform, which is exactly
v1 ADR-0063's scenario and exactly why v1 never designed for it: the reference
model was designed platform-first, before team-owned/cross-repo composition
was a real requirement.

**Where Translation should live:** scoped to the specific provisioner/consumer
boundary, not the reusable kind model. `Resource`/`Provider`/etc. must stay
portable across different provisioners without knowing any one provisioner's
naming quirks — so the alias table belongs on whatever wires a canonical
key to a concrete external module (the provisioner or topology-component
declaration, not yet built in v2), roughly:

```yaml
topology:
  - name: dispatcher
    provisioner: dispatcher_api
    input_aliases:
      region: location   # canonical platform key -> this provisioner's local name
```

Not designed in detail yet — this ADR records that the concept exists and
where it does *not* belong (not on the kind models), not a finished schema.

### Translation reconsidered: it's now required infrastructure for Injection, not just a nice-to-have

Reconnecting Translation to the `Injection = Interface ∩ Environment` formula
(designed after Translation was first written up) surfaces a real interaction:
`Interface ∩ Environment` is a literal set intersection **by name**. For
`dispatcher_api`, Interface says the module declares `location` (required, no
default); Environment declares `region`. Same value, semantically — but as
bare strings, `"location" != "region"`, so the intersection finds no match.

Worse, this collides with Injection's own hard-fail rule (Decision Outcome
point 8): "a required Interface variable absent from the intersection is a
build error." Without Translation, a perfectly satisfiable case would
incorrectly trigger that failure, even though nothing is actually missing —
the value exists, just under a different name.

**So Translation must run *before* the intersection is computed** — normalize
one vocabulary into the other so `Interface ∩ Environment` recognizes them as
the same key — and translate back when writing the actual output (Terraform
still needs the literal name `location` in its `.tfvars`, not `region`).
This makes Translation load-bearing for Injection's correctness whenever
local/canonical names diverge, not merely a convenience for team-owned
modules.

**Placement reconfirmed against the provisioner-scoped vs. invocation-scoped
axis already established for Grant vs. Injection.** Grant is invocation-scoped
(`stages[]`); Interface capability and `needs:` are provisioner-scoped —
fixed regardless of who invokes the provisioner. Translation sits on the same
side as the latter: a team-owned module's naming doesn't change based on
which stage invokes it. So `input_aliases` belongs on the provisioner/topology
declaration, never on `stages[]` — this was already the conclusion above, now
reinforced by the same axis that organizes the rest of this ADR.

**Worked example**, extending `web_infra`/`dispatcher_api`:

```yaml
topology:
  - name: dispatcher
    provisioner: dispatcher_api
    input_aliases:
      region: location   # canonical platform key -> this module's local name
```

At Injection time: `Interface = {location: required}`, `Environment =
{region: eu-west-1}`. Translation maps `location ↔ region` first, so the
intersection correctly resolves — `region`'s value flows in, written out as
`location` in the actual Terraform input, and Injection's required-but-missing
hard-fail does **not** incorrectly fire.

### Requirement is not the same as Authority

A resource does not read a variable; the provisioner that materializes it does —
so a requirement declared on a resource is a statement about code one step
removed from the declaring object. The useful test for where a concern belongs:
**who is the authority, and who would be wrong to override them?**

| Role | Authority on | Artifact |
|---|---|---|
| Component author | what the code consumes | the IaC/module source |
| Platform engineer | what exists and how it composes | workspace, resources |
| Operator / SRE | values, and who may see them | environment, deployment |

### Two independent input channels

A consumer (provisioner, module, etc.) receives values through two channels that
should not be conflated:

| Channel | Contents | Source |
|---|---|---|
| Structural | the platform's own composed model (resources, topologies, modules, …) | the platform artifact |
| Environmental | `variables`, `features`, and `secrets` | the environment |

`references` (Requirement/Injection/Grant) only ever governs the **environmental**
channel. Keeping that boundary explicit avoids scope creep of `references` into
structural composition.

### V2 decision: Requirement (`spec.references`) will not exist as a field

Working through Requirement on its own (separately from the other five
concepts, deliberately, since they're easy to conflate) and asking the
practical question — *does a document actually need to hand-declare this,
given what already exists elsewhere?* — concluded no, for both of its v1 jobs:

**Job 1 (provisioner scoping) is fully derivable, not something to hand-author.**
When a provisioner's declared inputs are introspectable (Terraform's
`variables.tf`, or any IaC tool with an equivalent declared-input file):

```
Injection = Interface ∩ Environment
```

— give the provisioner exactly what it declares it consumes (Interface),
filtered to what actually exists (Environment). That alone resolves v1's
`dispatcher_api` case (22 keys force-injected, 2 declared) with **no manifest
required** — the platform author isn't asked to also maintain a list that just
repeats what `variables.tf` already says. A `references` list here is a third
restatement of a fact two other places already know, and — per v1's own
"add a dummy unused Terraform variable to satisfy the validator" workaround
(ADR-0078) — a restatement that visibly drifts out of sync in practice.

**Job 2 (typo-catching for Value bindings) is a weaker check than the
alternative, not a necessary one.** v1's build-time check validates a Value
binding (`var: db_password`) against the document's *own* `spec.references`
list — i.e., internal self-consistency. It does not check against the thing
that actually matters: does `db_password` exist in the real `Environment`? If
an author makes the same typo in both places, Job 2 passes while the key still
doesn't exist. The stronger, more useful check is a Phase 2 validation (the
same `_validate_dynamic()` pattern already used by `ProviderService`/
`ResourceService` to cross-check `type`/`region` against `ConfigurationModel`)
that cross-references every Value binding directly against `Environment`'s
real declared keys. No `references` list is needed to get there — and the
result catches an actual nonexistent key, not just two spots in one file
disagreeing with each other.

**What's left** is only the case of a provisioner with no introspectable
Interface at all (a raw script, an opaque external call) — there, *some*
declaration of needed keys is unavoidable, since nothing else knows what it
consumes. That's a narrow, provisioner-specific problem, not a reason to add a
`references` block to the schema of every kind. If/when it's needed, it should
be a small, scoped construct attached to that specific provisioner/script
declaration — not ported back as a general-purpose field (see Remaining Work).

**Conclusion: `spec.references`/`XReferencesModel` (e.g. `ProviderReferencesModel`,
`ResourceReferencesModel`) will not be part of v2's kind models.** This
supersedes the earlier "decide Job 1 vs Job 2 per kind" guidance for
Requirement specifically — there's no per-kind decision left to make, because
the field itself isn't being built. (Value, Interface, Injection, Grant, and
Translation are unaffected by this — they remain as documented above; only
Requirement-as-a-schema-field is rejected.)

### V2 decision: Interface is load-bearing, not optional

Requirement turned out to be a hand-authored duplicate of information
available elsewhere, and was rejected. Interface is the opposite case: it is
the **one piece of derived, ground-truth information** about what a specific
provisioner instance actually accepts, and it's exactly what makes
`Injection = Interface ∩ Environment` (adopted above) possible without asking
a human to hand-maintain anything. Drop Interface, and there is no way to
scope injection at all — the fallback is v1's actual default, "inject
everything," which is the failure mode this whole ADR exists to avoid. **v2
wants Interface — it is necessary, not optional.**

**The concrete v1 bug to not repeat structurally.** v1 already does the hard
part: `parse_variables_tf()` globs every root-level `*.tf` with a real HCL
parser and extracts full variable metadata (name, type, default, sensitive,
nullable) per provisioner, at build time. But the result is **discarded**:
`_validate_inputs()` computes Interface and uses it only to reject ("this key
isn't declared, fail the build"), while `_save_terraform_vars()` — a
*separate* loop over the same provisioners — writes the actual `.tfvars`
files without ever consulting it. Two loops, one computes Interface, the
other emits values, and they never talk to each other — the literal root
cause of the over-injection bug (`dispatcher_api` gets 22 keys despite
declaring 2). **Whatever builds the injected tfvars/env in v2 must be the
same code path that computed Interface** — not a second pass that re-derives
inputs from scratch.

**Confirmed: Interface is fundamentally a build-time concept, not a
schema-time one.** v1 parses "the copied source" — the provisioner's actual
code must be fetched/checked out first (relevant for pinned, cross-repo,
team-owned modules). Interface cannot be computed by validating a standalone
YAML file in isolation; it only exists once the build pipeline has resolved
the real source. Unlike `ConfigurationModel` cross-checks (available as soon
as the configuration file is loaded), Interface-based scoping cannot run at
simple schema-validation time — it, and therefore the capability lookup below,
belong to the build layer, not `strata validate`.

### Build concept: provisioner capability decides which formula runs

Provisioner-type coverage (v1 only parses Terraform — 7 of 8 `ProvisionerType`s
get zero introspection) and the safe-default question (what happens when
Interface is unknowable) turn out to be **the same decision**, resolved by one
mechanism: a **capability lookup**, evaluated at build time, per provisioner
instance — not a user-declared YAML field, not a per-kind schema concept. It's
a small platform-defined table (same style as the existing `SCRIPT_EXTENSIONS`
constant), because it's a fact about the provisioner type, not something an
author configures:

| Provisioner type | Interface capability | Basis |
|---|---|---|
| `terraform` | **Always capable** | Real HCL parser, deterministic, proven in v1 |
| `helm` | **Conditionally capable** | Only if the specific chart ships `values.schema.json` — checked per-instance at build time, not per-type |
| `ansible`, `bicep`, `compose`, `script`, `argocd`, `flux` | **Never capable** | No parser exists for any of these today |

The capability determines which formula runs, per provisioner instance, at
build time:

```
if capability == always or (capability == conditional and schema file found):
    Injection = Interface ∩ Environment      # derived — no manifest needed
else:
    Injection = needs ∩ Environment          # deny-by-default — explicit opt-in required
```

`needs:` (a small, provisioner-scoped, opt-in list — e.g. `needs: [slack_webhook_url]`
on a `script` provisioner) is **not** a return of Requirement. It only exists
as the fallback for the capability-check-fails branch, and only ever lists
what that one provisioner instance needs — never a document-wide manifest.
This gives one clean rule instead of a type-by-type special case: capability
decides which formula runs; there is exactly one non-derived escape hatch,
used only when capability says no.

### V2 decision: Injection

Most of Injection's design fell out of resolving Interface — `Injection =
Interface ∩ Environment` (or `needs ∩ Environment` on the capability-fails
branch). Four things specific to Injection itself, confirmed in discussion:

1. **Injection is a third composition mode — intersection — distinct from
   the union (Requirement) and restriction (Grant) already named above.** It
   doesn't compose multiple components' requirements; it reconciles one
   provisioner's want (Interface) against what exists (Environment).
2. **Injection governs the Environmental channel only.** The Structural
   channel (the platform's own composed model — resources, topologies,
   modules) is a separate, already-partially-modeled mechanism in v1
   (`OutputProfileModel.emits[]`) and stays separate in v2 — it must not grow
   into the same concept as Injection.
3. **A required-but-missing Interface variable must be a hard build error.**
   If `Interface` declares a variable with no default (required) and it's
   absent from `Environment`, the intersection silently excludes it — which
   means the provisioner gets nothing for something it can't run without.
   Left unchecked this fails deep inside the provisioner (an interactive
   prompt, or an unhelpful apply-time error) instead of clearly at build time.
   Injection's computation must check this itself: every required Interface
   key must appear in the intersection, or the build fails immediately with
   a message naming the missing key and the provisioner.
4. **Grant layers on top of Injection; it does not replace it.** Injection is
   computed once, per provisioner, at build time. Grant (below) is evaluated
   per invocation, at deploy time, and can only narrow what Injection already
   established — `delivered = Injection` filtered by `Grant`, never the
   reverse.

**Worked example** (`variables.tf` declares `region` with no default,
`instance_size` with a default, `db_password` with no default+sensitive,
`enable_monitoring` with a default; `environment.yaml` declares
`enable_monitoring` and `db_password` but forgets `region`):

```
Injection = Interface ∩ Environment = { enable_monitoring: true, db_password: <resolved> }
```

`instance_size` is absent from both Interface's required set and Environment —
fine, Terraform uses its own default. `region` is required by Interface but
absent from the intersection — **not** fine:

```
Build failed: provisioner 'web_infra' requires variable 'region'
(variables.tf has no default) but 'region' is not declared in
Environment 'prod'.
```

### V2 decision: Grant

Unlike Requirement, Grant is kept — it answers a genuinely distinct,
invocation-scoped question that neither Interface nor Environment can answer:
*given who/what is invoking the provisioner right now, should the real
resolved value actually be exposed this time?*

**Home: `stages[]`, not the provisioner/topology declaration.** Injection-
related things (Interface capability, `needs:`, Translation) are
provisioner-scoped — fixed regardless of who invokes the provisioner. Grant
is invocation-scoped: the same provisioner, invoked by `plan` vs `apply` vs
`destroy`, legitimately needs different visibility. `stages[]` is the list of
invocations, so it's the only place that can express that difference without
duplicating the provisioner declaration per stage.

**Derived-by-default, not mandatory hand-authoring.** The same pattern used
to resolve Interface/Injection applies here: a `plan` stage doesn't need real
secret *values* (it's diffing state); `apply`/`destroy` do. That's derivable
from the stage's own kind, not something every stage author should declare
from scratch:

```
Grant = deny-all-secrets      if stage.kind == plan
Grant = allow-all-injected    if stage.kind in (apply, destroy)
```

Hand-authoring is reserved for the genuine exception (e.g. excluding one
unusually sensitive "break-glass" secret from an otherwise-normal `apply`
stage, or the rare `plan` stage that legitimately needs one secret the
default would withhold) — an allow/deny override, not a mandatory allowlist.
This also avoids v1's non-monotonic trap (a stage declaring nothing silently
narrowed to zero secrets, while no stage at all left it at everything) —
there's no "declared vs not declared" ambiguity when the default is derived.

**Confirmed secrets-only — not an oversight to fix, a deliberate scope.**
Variables/features have no confidentiality reason to restrict (`region:
eu-west-1` isn't sensitive), and restricting them would actively break
correctness: `terraform plan` needs a value for *every* declared variable —
sensitive or not — to compute its plan graph at all; it cannot skip a
required variable just because a stage would rather withhold it. The
distinction Grant draws ("real value vs. safe placeholder") only has meaning
for confidential data. Variables/features are fully governed by Injection
alone — there is no separate Grant question for them.

**Worked example** (continuing the `web_infra` case, `Injection =
{enable_monitoring: true, db_password: <resolved>}`):

| Stage | kind | Derived Grant | `db_password` flows? |
|---|---|---|---|
| `validate` | plan | deny-all-secrets | No |
| `rollout` | apply | allow-all-injected | Yes |
| `teardown` | destroy | allow-all-injected | Yes |
| `emergency-patch` (override: `grant.deny: [db_password]`) | apply | allow-all-injected, minus override | No, despite kind=apply |
| `cost-estimate` (override: `grant.allow: [db_password]`) | plan | deny-all-secrets, plus override | Yes, despite kind=plan |

Exact schema (field names, how overrides attach to a stage) is intentionally
left undecided here — this section records the conceptual model
(derived-by-default, secrets-only, override for exceptions) so the schema
can be designed later without re-litigating the concept.

## Decision Outcome

Adopt these as explicit design constraints for v2, applied as each relevant kind
is built:

1. **Reject Requirement (`spec.references`) as a schema field.** Do not add
   `XReferencesModel`-style fields to any future kind. Scoping is derived
   (`Injection = Interface ∩ Environment`); typo-catching is a direct Phase 2
   check against `Environment`, not a hand-authored list. Model Injection and
   Grant as their own mechanisms when the provisioner/build and deploy/stage
   layers are designed (not yet built in v2) — never derive one from a union
   of the other, and never resurrect a `references`-shaped field to do it.
2. **Design Value bindings' correctness check as a direct Phase 2
   cross-reference against `Environment`**, using the existing
   `_validate_dynamic()` pattern (`ProviderService`/`ResourceService` already
   do this for `type`/`region` against `ConfigurationModel`) — not as an
   internal-consistency check against a document-local list.

   Concretely (`Environment`/`Dns` don't exist in v2 yet — sketching the shape):

   ```yaml
   # environment.yaml — the real, ground-truth declared keys
   kind: environment
   spec:
     variables:
       region: eu-west-1
       internal_network_cidr: "10.0.0.0/16"
     secrets:
       db_password: "@vault/db_password"
   ```

   ```yaml
   # a dns-like document using a Value binding instead of a literal
   kind: dns
   spec:
     zones:
       - name: example.com
         records:
           - name: api
             type: A
             var: internal_network_cidr   # Value binding
   ```

   Phase 1 (schema only) passes regardless — Pydantic only checks `var` is a
   non-empty string, it can't know if the key is real. Phase 2 is the actual
   check, same pattern as `ProviderService`/`ResourceService`:

   ```python
   class DnsService(BaseService[DnsModel]):
       def _validate_dynamic(self, environment_model: EnvironmentModel | None = None) -> tuple[bool, list[str]]:
           if environment_model is None or self.model is None:
               return True, []

           errors: list[str] = []
           declared_vars = set(environment_model.spec.variables or {})
           declared_secrets = set(environment_model.spec.secrets or {})

           for zone in self.model.spec.zones:
               for record in zone.records:
                   if record.var and record.var not in declared_vars:
                       errors.append(
                           f"DNS record '{record.name}': var '{record.var}' not found in "
                           f"Environment. Available: {sorted(declared_vars)}"
                       )
                   if record.secret and record.secret not in declared_secrets:
                       errors.append(f"DNS record '{record.name}': secret '{record.secret}' not found in Environment.")

           return len(errors) == 0, errors
   ```

   A typo (`var: internal_netwrok_cidr`) is caught immediately against the
   *real* environment — `["DNS record 'api': var 'internal_netwrok_cidr' not
   found in Environment. Available: ['internal_network_cidr', 'region']"]` —
   with no `spec.references` list involved anywhere.
3. **Preserve the Requirement/Authority/Channel distinctions** as terminology
   when writing future ADRs or docstrings for resource/module/network/provisioner
   models, so the "who declares this and why" question has a standard vocabulary
   instead of re-deriving it per kind.
4. **Never call a Value binding a "requirement" in prose or docstrings**, even
   though both deal in variable/secret/feature key names — "requirement" now
   only describes the informal, derived notion of "what a provisioner needs"
   (Interface ∩ Environment, not a schema field); "Value binding" (or "Value")
   is the term for a specific field's `var:`/`secret:`/`feature:`. This ADR
   exists because the two are easy to conflate; the vocabulary is the guard
   rail even without a `references` field to anchor it to.
5. **Unify all Value bindings on one syntax: embedded `${var:KEY}`/
   `${secret:KEY}`/`${feature:KEY}` tokens (v1 ADR-0075), not
   `ValueSourceModel`'s discriminated union.** Every Value-binding field
   becomes a plain `str` that may contain zero or more of these tokens
   (a literal is just a string with none). Handles both the single-reference
   case and the composite/concatenation case (e.g. connection strings) with
   one mechanism — do not build `ValueSourceModel` at all. Extend to
   `${output:KEY}` as a fourth token if/when `output_key` is generalized
   (still deferred — see below). Implement the shared resolver/routing logic
   (partial regex substitution; a leaf containing any `${secret:...}` token
   is secret-shaped) when the first kind that needs it (`dns`, `network`, or
   `module`) is designed — not before.
6. **Model Translation as its own mechanism, scoped to the provisioner/topology
   boundary that wires a canonical key to a specific external module** — never
   fold it into the kind models (`Resource`/`Provider`/etc. must stay portable
   across provisioners) and never treat it as a Value binding or a rename of
   the canonical Requirement. Design it when the provisioner/topology-wiring
   layer is built, not before. **Apply Translation before computing
   `Interface ∩ Environment`**, not after — an untranslated name mismatch
   would otherwise incorrectly trigger Injection's required-but-missing
   hard-fail (point 8) for a value that is genuinely satisfiable.
7. **Treat Interface as necessary infrastructure, not an optional nicety.**
   Whatever computes Interface (parses a provisioner's declared inputs) must
   be the same code path that shapes what actually gets injected — never a
   separate pass that re-derives inputs from scratch (the literal v1 bug).
   Decide Injection's formula via a **provisioner capability lookup**
   (`always`/`conditional`/`never`, a small platform-defined table per
   `ProvisionerType` — not a schema field): capable instances get
   `Injection = Interface ∩ Environment`; incapable ones get
   `Injection = needs ∩ Environment`, where `needs:` is a narrow, opt-in list
   scoped to that one provisioner instance, not a document-wide manifest.
8. **Injection's computation must hard-fail on a required-but-missing
   Interface variable** (declared with no default, absent from `Environment`)
   — never silently omit it and let the provisioner fail unhelpfully deep
   inside its own run.
9. **Keep Grant, scoped to `stages[]`, secrets-only, derived-by-default from
   stage kind** (`plan` → deny secrets, `apply`/`destroy` → allow secrets),
   with a narrow allow/deny override reserved for genuine exceptions — never a
   mandatory hand-authored allowlist, and never extended to variables/features
   (no confidentiality need, and `terraform plan` requires every declared
   variable's value regardless of sensitivity, so restricting them would
   break correctness, not just be unnecessary). Exact schema (field names,
   how an override attaches to a stage) is deferred to when the deploy/stage
   layer is actually designed.

## Remaining Work

- **Remove `ProviderReferencesModel`/`ResourceReferencesModel` and the
  `references` field from `ProviderModel`/`ResourceModel`** (a follow-up code
  change, not done as part of writing this ADR) — both are now dead fields per
  the decision above, the same class of problem as v1's dead `custom`/
  `env_vars`. Update ADR-0003/0004 accordingly once removed.
- When designing `resource`, `network`, `module`, or `dns` kinds: do **not**
  add a `references`-style field. Value bindings validate directly against
  `Environment` (Phase 2); provisioner scoping is derived from Interface, not
  declared.
- When designing the first kind with a Value binding (most likely `dns`):
  port v1's `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` resolver (partial
  regex substitution + secret-shaped-leaf routing, ADR-0075) into
  `common_models.py`/the service layer, instead of `ValueSourceModel`'s
  discriminated union (superseded) and instead of copying v1's per-kind
  hand-written validators. Decide the shared resolver's exact home
  (`common_models.py` helper vs. a service-layer utility) at that point.
  `output_key` generalization to `${output:KEY}` remains deferred until a
  second kind needs it (deliberately, not by oversight).
- When designing the provisioner/build layer: implement Interface parsing as
  one code path shared by both the validation check and the actual
  tfvars/env-emission step (not two separate passes, per the v1 bug found
  above). Build the provisioner capability lookup (`always`/`conditional`/
  `never` per `ProvisionerType`; `conditional` types like `helm` check
  per-instance at build time for a schema file) and the `needs:` opt-in
  fallback before defining Injection and Grant as their own
  restriction-composed mechanisms.
- When designing the deploy/stage layer: implement Grant as a
  derived-by-default mechanism keyed on `stage.kind` (`plan` → deny secrets,
  `apply`/`destroy` → allow secrets), with a narrow allow/deny override for
  exceptions — not a mandatory per-stage allowlist, and not extended to
  variables/features (confirmed secrets-only). Exact schema (field names,
  where the override attaches) still needs to be designed.
- When designing the provisioner/topology-wiring layer: design Translation
  (per-provisioner canonical-key -> local-name alias table) as a first-class
  mechanism from the start — this is the one concept v1 never modeled at all,
  confirmed as a real gap by the `region`/`location` (`dispatcher_api`-style)
  case, not a hypothetical one.
