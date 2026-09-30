# Value Token Resolution — Design

- Status: implemented — all 7 phases of the Full Solution plan are done
  (see "Full Solution" below); the separate, lower-priority bare-`${KEY}`-
  token (no `kind:` prefix) lint hint remains open, tracked on its own; the
  5th kind, `${value:kind.name.path}`, designed and **fully implemented,
  all 7 of its own phases, in
  [cross-document-value-references.md](cross-document-value-references.md)**
  (`"value"` is a recognized `VALUE_TOKEN_KINDS` entry;
  `resolve_document_value_references()` (`value_references.py`) resolves a
  real `${value:kind.name.path}` token against the loaded `DocumentIndex`,
  merges into `ValueResolution.values` for deploy-time delivery, and backs
  a `strata validate` check (`semantic_checks.py`'s
  `_check_value_references()`) so both share one implementation — live-
  migrated into `.v2-cfg`'s real `environments/c0062-env.yaml` as proof);
  **a real resolution-reach gap found 2026-09-29** (Provider's
  `configuration`/`custom` never projected into any Terraform artifact at
  all, despite passing validation) motivated a new requirement —
  resolution must reach anywhere in `root.spec.*`, not a curated category
  list — see "Decision (2026-09-29)" below. **Both halves of this fix are
  now implemented**: Phase 0 (the `InfraIntegration.__subclasses__()`
  guardrail, preventing the same regression on the *tool* axis) and
  Phase 1 (the actual Terraform-delivery genericization, closing the
  confirmed Provider gap — `docs/design/gap_fit_v1.md` gap #17).
  **Phased implementation plan for both this fix and the `value:` kind
  lives in
  [cross-document-value-references.md](cross-document-value-references.md)'s
  own "Implementation Plan" section — all 7 phases done** — not
  duplicated here.
- Last updated: 2026-09-29 (Phase 6 — documentation and a full
  implementation review, including a live `.v2-cfg` migration proof —
  implemented and verified; the `value:` kind design is now fully done)
  primitive — implemented and verified)
  implemented and verified)

## Overview

The unified `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` embedded-token
mechanism, adopted in [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
to replace v1's per-kind `value`/`var`/`secret` discriminated unions
(`ValueSourceModel`, superseded). This doc tracks the mechanism's current
state across kinds so the same "Environment cross-check deferred" note
doesn't need repeating in every kind's ADR.

## Value Supply Mechanisms — three distinct ways, not one (2026-09-25)

Found while investigating `output.template` (ADR-0023 D3) as a candidate
`build run` feature: strata has **three** genuinely different mechanisms
for getting a value into generated output, easy to conflate since all
three ultimately draw from the same resolved variables/secrets/features.
Keeping them distinct, not merging them, is the point of this section.

**A. Structured input files/env vars** — `.auto.tfvars.json`, Helm
`values.yaml`, Compose environment vars; `TF_VAR_*`/`--set-string` for
secrets. The **preferred** mechanism (ADR-0025's own reasoning for why
strata never rewrites a synced source file in place): tool-native, the
consuming tool (Terraform/Helm/Compose) interprets it itself, zero custom
resolution code needed. Build-time-safe half (`constant`/`environment`
values) is built (docs/design/build-time-value-categories.md); the
deploy-time half (secrets/integration-backed) is not, blocked on
`deploy run`.

**B. Embedded string tokens** — `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`,
this doc's own subject. Use this **inside a field strata's own schema
defines** (`DnsRecordModel.value`, `SubnetModel.cidr`,
`FirewallRuleModel.from_`/`.to`, `ModuleServiceEnvironmentModel.value`,
`ProvisionerBackendModel.configuration`) — a partial substitution within
one field's own string value, for values that need to live inside a
document *strata itself owns*, not a third-party tool's native config
(that's what (A) is for). Always deploy-time (confirmed directly in v1:
`resolve_expr_string()`/`EXPR_PATTERN` is used exclusively by
`TerraformDeployer`/`HelmDeployer`, never any build-time builder). A 4th
kind, `${output:step.key}` (a prior step's collected outputs, dependency-
scoped), is being added for `deploy run` specifically —
[deploy-command.md](deploy-command.md)'s "Cross-step output context"
section. **A 5th kind, `${value:kind.name.path}` (a cross-document
reference, not tied to a specific `deploy run` step) was added later**,
fully implemented as of 2026-09-29 —
[cross-document-value-references.md](cross-document-value-references.md).

**C. Jinja2 full-file templates** — `output.template` (ADR-0023 D3). Use
this when **generating/templating an entire file** strata's own default
projection doesn't produce — a different problem from (B): whole-file
generation, not one field's value. Build-time can only validate such a
template (statically check its referenced names exist somewhere in the
declared schema, via `jinja2.meta.find_undeclared_variables()` — no
rendering); the actual render is deploy-time only, same reason as (B) —
full values (including secret-shaped-leaf awareness: never write a secret
to disk) aren't available until then.

**The guiding rule, going forward: strata's own document fields use
embedded tokens (B); generating or templating a whole file uses Jinja2
(C).** Don't reach for Jinja2 to fill in one field of an existing
strata-owned document, and don't reach for embedded tokens to generate an
entire new file from scratch. (B) and (C) are not redundant with each
other and should not be merged into one mechanism — but they share the
same underlying need (fully-resolved values, secret-shaped-leaf safety),
so when the deploy layer is designed, both should be built on top of one
shared resolution primitive (matching v1's real `ResolvedValues`), not as
two independent re-implementations of "resolve a value, refuse to write a
secret."

## Current Design

- **Syntax**: `${kind:KEY}` where `kind` is `var`, `secret`, or `feature`
  (also `output`, [Context](provisioning-injection-model.md)/[deploy-command.md](deploy-command.md)'s
  own dedicated design; also `value` — [cross-document-value-references.md](cross-document-value-references.md),
  a solution-wide `${value:kind.name.path.to.field}` reference into another
  already-loaded document's own literal field, not an Environment-declared
  key at all — fully implemented as of 2026-09-29, Phases 0-5 of that
  doc's own Implementation Plan: syntax recognition, resolution, deploy-time
  delivery, and a dedicated `strata validate` check).
  Embeddable anywhere inside a plain `str` field — a literal is just a
  string with no tokens in it.
- `validate_value_tokens()` (`common_models.py`) — Phase 1 syntax check only
  (rejects unknown kind, missing key, missing colon). Cannot check whether
  `KEY` actually exists anywhere — that's Phase 2.
- `has_value_tokens()` — lets a kind skip literal-format checks (e.g. CIDR
  syntax) when a token is present, since the value isn't known until
  resolution.
- The actual **resolver/router** (partial regex substitution across a whole
  leaf; a leaf containing any `${secret:...}` token is secret-shaped and
  routed to a deploy-time-only channel) is designed (ADR-0002 Decision 5)
  but **not implemented** — this is separate from the Phase 1 syntax
  validators above.

### Real-world evidence this is genuinely needed, not just theoretical (2026-09-25)

Checked haven and cfg-deployment directly rather than assuming. haven
has no `kind: dns`/`kind: network` documents at all. cfg-deployment's
only real ones (`stacks/spoke/dns.yaml`/`network.yaml`) use **zero**
`${var:}`/`${secret:}`/`${feature:}` tokens today — every value is a literal
placeholder (`"10.0.0.0/22"`, `"TODO-agw-public-fqdn.placeholder.azure.com"`).
But the file's own comments make clear this is a workaround, not a lack of
need: *"The actual values the deploy reads still live in
`deploy/hubs/z01/s01/environment.yaml`'s `variables`
(`ring_subnet_cidrs`, `apim_subnet_cidr`)... keep this file's CIDRs in sync
with that environment file by hand; nothing wires the two together
automatically."* Someone wants `cidr: "${var:ring_subnet_cidrs}"` but can't
use it — nothing would ever resolve it, so it would just be written
literally into `networks.auto.tfvars.json`, useless to Terraform. Zero
usage reflects zero *capability*, not zero *want*.

(Separately, `dns.yaml`'s own comment flags that `DnsRecordModel.name` has
no Value-token union at all — only `.value` does — so even a working
resolver wouldn't let a shared DNS template parameterize a record's name
per customer. A real, larger gap, but a schema change, not a resolution
change — out of scope here, logged for later.)

### Decision: one resolver for every kind, built at deploy time — not a per-kind build-time band-aid (2026-09-25)

A resolver that only covers `dns`/`network` while `firewall`/`module` (the
other two kinds in the Per-Kind Status table below, same Value-token
mechanism) stay unresolved would be inconsistent — a system like this works
for every kind that has Value-token fields, or it doesn't earn calling
itself "the" resolver. **Do not build a `build run`-scoped, `dns`/`network`-only
fix.** Confirmed directly in v1's real `terraform_builder.py`: `_build_dns_vars()`/
`_build_network_vars()` never call the resolver at all — v1's own real
`resolve_expr_string()`/`EXPR_PATTERN` mechanism (`resolved_values.py`) is
used **only** at deploy time (`TerraformDeployer`'s `backend.configuration`,
`HelmDeployer`'s rendered `values.yaml`), with the deployer's fully-resolved
`ResolvedValues` (every store type, including secrets and integration-backed)
and a **fail-loud** rule (an unresolved reference is always an error). That's
correct there and would be wrong at build time, where only `constant`/
`environment`-backed values exist (docs/design/build-time-value-categories.md,
Q1/Q5) — a `${var:X}` backed by an integration-backed or secret store is
*expected* to stay unresolved at build time, not an error.

**Build the one shared resolver when the deploy/stage layer is designed**
(it needs deploy's own fully-resolved values anyway), applying it uniformly
to every kind's Value-token fields (`dns`, `network`, `firewall`, `module`,
and any future one) — not separately, not build-time-only. Tracked as
blocked on the same prerequisite `docs/design/provisioning-injection-model.md`
already names for Context/deploy-time work generally.

## Per-Kind Status

**A `value:`-kind row is deliberately not added to the table below** —
see [cross-document-value-references.md](cross-document-value-references.md)
(fully implemented through Phase 5, 2026-09-29 — syntax recognition, the
resolution primitive, wiring into `resolve_values()`, and the `strata
validate` check; only Phase 6, documentation/final verification, remains):
unlike the four kinds this table tracks, `${value:kind.name.path}` is not
scoped to specific per-kind model fields (`DnsRecordModel.value`, etc.) at
all — it's valid in any string field any kind already walks generically,
addressed by `(kind, name)` document identity instead of a model field. A
per-kind row would misrepresent it as narrower than it is.

**Reflects final state (2026-09-28, after Full Solution Phases 0-7) — the
original 2026-09-25 version of this table showed Resource/Provider/
Namespace/Topology/Workspace as having no Value-token coverage at all and
every kind's "Phase 2 Environment cross-check" as permanently deferred;
both were only true until the phases below shipped.** "Phase 1 syntax
check" is "Done" for every row either via a dedicated `field_validator`
(DNS/Network/Firewall/Module, pre-dating this doc, unchanged) or via the
generic whole-document walk (`unresolved_value_tokens()`, Phase 1 of the
Full Solution) for every other kind's `configuration`/`custom`/
`properties` passthrough field — complementary mechanisms, not competing
(the four dedicated validators were never replaced). "Phase 2 Environment
cross-check" is "Done" for every row via `semantic_checks.py`'s
`_check_deployment_value_tokens()` (`_documents_reachable_from_workspace()`'s
walk, extended in Phase 7) — not the `_validate_dynamic(environment_model=...)`
per-service-class mechanism this table originally sketched, which was
never built; the same outcome (every reachable document's tokens checked
against the deployment's resolved environment) is achieved a different way.

| Kind        | Value-token field(s)                                                                                          | Phase 1 syntax check                                                    | Phase 2 Environment cross-check |
| ----------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------- |
| Provider    | `ProviderSpecModel.configuration` (Phase 6/7)                                                                 | Done (generic walk)                                                     | Done (Phase 7)                  |
| Resource    | `ResourceSpecModel.configuration`/`.custom`, `WorkspaceResourceModel.configuration` (Phase 6/7)               | Done (generic walk)                                                     | Done (Phase 7)                  |
| DNS         | `DnsRecordModel.value`                                                                                        | Done ([ADR-0005](../decisions/0005-dns-model-design-decisions.md))      | Done (Phase 2)                  |
| Network     | `SubnetModel.cidr`, `NetworkDefinitionModel.address_space` entries                                            | Done ([ADR-0007](../decisions/0007-network-model-design-decisions.md))  | Done (Phase 2)                  |
| Firewall    | `FirewallRuleModel.from_`/`.to`                                                                               | Done ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)) | Done (Phase 2)                  |
| Module      | `ModuleServiceEnvironmentModel.value`, `ModuleSpecModel.configuration` (via Phase 4/5's generic payload walk) | Done ([ADR-0009](../decisions/0009-module-model-design-decisions.md))   | Done (Phase 4/5)                |
| Namespace   | none identified                                                                                               | n/a                                                                     | n/a                             |
| Topology    | `TopologyVolumeModel.configuration` (Phase 6/7)                                                               | Done (generic walk)                                                     | Done (Phase 7)                  |
| Workspace   | `WorkspaceSpecModel.configuration`/`.custom`/`.properties` (Phase 6/7)                                        | Done (generic walk)                                                     | Done (Phase 7)                  |
| Environment | `EnvironmentSpecModel.properties`/`.custom` (Phase 7)                                                         | Done (generic walk)                                                     | Done (Phase 7)                  |
| Deployment  | `DeploymentSpecModel.properties`/`.custom` (Phase 7)                                                          | Done (generic walk)                                                     | Done (Phase 7)                  |
| Tenant      | `TenantSpecModel.configuration` (Phase 6/7)                                                                   | Done (generic walk)                                                     | Done (Phase 7)                  |

## Decision: unify `docs/design/gap_fit_v1.md` gaps #8/#9/#10 into one deploy-time resolver, general-scope, per-integration delivery (2026-09-28)

Triggered by reviewing the `.v2-haven` coverage-check migration (`docs/design/gap_fit_v1.md`
gaps #8-#10): a schema-correct `${secret:KEY}` in `ModuleServiceEnvironmentModel.value`
still doesn't reach a deployed container today, because the resolver this doc
already decided to build (2026-09-25, above) was only ever wired up for one
field (`provisioner.backend.configuration`). This section is the concrete
design for closing that gap — **design only, not yet implemented.**

### The walking primitive already exists and is already field-agnostic

`resolve_value_tokens_in_mapping()` (`strata/utils/value_tokens.py`) recurses
into every nested `dict`/`list` and resolves every string leaf — it does not
know or care whether it's walking `provisioner.backend.configuration`, a
`DnsRecordModel`, or a module's `configuration:` passthrough dict. **Closing
gap #8 (`configuration`/`custom` dicts) is therefore not a new algorithm —
it's new call sites for a function that already exists.** The "not
token-aware" framing in this doc's own Per-Kind Status table (and in
`ResourceSpecModel`'s docstring) was accurate as a description of *today's
call sites*, not a permanent architectural limit — there is no technical
reason `configuration`/`custom` can't be walked the same way once a call
site exists for the document that owns them.

### Per-integration delivery — three different mechanisms, matching how each tool actually accepts input

The walk is one shared step; *where the resolved value ends up* differs per
integration, because the three tools accept input differently (confirmed
directly, not assumed — see `docs/design/gap_fit_v1.md` gap #9's own investigation):

1. **Terraform — `dns`/`networks`/`firewalls` payloads.** `terraform_projection.py`
   already writes these as top-level Terraform variables (JSON, via
   `build run`), tokens included, unresolved. At deploy time: run
   `resolve_value_tokens_in_mapping()` over the already-written payload using
   the step's fully-resolved values (same call already made for
   `provisioner.backend.configuration`), then pass the **whole resolved
   payload** as `TF_VAR_dns=<json>` / `TF_VAR_networks=<json>` /
   `TF_VAR_firewalls=<json>` — Terraform natively accepts a JSON-encoded env
   var for a complex (object/list)-typed variable, and an env-var-sourced
   value overrides the same-named `-var-file` entry. The on-disk
   `.auto.tfvars.json` this was originally written to is **never rewritten**
   and never carries a resolved secret — same "never touches disk"
   guarantee `backend.configuration` already has, extended to three more
   variable names instead of invented fresh.
2. **Helm — module `services[].environment[]` and (once gap #8 lands)
   `configuration`/`custom`.** Non-secret (`var`/`feature`) tokens: safe to
   rewrite directly into the already-written `values.yaml` — it's `build
   run`'s own output, not a synced source file (ADR-0025's "never rewrite a
   synced source" rule doesn't apply). Secret-shaped tokens: never written
   to that file — instead, track the *dotted path* of the leaf while
   walking (e.g. `controllers.main.containers.main.env.DB_PASSWORD` for
   `immich.yaml`'s real example) and pass `--set-string
   <path>=<value>` per secret found, on the `helm upgrade` invocation
   itself (`helm.py`'s own docstring already names this as the intended
   mechanism — this design just supplies the missing path-tracking to make
   it buildable).
3. **Compose — module `services[].environment[]` and (once gap #8 lands)
   `configuration`/`custom`.** Non-secret tokens: same direct-rewrite
   reasoning as Helm. Secret-shaped tokens: rewrite the token *in the file*
   to Compose's own native bare `${KEY}` placeholder (a rename, not a
   value — never written to disk), then pass `KEY=<value>` via the `env:`
   kwarg `ComposeIntegration.deploy()`/`.plan()` already accept, exactly
   like Terraform's `TF_VAR_` mechanism.

### What counts as "secret-shaped"

Reuse the rule this doc's Current Design section already states, don't
invent a new one: *a leaf containing any `${secret:...}` token is
secret-shaped.* Applies per-**string**, not per-token — a single interpolated
string mixing a `${var:}` and a `${secret:}` token (e.g. a connection
string) is secret-shaped as a whole and must go through the CLI-arg/env-var
path in full, never partially rewritten into a file. No real example found
yet of a non-scalar secret leaf (every real secret-shaped value checked —
haven, cfg-int-deployment — is a plain scalar string), so a non-scalar
`--set-string` target is out of scope until evidenced.

### This also substantially resolves gap #10

Once the resolver covers every string in a document (not a fixed
field-by-field allowlist), `unresolved_value_tokens()`'s "false green light"
failure mode (a well-formed, declared token sitting in a field that will
never actually be substituted) stops being possible — every declared,
well-formed token now *does* eventually resolve, regardless of which field
it's in. The one failure mode this doesn't fix: a **bare** `${VARNAME}`
(no `kind:` prefix) is still invisible to `VALUE_TOKEN_PATTERN` and gets
zero validation signal — a separate, smaller, still-open problem (a future
lint could flag a `${[A-Z_]+}`-shaped string that isn't a well-formed token
as a "did you mean `${secret:...}`?" hint), not blocking closure of #8/#9.

### Phased implementation plan (design only — none of this is built yet)

1. Terraform: `dns`/`networks`/`firewalls` payload resolution via
   `TF_VAR_<name>=<json>`, reusing `resolve_value_tokens_in_mapping()` as-is.
2. A path-tracking sibling to `resolve_value_tokens_in_mapping()` (or an
   extension of it) that additionally reports `{dotted_path: value}` for
   every secret-shaped leaf, separate from the plain resolved-dict return
   for non-secret leaves.
3. Helm: rewrite `values.yaml` for non-secret leaves; `--set-string` for
   secret-shaped leaves from step 2's path map.
4. Compose: rewrite the compose file for non-secret leaves; bare `${KEY}`
   rename + `env:` kwarg for secret-shaped leaves.
5. Extend every call site above to also walk `configuration`/`custom` —
   closes gap #8 as a consequence of building #9 generally, not as a
   separate feature.
6. Revisit gap #10 last: if 1-5 land completely, the false-green-light
   failure mode is gone by construction and no code change is needed there;
   only the bare-token lint hint (above) would remain open, as its own
   smaller, separately-prioritized item.

## Escape syntax: `$${...}` for a literal, non-strata `${...}` (2026-09-28)

Triggered by asking whether Phase 1 (`validate_value_tokens()`, malformed-
token syntax check) could simply be generalized to every string field
(`model_validator(mode="after")` on `PlatformBaseModel`, walking every leaf
the same way Phase 2's `unresolved_value_tokens()` already does) instead of
today's fixed per-field allowlist (`dns_model.py`, `module_model.py`,
`network_model.py`/`firewall_model.py`). That generalization is desirable —
it would catch gap #8's real modules (`modules/immich.yaml` etc.'s bare
`${IMMICH_DB_PASSWORD}`) as a malformed-token error today, for free, since
`_VALUE_TOKEN_CANDIDATE_PATTERN` already matches any `${...}`-shaped string
regardless of whether it has a `kind:` prefix.

**But it can't be unconditional.** `modules/gatus.yaml` (`.v2-haven`) has a
real, already-working, explicitly-documented bare `${TOKEN}` that is
Gatus's *own* chart-native substitution mechanism — nothing to do with
strata's tokens. A generic Phase 1 would wrongly flag that as malformed and
break a document that works correctly today. Any generalization of Phase 1
(or, going forward, of Phase 2/the resolver into more passthrough fields —
see gap #8's own "extend to `configuration`/`custom`" decision) needs an
escape mechanism first, or it will keep producing exactly this kind of
false positive wherever a chart's own template syntax happens to look like
strata's.

### The syntax: doubled leading `$`

Write `$${kind:key}` (or `$${anything}` for a non-token-shaped literal like
Gatus's) to mean "this is not a strata Value token — skip both validation
phases, and emit a single `$` followed by the literal braces in the final
build/deploy output." **Not a new invented convention** — it doubles the
same escape idiom two of strata's own three integration targets already use
for their own native interpolation syntax:

- **Terraform** escapes its own `${var.x}` interpolation with `$${` — the
  exact same doubled-`$` shape.
- **Compose** escapes its own `${VAR}` interpolation with `$$`.
- **Helm** has no interpolation syntax of its own to collide with (Go
  templates use `{{ }}`, not `${ }`) — the escape there exists purely for
  cases like Gatus's, a chart's *application-level* config doing its own
  `${TOKEN}` substitution at container startup, unrelated to Helm itself.

Authors targeting Terraform/Compose already know this convention from the
underlying tool; reusing it for strata's own tokens avoids teaching a
fourth, strata-specific escape idiom.

### Where it takes effect

- **Phase 1/Phase 2 (`value_tokens.py`):** `validate_value_tokens()`,
  `has_value_tokens()`, and `extract_value_tokens()` all need to strip any
  `$${...}` occurrence from the string *before* running their own regex
  checks, via one new shared helper (e.g. `strip_escaped_value_tokens()`).
  An escaped occurrence becomes invisible to both phases — not "always
  valid," simply not a candidate at all.
- **Where unescaping actually happens (corrected — see "Validated before
  implementation" in the Full Solution section below):** inside the
  resolver itself (`resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`),
  at deploy time, uniformly for all three integrations — not a build-time
  projection-writer concern. An earlier version of this doc claimed
  build-time was sufficient and the resolver needed no escape-awareness;
  that's disproven for Terraform specifically (see below).
- **New functions needed (`value_tokens.py`):** `strip_escaped_value_tokens(value: str) -> str`
  (used internally by the three Phase 1/2 functions above) and
  `unescape_value_tokens(value: str) -> str` (used by the resolver's final
  output step, once built).

### Status

Design only — nothing implemented. Independent of whether Phase 1 actually
gets generalized (gap #8's own still-open question): this escape syntax is
a prerequisite *if* that generalization happens, but doesn't itself require
it — it's equally usable today in the four fields Phase 1 already checks,
for an author who genuinely needs a literal `${...}`-shaped string past the
existing per-field check.

## Full Solution: Unified Design and Implementation Plan (2026-09-28)

Everything above (Phase 1 generalization, the escape syntax, the gap
#8/#9/#10 resolver) was designed incrementally, one question at a time.
This section is the single coherent pipeline all of it adds up to —
authoring through deploy — plus the one implementation plan that supersedes
the narrower "Phased implementation plan" above (that plan is now Phases
3-7 below, unchanged, just renumbered into the full sequence).

### The end-to-end pipeline

1. **Author** writes any string field, anywhere in any document. Three
   valid shapes: a plain literal, a real token (`${var:KEY}`/`${secret:KEY}`/
   `${feature:KEY}`/`${output:step.key}`), or an escaped literal (`$${...}`)
   for a third-party tool's own `${...}`-shaped syntax (Gatus's case).
2. **`strata validate`**:
   - Phase 1 (once generalized): every string leaf in the whole document —
     not just today's four wired fields — is checked for malformed token
     syntax. `strip_escaped_value_tokens()` runs first, so an escaped
     literal is invisible to this check entirely, not just tolerated.
   - Phase 2 (already generalized, no change needed): every remaining
     well-formed token's key is cross-checked against the environment's
     declared keys (`unresolved_value_tokens()`), `${output:}` handled
     cleanly per gap #11's fix.
3. **`strata build run`**: writes tokens through unresolved, as today — a
   real `${kind:key}` token is left alone (build time never has resolved
   values); an escaped `$${...}` literal is **also** left alone at this
   stage (corrected — see "Validated before implementation" below: an
   earlier version of this design unescaped at build time, which is wrong
   for Terraform specifically).
4. **`strata deploy run`**: the resolver (Phases 3-7 below) walks each
   integration's payload — Terraform's freshly-built-from-model JSON for
   `dns`/`networks`/`firewalls`/`backend.configuration`, or the
   already-written Helm `values.yaml`/Compose file rewritten in place — and
   handles **both** real-token substitution and escape unescaping in the
   same pass, inside `resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`
   itself. One mechanism, used identically by all three integrations,
   instead of a build-time step that only Helm/Compose could actually
   observe.

### Validated before implementation (2026-09-28) — two corrections

Checked the plan above against real source before writing any code, per
request ("let's validate the design before we implement it"). Found two
real problems, both now fixed above:

1. **Phase 1 generalization mechanism was wrong.** `configuration`/`custom`
   is `dict[str, Any] | None` on 18 different models (`resource_model.py`,
   `module_model.py`, `dns_model.py`, `firewall_model.py`,
   `network_model.py`, `namespace_model.py`, `workspace_model.py`,
   `topology_model.py`, `provider_model.py`, and more — confirmed by grep,
   not assumed). Pydantic never recurses into an `Any`-typed dict's
   contents on its own, so a `model_validator(mode="after")` on
   `PlatformBaseModel` would still need its own manual recursive walk for
   these fields — at which point it's reimplementing `_iter_strings()`,
   which Phase 2's `unresolved_value_tokens()` already does correctly.
   **Corrected plan:** extend that existing walk to also flag malformed
   candidates (reusing `_VALUE_TOKEN_CANDIDATE_PATTERN` vs
   `VALUE_TOKEN_PATTERN`, the same two regexes `validate_value_tokens()`
   already uses) in the same pass, rather than adding a second, separate
   Pydantic-level mechanism. The four already-working `field_validator`s
   (`dns_model.py`, `module_model.py`, `network_model.py`/`firewall_model.py`)
   stay untouched — this is additive (covers what they don't), not a
   replacement.
2. **"Unescape at build time, resolver needs zero escape-awareness" is
   wrong for Terraform.** `deploy_controller.py:303` —
   `resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)`
   — proves the real resolution precedent reads straight from the
   **in-memory model field**, never from the on-disk `.auto.tfvars.json`
   (which this doc's own "Per-integration delivery" section already says is
   "never rewritten"). A build-time unescape into the Terraform projection
   writer would touch a file deploy never reads back — invisible, not just
   ineffective. Worse: `resolve_value_tokens()` itself
   (`VALUE_TOKEN_PATTERN.sub()`) is unanchored today — fed a
   `"$${var:x}"` string, it *would* match the inner `${var:x}` and
   substitute it, leaving a stray literal `$` prepended to the resolved
   value. A real bug, not hypothetical, if shipped as originally written.
   Helm/Compose are different (their deploy-time step does rewrite the
   already-written build artifact in place, so a build-time unescape would
   have worked there) — but that per-integration asymmetry means
   "build-time, resolver-agnostic" can't be a universal rule.
   **Corrected plan:** fold escape-handling directly into
   `resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`, applied
   uniformly at deploy time for all three integrations — matching how
   Terraform's own real interpolation engine treats `$${` as part of normal
   evaluation, not a separate pass. **Consequence, stated plainly:** a
   Gatus-style escape-only field currently needs zero strata involvement
   (raw passthrough already "just works"); once gap #8 wires any resolution
   into `configuration`/`custom`, that field will need `deploy run` to
   reach its correct unescaped value — build alone is no longer enough.
   This is an unavoidable consequence of extending resolution into that
   field at all, not a flaw specific to the escape mechanism.

### Full implementation plan (supersedes the narrower plan above)

0. **Escape primitives** (`value_tokens.py`): add `strip_escaped_value_tokens()`
   (used by validation) and make `resolve_value_tokens()` itself
   escape-aware — match `$${...}` first and emit the unescaped literal,
   *before* the existing unanchored `VALUE_TOKEN_PATTERN.sub()` pass runs,
   so a real token is never mistakenly matched inside an escaped span. Pure
   new/changed functions, no integration call sites changed yet — ships
   independently, fully covered by unit tests (`$${var:x}` -> literal
   `${var:x}`, untouched by real resolution; a real `${var:x}` resolves as
   today; a mixed string with both).
   **Status: DONE 2026-09-28**, narrowed on implementation —
   `strip_escaped_value_tokens()` and `find_malformed_value_tokens()`
   shipped; `resolve_value_tokens()` itself was **not** made escape-aware
   yet (no real resolution call site exists to need it before Phase 2 of
   this plan) — deferred to whichever of Phases 2-5 lands first, not
   speculatively built ahead of a caller.
1. **Extend Phase 2's existing walk to also catch malformed syntax**
   (`unresolved_value_tokens()`/`environment_service.py`): reuse
   `_VALUE_TOKEN_CANDIDATE_PATTERN` alongside `VALUE_TOKEN_PATTERN` in the
   same pass — a candidate that doesn't fullmatch is a malformed-token
   diagnostic, exactly like `validate_value_tokens()`'s own logic, just
   folded into the walk that already covers `configuration`/`custom`
   instead of a second, separate Pydantic-level mechanism. Skip anything
   `strip_escaped_value_tokens()` already removed. The four existing
   `field_validator`s stay untouched. Regression test: a Gatus-shaped
   fixture (bare `${TOKEN}`, escaped as `$${TOKEN}`) must validate clean;
   an unescaped bare `${TOKEN}` in the same fixture must now fail.
   **Status: DONE 2026-09-28.** 3 new tests added directly against
   `unresolved_value_tokens()` using `DnsZoneModel.configuration` (a real
   `dict[str, Any]` passthrough field, no field-level validator). Full
   check suite green (1152 tests). Real impact checked against
   `.v2-haven`: 44 new errors across 17 module files (`docs/design/gap_fit_v1.md`
   gap #8's own "Real impact" note has the full breakdown) — left
   deliberately unfixed pending a dedicated follow-up pass, per explicit
   request, rather than mass-editing 17 real files in the same change.
2. **Terraform**: `dns`/`networks`/`firewalls` payload resolution via
   `TF_VAR_<name>=<json>`, reusing `resolve_value_tokens_in_mapping()`
   (now escape-aware per step 0) as-is.
   **Status: DONE 2026-09-28.** New public
   `build_dns_networks_firewalls_payloads()` (`terraform_projection.py`) —
   a focused sibling to `build_platform_projection()`, not that function's
   caller plucking three keys out of a bigger payload it doesn't otherwise
   need. Wired into `deploy_controller.py`'s per-step loop: computed once
   per run (workspace-wide, not step-specific data, matching
   `build_platform_projection()`'s own dns/networks/firewalls categories),
   re-resolved per step using that step's own `tokens` dict (mirrors
   `backend.configuration`'s existing per-step pattern exactly, so a
   dependency-scoped `${output:...}` token would resolve identically if
   one is ever used here). Empty categories are skipped, matching
   `planned_files()`'s own convention. Confirmed the on-disk
   `*.auto.tfvars.json` `build run` already wrote is never touched — only
   the `TF_VAR_<category>` env var carries the resolved payload, same
   "never touches disk" guarantee `backend.configuration` already had. 1
   new test; full check suite green (1156 tests); `.v2-haven` unaffected
   (still the same known 44 errors — this phase is deploy-time only, not
   a validation concern).
3. **Path-tracking**: a sibling to `resolve_value_tokens_in_mapping()` (or
   an extension of it) that additionally reports `{dotted_path: value}` for
   every secret-shaped leaf, separate from the plain resolved-dict return
   for non-secret leaves.
   **Status: DONE 2026-09-28.** New `resolve_value_tokens_tracking_secrets()`
   (`value_tokens.py`) — deliberately a separate function, not a modified
   `resolve_value_tokens_in_mapping()`, since Terraform's use (Phase 2)
   delivers the whole payload via `TF_VAR_<name>` either way and never
   needed the split. A secret-shaped leaf's original, unresolved literal
   is left untouched in the returned dict rather than deleted or blanked
   — safe, since Helm's `--set-string`/Compose's `env:` both override
   whatever a values/compose file already has at that path. Verified
   against the real `immich.yaml` nested-path shape
   (`controllers.main.containers.main.env.DB_PASSWORD`) and a mixed
   `${var:}`+`${secret:}` connection string (secret-shaped as a whole,
   reported only in the secrets map, per this doc's own existing rule). 7
   new tests; full check suite green (1165 tests). No `deploy_controller.py`/
   Helm integration changes yet — this only adds the primitive Phase 4
   will consume.
4. **Helm**: rewrite `values.yaml` for non-secret leaves (escape-aware
   resolution unescapes any `$${...}` in the same pass); `--set-string`
   for secret-shaped leaves from step 3's path map.
   **Status: DONE 2026-09-28.** Scoped down first (checking what this
   needed to wire into found gap #13, `docs/design/gap_fit_v1.md`: no real `helm
   upgrade` invocation per module existed in `deploy_run()` at all), then
   completed the same day once gap #13 was designed and implemented.
   `resolve_module_values()` (`helm.py`) applies Phase 3's path-tracking
   to one module's rendered `values.yaml` payload — pure, disk-free,
   mirroring `_render_values()`'s own testable convention. Its caller,
   `HelmIntegration.deploy_namespace()` (gap #13), rewrites `values.yaml`
   for non-secret leaves and assembles `--set-string` argv (new
   `set_string=` kwarg on `plan()`/`.deploy()`, escaped per v1's real
   `_escape_set_value()`) for secret-shaped ones — see
   `docs/design/deploy-command.md`'s "Helm/Compose orchestration"
   section for the full orchestrator-side design/implementation.
5. **Compose**: rewrite the compose file for non-secret leaves (same
   escape-aware resolution); bare `${KEY}` rename + `env:` kwarg for
   secret-shaped leaves.
   **Status: DONE 2026-09-28.** New `resolve_value_tokens_renaming_secrets()`
   (`value_tokens.py`) — a genuinely different primitive from Phase 3's
   path-tracking, not a reuse of it: Helm's `--set-string <path>=<value>`
   overrides a whole values.yaml path in one shot, so Phase 3 treats an
   entire leaf as one secret-shaped unit; Compose has no such override
   mechanism — `docker stack deploy`/`stack config` substitute `${KEY}`
   occurrences *within* a string using the subprocess's own environment,
   so this resolves **per token**, not per leaf (a mixed string like
   `"postgres://${var:HOST}/${secret:DB_PASSWORD}"` resolves the `var`
   token to its literal value while renaming only the `secret` token to
   `${DB_PASSWORD}`). `resolve_compose_values()` (`compose.py`) is the thin,
   tool-specific wrapper (mirrors `resolve_module_values()`'s own
   placement); its caller, `ComposeIntegration.deploy_namespace()`
   (gap #13), rewrites the namespace's already-merged `docker-compose.yml`
   and delivers the renamed secrets as the `docker stack deploy` subprocess's
   own environment — no `.env` file needed (`Integration.run()`'s `env`
   kwarg already merges per-call onto `os.environ`, simpler than v1's real
   `inject_compose_env()` context-manager-mutates-`os.environ` approach).
6. **Extend every delivery call site above to also walk
   `configuration`/`custom`** — closes gap #8 as a consequence of building
   #9 generally, not as a separate feature.
   **Status: DESIGNED 2026-09-28, IMPLEMENTED 2026-09-28.** Checked every real
   `configuration`/`custom`-shaped passthrough field against what's
   actually *projected* into a rendered/deployed artifact today
   (`terraform_projection.py`, read directly, not assumed) — found the
   phase's own original scope (Helm/Compose module `configuration`) is
   **already closed**, and a materially bigger, previously-undiscovered
   gap on the Terraform side that this phase should cover instead:
   - **Already closed, no work needed:** `HelmIntegration.resolve_module_values()`/
     `ComposeIntegration.resolve_compose_values()` (Phases 4/5) both walk
     their *entire* rendered payload generically (dict/list/str recursion,
     no field-specific scoping) — and `_render_values()`/the Compose merge
     already fold `module.spec.configuration`/`service.configuration` into
     that payload before either resolver ever runs. Gap #8's own catalyst
     (`modules/immich.yaml`'s `configuration.controllers.main.containers.
     main.env.DB_PASSWORD`) resolves today as a side effect of Phase 4,
     confirmed by `test_resolve_module_values_matches_the_real_immich_style_configuration_path`
     already passing.
   - **Newly found, real gap** (`terraform_projection.py`, confirmed by
     reading every `_build_*_payload()` function directly): five more
     categories are projected into a `*.auto.tfvars.json` file at build
     time and contain a genuine free-form `dict[str, Any]` passthrough
     field, but are excluded from `build_dns_networks_firewalls_payloads()`'s
     deploy-time `TF_VAR_<name>` delivery (Phase 2) the same way dns/
     networks/firewalls were before Phase 2 fixed them:
     - `resources_by_category` — `_merge_resource_entry()`'s `configuration`
       key (`ResourceSpecModel.configuration` merged with
       `WorkspaceResourceModel.configuration`, "workspace wins").
     - `topologies` — `TopologyVolumeModel.configuration` (driver-specific
       volume config), included via `spec.volumes`' `model_dump()`.
     - `properties` (`graph.properties`) and `custom` (`graph.custom`) —
       the workspace → environment(s) → deployment merged dicts
       (docs/design/build-time-value-categories.md Q3), already computed
       once per run and reused for `output.template` rendering, but never
       token-resolved for TF_VAR_ delivery.
     - `tenant` — `TenantSpecModel.configuration` inside
       `_build_tenant_payload()`.
     Zero real `.v2-haven` document currently puts a token in any of
     these five (checked directly — every real `${var:}`/`${secret:}`
     usage today lives in `modules/*.yaml`'s `services[].environment[]`)
     — unlike gap #8's own dated catalyst, this is a **consistency** gap
     (the schema permits it, the mechanism doesn't exist), not a
     currently-broken real document, same honesty gap #9's own original
     dns/networks/firewalls discovery already modeled.
   - **`FirewallSpecModel.configuration` is explicitly NOT part of this
     gap** — checked `_build_firewalls_payload()` directly: it never reads
     `spec.configuration` at all, so nothing is written anywhere for it to
     resolve. That's a *projection-completeness* question (should this
     field ever reach Terraform?), a different, smaller, unrelated gap —
     conflating it with token resolution would over-scope this phase.
     Left untouched; flagged here only so a future reader doesn't assume
     it was missed.
   - **Decision: broadcast-only, `${output:}` is rejected outright for all
     five** — not gap #12's per-name claiming scheme. `properties`/
     `custom`/`tenant` are workspace/deployment-wide **singletons** (one
     payload, no name to put in a step's `targets`); `topologies`/
     `resources_by_category` group by topology/resource-type name, which
     *are* addressable `target_names` today, but extending per-name
     ownership to them is real, additional design surface with zero
     evidenced need (mirrors gap #12's own precedent of only building
     ownership once a real DNS `output_key` case existed) — deferred, not
     forgotten, should real evidence ever surface. `${var:}`/`${secret:}`/
     `${feature:}` resolve identically for every step regardless (same
     "unclaimed document broadcasts to every step" treatment
     dns/networks/firewalls already give an unclaimed document) — safe
     precisely because `${output:}` is banned, so there is no
     per-step-varying data any of these five could ever need.
   - **Concrete plan:** one new function,
     `build_configuration_payloads(graph) -> dict[str, Any]`
     (`terraform_projection.py`, sibling to
     `build_dns_networks_firewalls_payloads()`, not folded into it — the
     claiming/ownership semantics genuinely differ, conflating them would
     blur that distinction) returning `{tf_var_suffix: payload}` for every
     *non-empty* category among `properties`/`custom`/`tenant`/
     `topologies`/`resx_<type>` (one entry per resource type actually
     present, mirroring `planned_files()`'s own per-type file-naming
     exactly — `resx_compute`, not a combined `resources_by_category`
     key, since that's what a real root module's `variable "resx_compute"
     {}` expects). `deploy_controller.py`: compute once per run (workspace-
     wide, same reasoning `dns_networks_firewalls` already documents),
     pre-flight-reject any `${output:}` found in any of these five exactly
     like an unclaimed dns/network/firewall document today (reuses
     `_contains_output_token()` unchanged), then inside the existing
     Terraform-shaped ("else") branch only — never the container branch,
     these five are infrastructure-only concepts — resolve each via
     `resolve_value_tokens_in_mapping()` (already generic, already proven,
     zero new resolution logic needed) and set `env[f"TF_VAR_{name}"] =
     json.dumps(resolved_payload)`, identical to dns/networks/firewalls'
     own delivery loop.
   - **Two stale docstrings found and fixed while gathering this evidence**
     (`terraform_projection.py`'s `_build_dns_payload()`/
     `_build_networks_payload()`): both still said resolving their tokens
     "is Phase 3's job, not yet wired into this category (known gap)" —
     true when originally written, false since Phase 2 shipped
     `build_dns_networks_firewalls_payloads()`/`deploy_controller.py`'s
     TF_VAR_ delivery the same day. Corrected in place rather than left
     to mislead a future reader (pure comment fix, zero behaviour change).
   - **Built exactly as planned**, no deviations: `build_configuration_payloads()`
     (`terraform_projection.py`) and the matching `deploy_controller.py`
     wiring (computed once per run, pre-flight `${output:}` rejection,
     delivery inside the Terraform-shaped branch only). One existing test
     (`test_deploy_run_injects_tf_var_env_for_every_step`) needed updating
     — a resource is now always delivered as `TF_VAR_resx_<type>` even
     with no explicit `configuration`, a real, correct behaviour change
     that test's exact-dict assertion hadn't anticipated. 8 new tests in
     `test_integrations_terraform_projection.py` (default presence,
     empty-category exclusion, disabled-resource exclusion, exact parity
     with `build_platform_projection()`'s own per-category values so
     there is no second, divergent implementation), 2 new end-to-end
     tests in `test_deploy_controller.py` (a resource's `${secret:}`
     resolves via `TF_VAR_resx_server` while the on-disk file stays
     literal; a `${output:}` in the same field is rejected outright).
     Full check suite green: mypy 107 files, ruff clean, import-linter
     1/0, pytest 1212 passed.
7. **Revisit gap #10 last**: if 0-6 land completely, the false-green-light
   failure mode is gone by construction — no code change needed there.
   Only the separate, smaller bare-`${KEY}`-token (no `kind:` prefix) lint
   hint would remain open, as its own lower-priority item.
   **Status: VERIFIED AND EXTENDED 2026-09-28 — the premise only partially
   held.** Verification (not assumption) found `unresolved_value_tokens()`'s
   one real call site only ever passed it DNS/network/firewall/module
   documents (`_documents_reachable_from_workspace()`'s own pre-Phase-6
   scope) — a token in `ResourceSpecModel.configuration`/
   `ProviderSpecModel.configuration`/`TenantSpecModel.configuration`/
   `WorkspaceSpecModel.configuration`/`.custom`/`.properties`/
   `TopologyVolumeModel.configuration`/an `EnvironmentSpecModel`/
   `DeploymentSpecModel`'s own `properties`/`custom` was never checked at
   all — a validate-time coverage gap Phase 6 was never going to fix
   (Phase 6 only closed the deploy-time delivery side for these same seven
   fields). Fixed: `_documents_reachable_from_workspace()` now also
   returns the workspace document itself plus every Resource/Provider/
   Topology it references; `_check_deployment_value_tokens()` additionally
   checks the deployment document, its resolved Tenant, and every
   reachable Environment (a new `_reachable_environment_names()` helper,
   extracted from `_merged_declared_keys()`, avoids duplicating the
   tenant-then-deployment merge-order logic). 7 new tests in
   `test_semantic_checks.py`; full check suite green (mypy 107 files, ruff
   clean, import-linter 1/0, pytest 1221 passed); `.v2-haven` unaffected
   (still the same known 44 errors — zero real document puts a token in
   any of these seven fields today). Full write-up in
   `docs/design/gap_fit_v1.md`'s gap #10.

Each phase is independently shippable and independently testable — 0-1 fix
the escape/malformed-syntax questions with zero dependency on the rest; 2-5
are the resolver, phase order matching integration complexity (Terraform
simplest/already-proven pattern, Helm/Compose need the new path-tracking
step first); 6 extends delivery to `configuration`/`custom`; 7 is
verification, not new code.

## Decision (2026-09-29): resolution reach must match validation reach — no curated per-category allowlist — ~~IMPLEMENTED (2026-09-29, as docs/design/gap_fit_v1.md gap #17)~~

**Requirement, stated directly (per request): the actual replacement
mechanism — not just validation — must work anywhere in `root.spec.*`
(any depth, any kind), for all five token kinds
(`var`/`secret`/`feature`/`output`/`value` — the last one proposed,
[cross-document-value-references.md](cross-document-value-references.md)),
not a hand-maintained subset of fields/categories.**

### Why this needed stating explicitly — a real gap found while answering "does resolution work everywhere?"

Validation already satisfies this (Phase 1/2's generic whole-document walk
— confirmed above, "The walking primitive already exists and is already
field-agnostic"). **Resolution does not, today** — checked
`terraform_projection.py` directly (2026-09-29): Terraform delivery is
built from a **curated list** of category-builder functions
(`build_dns_networks_firewalls_payloads()`'s 3 + `build_configuration_payloads()`'s
5), not a generic walk over everything `build_platform_projection()`
itself already produces (13 categories: `workspace`/`providers`/
`topologies`/`resources_by_category`/`namespaces`/`firewalls`/`dns`/
`networks`/`flags`/`variables`/`properties`/`custom`/`tenant`). Whatever
isn't on the curated 8-category re-resolution list simply never gets
substituted, no matter how correctly validated it is.

**Confirmed real, concrete gap this produces**: `_build_providers_payload()`
(`terraform_projection.py`) projects only `type`/`region`/`display_name`
from `ProviderPropertiesModel` — it never even projects `ProviderSpecModel.
configuration`/`.custom` into *any* artifact, build- or deploy-time, despite
the Per-Kind Status table above correctly saying Provider's Phase 1/Phase 2
*validation* is "Done." A `${var:X}`/`${secret:X}` token in a Provider's
`configuration` field passes `strata validate` cleanly today and is then
**silently dropped** — not even written as an unresolved literal, simply
never projected into any `.auto.tfvars.json` file the way the table's own
"Done" implies. This is a materially different, more complete way this
same "validation reach ≠ resolution reach" mismatch can hide, beyond what
Phase 7's own investigation already found for the seven `configuration`/
`custom`/`properties` fields — Phase 7 fixed *validate-time coverage* for
those seven; this is the *symmetric* miss on the *delivery* side, and
Provider in particular wasn't even one of the seven Phase 7 already
checked.

### Fix direction: genericize Terraform delivery to match validation's own genericization, not add a 9th curated category

Adding `providers` as a 9th hand-picked category to
`build_configuration_payloads()` would fix today's one known instance but
not the underlying pattern — the next kind added to the schema would
silently repeat this exact gap unless someone remembers to also add it to
the curated list, by hand, every time. The fix that actually closes this
by construction: **iterate every category `build_platform_projection()`
already produces** (the full 13, not a curated re-selection of some of
them) and apply the same "resolve via the step's `tokens`, deliver as
`TF_VAR_<category>`" treatment to each — `flags`/`variables`/`workspace`/
`namespaces` are harmless no-ops under this (already fully resolved, or
contain no token-bearing field at all, confirmed by reading each builder
function directly), so genericizing costs nothing for those, and closes
`providers` (and any future kind) automatically, with zero new
category-specific code required when a new kind gains a `configuration`/
`custom` field later.

Module/Helm/Compose delivery **already satisfies this** — confirmed
directly, not assumed: `resolve_module_values()`/`resolve_compose_values()`
both walk their entire rendered payload generically already (no
field-specific scoping, per their own docstrings) — the curation problem
above is Terraform-delivery-specific, an artifact of `terraform_projection.py`'s
own category/file-per-Terraform-variable convention (a real constraint —
Terraform needs one `variable "X" {}` per category, so *some* grouping is
unavoidable — the fix is "group by the same categories `build
platform_projection()` already uses, all of them," not "invent a new,
independent, curated list of which ones matter").

### Consequence for the proposed `value:` kind

Since `${value:...}` substitution reuses the exact same
`resolve_value_tokens()`/flat-map mechanism (docs/design/
cross-document-value-references.md's own "Resolution model" section) as
`var`/`secret`/`feature`/`output`, fixing the curation gap above for the
existing four kinds means `value:` inherits full `root.spec.*` reach for
free, the same way — no separate genericization work needed for the 5th
kind specifically, as long as it lands after (or alongside) this fix
rather than before it.

### Guardrail: prevent this regressing on the *tool* axis too (a future Bicep, not just a future kind)

The Terraform-delivery genericization above closes the gap on the **kind**
axis (a new schema kind's `configuration`/`custom` field is covered
automatically, no per-kind code needed). It does not, by itself, guarantee
parity on the **tool** axis — a future `InfraIntegration` subclass (e.g. a
real `BicepIntegration`, `ProvisionerType.BICEP` already exists in
`builtin_types.py`'s enum, anticipated in `registry.py`'s own
`_KNOWN_V1_TYPES` comment) could ship with none of `ENV_VAR_PREFIX`/
`SUPPORTS_OUTPUT`/`Capability.CONTAINER` deliberately set, silently
inheriting every optional default and never receiving a resolved value at
all — the exact same failure shape as the Provider gap above, on a
different axis. Confirmed real risk, not hypothetical: Bicep's actual
native input mechanism (a `parameters.json` file / `--parameters` CLI
args) isn't env-var-shaped at all, unlike Terraform/Compose — so
`ENV_VAR_PREFIX = None` would be the *correct* value for it, indistinguishable
from "nobody decided this yet" unless something forces the decision.

**Fix, mirroring an already-proven pattern in this exact codebase**
(`commands/exit_codes.py`'s `EXIT_CODE_BY_ERROR` table + its own
`test_every_error_type_is_mapped()`, which walks `StrataError.
__subclasses__()` recursively and fails if any subclass is unmapped): add
the same shape for `InfraIntegration`. A new contract test,
`test_every_infra_integration_declares_a_resolved_value_delivery_mechanism()`,
walks `InfraIntegration.__subclasses__()` recursively and asserts each one
either sets `ENV_VAR_PREFIX` or declares `Capability.CONTAINER` (today's
two real delivery shapes) — anything else must be added to an explicit,
commented allowlist in the test itself, never silently pass. This fails CI
the moment a `BicepIntegration` (or any future tool) is added without a
resolved-value delivery decision, forcing that decision at review time
instead of discovering it in production the first time someone's real
deployment gets a literal `${var:X}` string instead of the resolved value.

**Implemented (2026-09-29)**, in
`tests/strata/integrations/test_integrations_capabilities.py`. Two real
complications surfaced during implementation, both now handled and
documented in the test's own docstrings: registered integrations are
lazily imported by `registry.py`, so every `registry._KNOWN` entry must be
force-loaded (class only, never instantiated) before the
`__subclasses__()` walk finds anything; and the walk must filter to
`cls.__module__.startswith("strata.")` to exclude this same test file's
own `_Bare`/`_Compliant`/`_Incomplete`/`_WithOutput` fixture subclasses.
Companion test `test_every_infra_integration_type_is_a_known_real_integration()`
names the three covered integrations explicitly. Full check suite green.

### Status

**Implemented (2026-09-29), as `docs/design/gap_fit_v1.md` gap #17.**
`_build_providers_payload()` (`terraform_projection.py`) now includes
`configuration`/`custom`; `build_configuration_payloads()` extended from
its curated 5-category subset to all 10 non-claimable categories
(`workspace`/`providers`/`resx_<type>`/`topologies`/`namespaces`/`flags`/
`variables`/`properties`/`custom`/`tenant`) — `dns`/`networks`/`firewalls`
keep their unchanged, separate per-name claiming rule (gap #12). Kept as
two functions, not one (`build_dns_networks_firewalls_payloads()` +
`build_configuration_payloads()`), a deliberate deviation from the
original one-function sketch to avoid rewriting an existing test suite
that imports `build_configuration_payloads()` directly — `deploy_
controller.py`'s own two call sites needed zero logic changes either way.
Full writeup, including the one corrected design assumption found during
implementation (`workspace`/`providers` are *unconditionally present*, not
absent, in every real solution — "no-op" means their values don't change
under resolution, not that they contribute nothing), lives in
[cross-document-value-references.md](cross-document-value-references.md)'s
Implementation Plan (Phase 1) and `docs/design/gap_fit_v1.md` gap #17 — not
duplicated here. Full check suite green: mypy (107 files), ruff,
import-linter (1 kept, 0 broken), pytest (1258 passed).

**The `InfraIntegration.__subclasses__()` contract test above (Phase 0) is
implemented** — covers today's three registered subclasses
`TerraformIntegration`/`HelmIntegration`/`ComposeIntegration`, and every
future one from then on, automatically. **All 7 phases of
[cross-document-value-references.md](cross-document-value-references.md)'s
Implementation Plan are now done** (`${value:...}` syntax is recognized as
of Phase 2; `resolve_document_value_references()` — the resolution
primitive, living in `value_references.py` after Phase 5's circular-import
fix moved it out of `value_controller.py` — is implemented as of Phase 3;
`resolve_values()` merges its output as of Phase 4; `strata validate`'s
`_check_value_references()` reuses the same primitive as of Phase 5;
Phase 6 added documentation, a full implementation review — which found
and fixed two stale docstrings and one real, pre-existing, non-`value:`-
specific limitation (a Value token embedded in a `store: constant`
field's own value is never resolved) — and a live migration proof in
`.v2-cfg`'s real `environments/c0062-env.yaml`). `${value:...}` now
resolves correctly at deploy time AND is checked at validate time, with a
real, working, live example in the repo's own coverage-check fixture.

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — token syntax decision, resolver design, rationale for rejecting `ValueSourceModel`/Jinja
- [ADR-0005](../decisions/0005-dns-model-design-decisions.md), [ADR-0007](../decisions/0007-network-model-design-decisions.md), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md), [ADR-0009](../decisions/0009-module-model-design-decisions.md) — per-kind adoption
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, the `${step:}` token this doc doesn't cover yet
- [docs/design/gap_fit_v1.md](gap_fit_v1.md) gaps #8, #9, #10 — the real-world evidence (`.v2-haven`) that triggered this section

## Remaining Work / Open Questions

- ~~Build the `environment` kind~~ — **done**, this claim was stale:
  `EnvironmentModel`/`PlatformKind.ENVIRONMENT` already exists and is used
  extensively (`value_controller.py`, `build_value_references()`, etc.).
  ~~What's still open: wire `_validate_dynamic(environment_model=...)` on
  `DnsService`/`NetworkService`/`FirewallService`/`ModuleService` — one
  shared pattern, four call sites (sketched already in ADR-0002's worked
  example).~~ — **also done, 2026-09-28, via a different mechanism than
  sketched**: that specific per-service `_validate_dynamic()` shape was
  never built; the same outcome (every reachable document's tokens
  checked against the deployment's resolved environment) is achieved by
  `semantic_checks.py`'s `_check_deployment_value_tokens()` instead,
  extended in Phase 7 to reach every kind, not just DNS/network/
  firewall/module.
- ~~Build the actual resolver/router (partial regex substitution + secret-
  shaped-leaf routing) — **decided 2026-09-25: one shared implementation,
  applied uniformly to every kind above, built at deploy time**; **design
  completed 2026-09-28, see the new section above** (per-integration
  delivery mechanisms, phased plan). Still not started — the phased plan
  above is the concrete next step, no longer blocked on undesigned
  questions.~~ — **done, 2026-09-28**: this was the entire subject of the
  "Full Solution" Phases 0-7 above — Terraform/Helm/Compose delivery,
  escape syntax, and the validate-time cross-check are all implemented
  and tested.
- `DnsRecordModel.name` has no Value-token union at all (only `.value`
  does) — found 2026-09-25 via real evidence (`cfg-deployment/stacks/
  spoke/dns.yaml`'s own comment). A schema change, not a resolution change;
  logged here, not designed yet.
- Add `${step:step_name.output_key}` as a fourth token kind once Context
  ([ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)) is
  built, and re-add an output-sourced binding to `DnsRecordModel`.

## Changelog

- 2026-09-28: **Designed (not yet implemented)** the concrete deploy-time
  resolver closing `docs/design/gap_fit_v1.md` gaps #8/#9/#10, triggered by the
  `.v2-haven` coverage-check migration. Key finding:
  `resolve_value_tokens_in_mapping()` is already field-agnostic, so closing
  gap #8 (`configuration`/`custom` dicts) needs new call sites, not a new
  algorithm. Designed per-integration delivery (Terraform:
  `TF_VAR_<name>=<json>` for the whole `dns`/`networks`/`firewalls`
  payload, extending the same never-touches-disk pattern
  `backend.configuration` already has; Helm: rewrite `values.yaml` for
  non-secrets, `--set-string <dotted.path>=<value>` for secrets; Compose:
  rewrite the file for non-secrets, bare `${KEY}` rename + `env:` kwarg for
  secrets) and a 6-phase implementation plan. Also found this design
  substantially resolves gap #10 as a side effect (no more field-allowlist
  means no more false-green-light), leaving only the separate, smaller
  bare-`${KEY}`-token lint hint open.
- 2026-09-24: Created, consolidating the recurring "Environment cross-check
  deferred until `environment` kind exists" remaining-work item duplicated
  across ADR-0005/0007/0008/0009.
- 2026-09-25: Investigated dns/networks token resolution as a candidate
  `build run` feature (docs/design/build-command.md's gap table). Found
  real evidence it's genuinely wanted (cfg-deployment's `network.yaml`/
  `dns.yaml` comments) but confirmed v1 itself never resolves these at
  build time — only at deploy, with fully-resolved values and a fail-loud
  rule. Decided against a `build run`-scoped, `dns`/`network`-only fix;
  recorded the one-shared-resolver-at-deploy-time decision above. Also
  corrected the stale "`environment` kind not built" claim.
- 2026-09-25: Investigated `output.template` (ADR-0023 D3) as a candidate
  `build run` feature. Found the same flaw as the dns/networks finding
  above: ADR-0023's Phase 4 sketch assumed full build-time rendering, but
  `variables`/`flags` only carry `constant`/`environment`-backed values at
  build time — any real template referencing a Vault/AppConfig-backed key
  would raise unconditionally, every build. Split into build-time
  validation (genuinely buildable, not yet implemented) and deploy-time
  rendering (blocked on `deploy run`, same as (B) above). This led to
  naming and documenting all three of strata's value-supply mechanisms
  (A/B/C above) explicitly, since (B) and (C) were at risk of being
  conflated or merged — they solve different problems and should stay
  separate, but share one resolution primitive once deploy exists.
- 2026-09-28: **Added the "Full Solution" section** — unified Phase 1
  generalization, the escape syntax, and the gap #8/#9/#10 resolver into
  one end-to-end pipeline (author -> validate -> build -> deploy), and
  replaced the resolver-only phased plan with a full 9-phase
  (0-8) implementation plan. Corrected one earlier framing while unifying:
  unescaping (`$${` -> `${`) needs no resolved values, so it belongs at
  **build time** in each projection writer, not deferred to the deploy-time
  resolver as the escape-syntax section originally said — this makes an
  escape-only field (no real secret involved, e.g. Gatus's) already correct
  right after `build run`, with no dependency on `deploy run` or the
  resolver's other phases. Design only, nothing implemented yet.
- 2026-09-25: **Implemented the build-time-validation half of `output.template`.**
  Added `jinja2` as a dependency; `OutputModel`/`ProvisionerModel.output`
  (valid for any tool, unlike `backend`/`properties`); `strata/utils/templater.py`'s
  `validate_template_references()` — static-only, no rendering, two tiers
  (unknown root name via `jinja2.meta.find_undeclared_variables()`; unknown
  `variables.KEY`/`flags.KEY`/`secrets.KEY` via an AST walk for `Getattr`/
  `Getitem` nodes; `properties`/`custom` skipped, arbitrary shape). Wired
  into `InfraIntegration.prepare()`: when `provisioner.output.template` is
  set, `default_output()` is skipped entirely and nothing is written for
  that provisioner — validated only, deploy renders later. `template_path`
  is resolved by `build_controller.py` (workspace-relative, no `@repo/`
  yet — no evidenced need) and passed through explicitly, matching
  `sync_source()`'s "controller resolves paths, integration consumes
  already-resolved ones" split (ADR-0021 D2) — `templater.py` itself has no
  dependency on `ResolvedWorkspaceGraph`/`strata.integrations` (layering,
  ADR-0003), callers build the `known_names` schema and pass plain
  `dict`/`set` data in. 24 new tests (9 unit, 3 `prepare()`-level, 3
  end-to-end through `build_run()`, plus model tests). Full check suite
  green (1036 tests, mypy 103 files, 0 broken import-linter contracts).
- 2026-09-25: **Review pass found a real gap**: `--dry-run` skipped
  `output.template` validation entirely (it lived inside `sync_source()`'s
  branch, past the `if dry_run: ...; continue` early exit) — contradicting
  `build_run()`'s own documented rule that a dry run still catches what it
  cheaply can (already true for `--resolve`'s validation). Fixed:
  `template_path` resolution and the validation call now happen before the
  `dry_run` branch, so a bad reference or a missing template file is
  caught under `--dry-run` too, with zero filesystem mutation. 2 new tests
  confirm this. Full check suite green (1038 tests).
- 2026-09-28: **Validated the Full Solution design against real source
  before implementing, per request** ("let's validate the design before we
  implement it"). Found and fixed two real problems: (1) generalizing Phase
  1 via a `model_validator` on `PlatformBaseModel` doesn't work — grep
  confirmed `configuration`/`custom` is `dict[str, Any] | None` on 18
  models, which Pydantic never recurses into on its own, so the validator
  would need to reimplement `_iter_strings()` itself anyway; corrected to
  extend Phase 2's existing `unresolved_value_tokens()` walk instead of
  adding a second mechanism. (2) "unescape at build time, resolver needs no
  escape-awareness" is disproven for Terraform — `deploy_controller.py:303`
  proves `backend.configuration` resolves straight from the in-memory
  model, never from the on-disk (never-rewritten) `.auto.tfvars.json`, so a
  build-time unescape there would be invisible to deploy; also confirmed
  `resolve_value_tokens()`'s unanchored regex would today mismatch an
  escaped `$${var:x}` and produce a stray leading `$`. Corrected to fold
  escape-handling into the resolver itself, deploy-time, uniform across all
  three integrations — noting the real consequence that a Gatus-style
  escape-only field will now need `deploy run` (not just `build run`) once
  gap #8 wires resolution into `configuration`/`custom` at all. Full
  Solution section and 8-phase implementation plan (0-7) updated in place
  to reflect both corrections. Design only, still nothing implemented.
- 2026-09-28: **Implemented Phases 0-1 of the plan** (escape primitives +
  generalized malformed-token detection), per request ("design, plan, and
  implement phase 1"). `value_tokens.py`: added
  `strip_escaped_value_tokens()`/`find_malformed_value_tokens()`; refactored
  `validate_value_tokens()` to reuse the latter; made `has_value_tokens()`/
  `extract_value_tokens()` escape-aware (fixes the unanchored-match bug
  found during design validation). `environment_service.py`:
  `unresolved_value_tokens()` now also flags malformed candidates anywhere
  in the document walk, closing gap #8's `configuration`/`custom` blind
  spot without touching the four existing field-level validators. 17 new
  tests; full check suite green (1152 tests). Did **not** make
  `resolve_value_tokens()` itself escape-aware yet — no real resolution
  call site needs it before Phases 2-5. Checked real impact against
  `.v2-haven`: 44 new errors across 17 module files (`docs/design/gap_fit_v1.md`
  gap #8 has the full breakdown) — left unfixed, per explicit request, as
  its own dedicated follow-up rather than mass-editing 17 real files in
  the same change.
- 2026-09-28: **Implemented Phase 2** (Terraform `dns`/`networks`/
  `firewalls` payload resolution), per request ("design, plan, and
  implement phase 2"). Made `resolve_value_tokens()` itself escape-aware
  first (a combined regex matches the escaped-literal shape before the
  real-token shape, so an unanchored match can never reach inside an
  escaped span — the exact bug flagged, not yet fixed, in the design
  validation pass) — this was Phase 0's one deferred piece, needed now that
  a real resolution call site exists. Added
  `build_dns_networks_firewalls_payloads()` (`terraform_projection.py`),
  wired into `deploy_controller.py`'s per-step loop: computed once per run,
  re-resolved per step using the same `tokens` dict already built for
  `backend.configuration` (dependency-scoped `${output:}` support falls out
  for free, matching gap #11's own note that this call site needs zero
  extra design work). Empty categories skipped, matching `planned_files()`'s
  convention. Confirmed via a new test that the on-disk
  `dns.auto.tfvars.json` `build run` wrote stays literal/unresolved —
  only `TF_VAR_dns` carries the resolved payload. 4 new tests (1 resolver,
  1 deploy_controller, 2 mapping-level); full check suite green (1156
  tests); `.v2-haven` unaffected (same known 44 errors, deploy-time only).
- 2026-09-28: **Found gap #13, implemented Phase 4 scoped down**, per
  request ("design, plan, and implement phase 4"). Checked whether Helm's
  secret delivery had a real invocation to wire into before building it —
  it doesn't: `ProvisionerModel` has no release/chart/namespace field,
  no test exercises a `tool: helm`/`tool: compose` step through
  `deploy_run()`, and Helm's real granularity (one release per module,
  many modules per namespace-targeting step) structurally doesn't fit the
  current per-step loop shape either. Logged as gap #13
  (`docs/design/gap_fit_v1.md`), separate from gap #9. Built the piece that doesn't
  depend on it: new `resolve_module_values()` (`helm.py`) — pure,
  disk-free, applies Phase 3's path-tracking to one module's rendered
  `values.yaml`, mirroring `_render_values()`'s own testable convention.
  4 new tests; full check suite green (1169 tests). Compose (Phase 5) is
  blocked on gap #13 the same way — not attempted.
- 2026-09-28: **Resolved gap #12** (`docs/design/gap_fit_v1.md`), per pushback that
  the design's earlier "zero evidence" recommendation there was wrong, and
  a direct question about whether DNS/firewall/network genuinely need
  infra outputs (VM IPs, cluster data). Checked v1's real source (not the
  fixture): `DnsRecordModel.output_key` is real, working v1 code — v1
  defers resolution entirely to Terraform's own HCL (bucket coordinates,
  inject a plain `TF_VAR_<output_key>`, module reads `var.X` itself).
  Explicitly rejected switching v2 to that model — v2's Python-side
  embedded-token substitution (this doc's own design, already built in
  Phase 2) stays; only the missing step-ownership piece needed fixing.
  `workspace_model.py`'s `target_names` now accepts `dns_zones`/
  `networks`/`firewalls`, reusing `validate_provisioning_steps()`'s
  existing shared-target ordering rule; `deploy_controller.py` delivers a
  claimed document only to its owning step(s), resolved with that step's
  own outputs, and raises one clear diagnostic for an unclaimed
  `${output:}` document. 2 new tests; full check suite green (1158
  tests); `.v2-haven` unaffected. Full gap #12 write-up lives in
  `docs/design/gap_fit_v1.md`, not duplicated here.
- 2026-09-28: **Implemented Phase 3** (secret path-tracking primitive), per
  request ("design, plan phase 3" then "implement phase 3"). New
  `resolve_value_tokens_tracking_secrets()` (`value_tokens.py`) — a
  separate function from `resolve_value_tokens_in_mapping()` (Terraform's
  Phase 2 use delivers the whole payload via `TF_VAR_<name>` either way
  and never needed the split). A secret-shaped leaf's original,
  unresolved literal is left untouched in the returned dict rather than
  deleted — Helm's `--set-string`/Compose's `env:` both override whatever
  a file already has at that path regardless. Verified against the real
  `immich.yaml` nested-path shape and a mixed `${var:}`+`${secret:}`
  connection string (secret-shaped as a whole, per this doc's own
  existing rule, reported only in the secrets map). 7 new tests; full
  check suite green (1165 tests). No `deploy_controller.py`/`helm.py`
  wiring yet — that's Phase 4, which consumes this primitive's output.
- 2026-09-28: **Completed Phase 4** (Helm delivery), by implementing gap
  #13 (`docs/design/gap_fit_v1.md`) — the blocker this same day's earlier entry
  scoped Phase 4 down around. `resolve_module_values()` (built earlier
  today) now has a real caller: `HelmIntegration.deploy_namespace()`
  rewrites `values.yaml` for non-secret leaves and delivers secret-shaped
  ones via a new `--set-string` argv path. Full design/implementation
  write-up lives in `docs/design/deploy-command.md`'s "Helm/Compose
  orchestration" section, not duplicated here. Compose (Phase 5) remains
  blocked — no `ComposeIntegration.deploy_namespace()` yet. 40 tests in
  `test_integrations_helm.py`, 3 in `test_deploy_controller.py`; full
  check suite green (mypy 107 files, ruff clean, import-linter 1/0,
  pytest 1185 passed).
- 2026-09-28: **Completed Phase 5** (Compose delivery), per direct request
  ("so lets get back to phase 4. that should be complete, making phase 5
  available?"). Confirmed Phase 4 was indeed complete, then built Phase 5's
  own primitive: `resolve_value_tokens_renaming_secrets()` (`value_tokens.py`)
  is genuinely new logic, not a Phase-3 reuse — checked v1's real
  `ResolvedValues.as_compose_env()`/`inject_compose_env()` first (evidence
  over assumption) and confirmed Compose has no whole-path override
  mechanism the way Helm's `--set-string` does; it substitutes `${KEY}`
  occurrences *within* a string from the subprocess's own environment, so
  resolution must happen per-token, not per-leaf, and the returned secrets
  map is keyed by the token's own `KEY` name (not a dotted path — matches
  v1's real flat, deployment-wide `as_compose_env()` convention exactly).
  `resolve_compose_values()` (`compose.py`) is the thin per-tool wrapper
  (mirrors `resolve_module_values()`'s placement); `ComposeIntegration.
  deploy_namespace()` reads the namespace's already-merged
  `docker-compose.yml`, rewrites it, and delivers the renamed secrets via
  `docker stack deploy`'s own subprocess environment — no `.env` file
  needed, since `Integration.run()`'s `env` kwarg already merges per-call
  onto `os.environ` (simpler than v1's `os.environ`-mutating context
  manager). 11 new tests in `test_utils_value_tokens.py`, 6 in
  `test_integrations_compose.py`, 1 in `test_deploy_controller.py`
  (compose step dispatch end-to-end, mirroring the existing Helm one —
  proves the orchestrator's container-capability branch is genuinely
  tool-agnostic). Full check suite green: mypy 107 files, ruff clean,
  import-linter 1/0, pytest 1203 passed.
- 2026-09-28: **Designed Phase 6**, per direct request ("do the design and
  plan for phase 6"). Checked every real `configuration`/`custom`-shaped
  field against `terraform_projection.py`'s own `_build_*_payload()`
  functions directly (evidence over assumption, not a re-read of the
  phase's own one-line description) — found the phase's originally-stated
  target (Helm/Compose module `configuration`) is already closed as a side
  effect of Phases 4/5's generic payload walk, and a materially bigger,
  previously undiscovered gap in its place: `resources_by_category`
  (`ResourceSpecModel.configuration`/`WorkspaceResourceModel.configuration`),
  `topologies` (`TopologyVolumeModel.configuration`), `properties`,
  `custom`, and `tenant` (`TenantSpecModel.configuration`) are all
  projected into a `*.auto.tfvars.json` file at build time but excluded
  from Phase 2's `TF_VAR_` deploy-time delivery — the same shape dns/
  networks/firewalls had *before* Phase 2 fixed them. Zero real
  `.v2-haven` document uses a token in any of the five today (checked
  directly) — a consistency gap, not a currently-broken real document,
  same honesty Phase 2's own original discovery already modeled.
  Confirmed `FirewallSpecModel.configuration` is a separate,
  out-of-scope gap (never projected at all — a projection-completeness
  question, not a token-resolution one). Decided broadcast-only delivery
  (`${output:}` rejected outright, reusing `_contains_output_token()`
  unchanged) rather than extending gap #12's per-name claiming scheme —
  `properties`/`custom`/`tenant` are singletons with no name to claim by,
  and extending ownership to `topologies`/`resources_by_category` (which
  *do* have addressable names) is deferred pending real evidence, mirroring
  gap #12's own precedent. Planned one new function,
  `build_configuration_payloads()` (sibling to, not folded into,
  `build_dns_networks_firewalls_payloads()` — the claiming semantics
  genuinely differ), plus the matching `deploy_controller.py` wiring,
  reusing `resolve_value_tokens_in_mapping()` unchanged (no new resolution
  logic needed). Found and fixed two stale docstrings while gathering this
  evidence (`_build_dns_payload()`/`_build_networks_payload()` still
  claimed their tokens were "not yet wired" — true when written, false
  since Phase 2 shipped). Design only — nothing implemented yet.
- 2026-09-28: **Implemented Phase 6** exactly as designed, per direct
  request ("do the implementation"). `build_configuration_payloads()`
  (`terraform_projection.py`) returns `{tf_var_suffix: payload}` for every
  non-empty `resx_<type>`/`topologies`/`properties`/`custom`/`tenant`
  category; `deploy_controller.py` computes it once per run, pre-flight-
  rejects any `${output:...}` found in it (reusing `_contains_output_token()`
  unchanged), then delivers each as `TF_VAR_<name>` inside the existing
  Terraform-shaped branch only, identical to dns/networks/firewalls' own
  loop. One existing test needed a real update, not a workaround: a
  resource is now always delivered as `TF_VAR_resx_<type>` even with no
  explicit `configuration` set (harmless — `resolve_value_tokens_in_mapping()`
  is a no-op on token-free data — but a real, correct behaviour change
  the old exact-dict assertion hadn't anticipated). 8 new tests in
  `test_integrations_terraform_projection.py`, 2 new end-to-end tests in
  `test_deploy_controller.py`. Full check suite green: mypy 107 files,
  ruff clean, import-linter 1/0, pytest 1212 passed.
- 2026-09-28: **Verified and extended Phase 7**, per direct request ("ok
  next phase was 7?" then "design, plan, and implement"). Phase 7's own
  premise ("if 0-6 land completely, the false-green-light failure mode is
  gone by construction — no code change needed") was checked against the
  real code rather than assumed true: `unresolved_value_tokens()`'s one
  real call site (`_check_deployment_value_tokens()`,
  `semantic_checks.py`) only ever passed it DNS/network/firewall/module
  documents — `_documents_reachable_from_workspace()`'s own scope, written
  before Phase 6 existed. A token in `ResourceSpecModel.configuration`/
  `ProviderSpecModel.configuration`/`TenantSpecModel.configuration`/
  `WorkspaceSpecModel.configuration`/`.custom`/`.properties`/
  `TopologyVolumeModel.configuration`/an `EnvironmentSpecModel`/
  `DeploymentSpecModel`'s own `properties`/`custom` was never checked at
  all by `strata validate` — a genuinely separate validate-time coverage
  gap Phase 6 could never have fixed (Phase 6 only closed the deploy-time
  delivery side for those same seven fields). Extended
  `_documents_reachable_from_workspace()` to also return the workspace
  document itself (covering `WorkspaceResourceModel.configuration` for
  free via the same whole-document walk) plus every Resource/Provider/
  Topology it references; extended `_check_deployment_value_tokens()` to
  also check the deployment document, its resolved Tenant, and every
  reachable Environment (new `_reachable_environment_names()` helper,
  extracted from `_merged_declared_keys()` to avoid duplicating the
  tenant-then-deployment merge-order logic). 7 new tests in
  `test_semantic_checks.py`. Full check suite green: mypy 107 files, ruff
  clean, import-linter 1/0, pytest 1221 passed; `.v2-haven` re-run
  directly (not assumed) and confirmed unaffected — still the same known
  44 errors. All 7 phases of this design are now implemented; only the
  separate, lower-priority bare-`${KEY}`-token lint hint remains open.
- 2026-09-29: **Implemented the `InfraIntegration.__subclasses__()`
  guardrail test** (the "Guardrail" section above; tracked as Phase 0 of
  [cross-document-value-references.md](cross-document-value-references.md)'s
  Implementation Plan), per request ("design, plan, and implement phase
  0"). Full details, including two real complications found and fixed
  during implementation (test-fixture pollution, lazy-import blind spot),
  are recorded in that doc's own changelog rather than duplicated here.
  Full check suite green: mypy `src` clean, ruff `src tests` clean,
  import-linter 1/0, pytest 1256 passed. Phase 1 (the Provider-gap
  Terraform-delivery fix itself) remains design only.
- 2026-09-29: **Implemented Phase 1** (the Provider-gap Terraform-delivery
  genericization), per request ("design, plan, and implement phase 1").
  Confirmed the real gap directly against source first:
  `_build_providers_payload()` never read `ProviderSpecModel.
  configuration`/`.custom`, and grep confirmed nothing else in the
  codebase reads them either — live in `.v2-haven`'s real
  `hetzner-eu-de.yaml`. Fixed that function to include both; extended
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
  (1258 passed). Logged as `docs/design/gap_fit_v1.md` gap #17 (resolved). Phases
  2-6 (the `${value:...}` kind itself) remain unimplemented.
- 2026-09-29: **Implemented Phase 2** (`${value:...}` syntax recognition),
  per request ("design, plan, and implement phase 2"). Added `"value"` to
  `VALUE_TOKEN_KINDS` and both regex kind alternations in
  `value_tokens.py`, exactly as designed. Confirmed and tested one real
  interim consequence: `unresolved_value_tokens()` has no
  `kind == "value"` skip branch yet (Phase 5's job), so a document
  containing `${value:...}` today is honestly flagged
  `unsupported_value_token_kind` — same interim shape `${output:}` had
  before its own later phases. 6 new tests. Full check suite green: mypy
  (107 files), ruff, import-linter (1 kept, 0 broken), pytest (1264
  passed). Full writeup in
  [cross-document-value-references.md](cross-document-value-references.md)'s
  own changelog, not duplicated here. Phases 3-6 (existence/resolution/
  validate-time wiring) remain unimplemented.
- 2026-09-29: **Implemented Phase 3** (the `${value:...}` resolution
  primitive), per request ("design, plan, and implement phase 3"). New
  `resolve_document_value_references()` (`value_controller.py`, per Open
  Question 1's leaning). One real refinement found while implementing,
  against the design's own looser Phase 2 framing: a bare
  `${value:onlyonesegment}` is now confirmed to surface as
  `value_reference_unknown_kind` (the one segment genuinely isn't a real
  `PlatformKind` value), not `value_reference_invalid_path` as that
  earlier note loosely suggested — `value_reference_invalid_path` is
  reserved for a *valid* kind with no name segment at all (e.g. a bare
  `${value:tenant}`). 14 new tests, including the `kind: solution` edge
  case and both a `${var:}`- and a `${value:}`-chained non-literal-target
  rejection (proving the cycle-impossible-by-construction claim for a
  same-kind chain too, not just a mixed one). Full check suite green:
  mypy (107 files), ruff, import-linter (1 kept, 0 broken), pytest (1278
  passed). Full writeup in
  [cross-document-value-references.md](cross-document-value-references.md)'s
  own changelog, not duplicated here. Phases 4-6 (wiring into
  `resolve_values()`/`strata validate`, documentation) remain
  unimplemented.
- 2026-09-29: **Implemented Phase 4** (wiring `${value:...}` into
  `resolve_values()`), per request ("design, plan, and implement phase
  4"). Implemented exactly as designed, zero deviations:
  `resolve_values()` now calls `resolve_document_value_references()`
  unconditionally and merges into `ValueResolution.values`/`.diagnostics`.
  Confirmed `deploy_controller.py` needed zero changes — its own
  `resolved.values` consumption was already generic/blind to which
  mechanism produced an entry. 3 new tests. Full check suite green: mypy
  (107 files), ruff, import-linter (1 kept, 0 broken), pytest (1281
  passed). `${value:...}` now resolves correctly end-to-end at deploy
  time; only the `strata validate` check (Phase 5) and documentation
  (Phase 6) remain unimplemented.
- 2026-09-29: **Implemented Phase 5** (the `strata validate` check for
  `${value:...}`), per request ("design, plan, and implement phase 5").
  Found and fixed a real circular import, not anticipated by the design:
  `semantic_checks.py` (imported by `solution_context.py`) importing
  `resolve_document_value_references()` from `value_controller.py`
  (which itself imports `solution_context.py` for the `SolutionContext`
  type) closed a loop — `solution_context -> semantic_checks ->
  value_controller -> solution_context`. Neither mypy nor ruff nor
  `get_errors` caught it; only running `pytest` surfaced the real
  `ImportError`. Fixed by extracting the function into a new sibling
  module, `value_references.py`, which only needs `DocumentIndex` and has
  no dependency on `solution_context.py` — both callers now import it
  directly, no cycle. `unresolved_value_tokens()` gained the designed
  `kind == "value"` skip branch; `semantic_checks.py`'s new
  `_check_value_references()` reuses the (relocated) primitive as the 9th
  check. 4 new end-to-end tests, 1 existing test rewritten to assert the
  new correct behaviour instead of Phase 2's old interim one. Full check
  suite green: mypy (108 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1285 passed). Full writeup, including the circular-import root
  cause, in
  [cross-document-value-references.md](cross-document-value-references.md)'s
  own changelog. Only Phase 6 (documentation/final verification) remains.
- 2026-09-29: **Implemented Phase 6 and did a full implementation
  review** of the `value:` kind, per request ("design, plan, and
  implement phase 6 and do a full review of the implementation"). Fixed
  two stale docstrings in `value_tokens.py` (still described `${value:}`
  resolution as unimplemented, stale since Phase 3/4) plus one stale test
  docstring, adding a new positive-case test alongside. Found a real,
  confirmed, pre-existing limitation while attempting the live migration
  proof below: a Value token (any kind, not just `value:`) embedded in a
  `store: constant` field's own value is never resolved by
  `_resolve_store_value()`/`build_value_references()`. Live-migrated
  `.v2-cfg`'s real duplicated `"c0062"` (`environments/c0062-env.yaml`) to
  `${value:tenant.c0062.meta.name}` — only the `properties`-embedded
  occurrence, per the limitation just found; the `store: constant`
  variable occurrence was left a literal, with an explanatory comment.
  `strata validate .v2-cfg` (run via the local dev source) confirmed
  clean, 12/12; `.v2-haven` reconfirmed at its same known 44
  pre-existing errors, zero new `value_reference_*` codes. Full check
  suite green: mypy (108 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1286 passed). **All 7 phases of the `${value:...}` design are
  now implemented and live-proven.** Full writeup in
  [cross-document-value-references.md](cross-document-value-references.md)'s
  own changelog.

