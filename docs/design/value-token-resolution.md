# Value Token Resolution — Design

- Status: partially-implemented
- Last updated: 2026-09-28 (design validated against source — 2 corrections)

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
section.

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
  (future: `step`, once [Context](provisioning-injection-model.md) lands).
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

| Kind                           | Value-token field(s)                                                              | Phase 1 syntax check                                                    | Phase 2 Environment cross-check     |
| ------------------------------ | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------- |
| Provider                       | none identified                                                                   | n/a                                                                     | n/a                                 |
| Resource                       | none today; design below proposes extending to `configuration`/`custom` generally | n/a                                                                     | n/a                                 |
| DNS                            | `DnsRecordModel.value`                                                            | Done ([ADR-0005](../decisions/0005-dns-model-design-decisions.md))      | Deferred — needs `environment` kind |
| Network                        | `SubnetModel.cidr`, `NetworkDefinitionModel.address_space` entries                | Done ([ADR-0007](../decisions/0007-network-model-design-decisions.md))  | Deferred — needs `environment` kind |
| Firewall                       | `FirewallRuleModel.from_`/`.to`                                                   | Done ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Module                         | `ModuleServiceEnvironmentModel.value`                                             | Done ([ADR-0009](../decisions/0009-module-model-design-decisions.md))   | Deferred — needs `environment` kind |
| Namespace, Topology, Workspace | none yet                                                                          | n/a                                                                     | n/a                                 |

## Decision: unify `docs/_gap_v1.md` gaps #8/#9/#10 into one deploy-time resolver, general-scope, per-integration delivery (2026-09-28)

Triggered by reviewing the `.v2-haven` coverage-check migration (`docs/_gap_v1.md`
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
directly, not assumed — see `docs/_gap_v1.md` gap #9's own investigation):

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
2. **Terraform**: `dns`/`networks`/`firewalls` payload resolution via
   `TF_VAR_<name>=<json>`, reusing `resolve_value_tokens_in_mapping()`
   (now escape-aware per step 0) as-is.
3. **Path-tracking**: a sibling to `resolve_value_tokens_in_mapping()` (or
   an extension of it) that additionally reports `{dotted_path: value}` for
   every secret-shaped leaf, separate from the plain resolved-dict return
   for non-secret leaves.
4. **Helm**: rewrite `values.yaml` for non-secret leaves (escape-aware
   resolution unescapes any `$${...}` in the same pass); `--set-string`
   for secret-shaped leaves from step 3's path map.
5. **Compose**: rewrite the compose file for non-secret leaves (same
   escape-aware resolution); bare `${KEY}` rename + `env:` kwarg for
   secret-shaped leaves.
6. **Extend every delivery call site above to also walk
   `configuration`/`custom`** — closes gap #8 as a consequence of building
   #9 generally, not as a separate feature.
7. **Revisit gap #10 last**: if 0-6 land completely, the false-green-light
   failure mode is gone by construction — no code change needed there.
   Only the separate, smaller bare-`${KEY}`-token (no `kind:` prefix) lint
   hint would remain open, as its own lower-priority item.

Each phase is independently shippable and independently testable — 0-1 fix
the escape/malformed-syntax questions with zero dependency on the rest; 2-5
are the resolver, phase order matching integration complexity (Terraform
simplest/already-proven pattern, Helm/Compose need the new path-tracking
step first); 6 extends delivery to `configuration`/`custom`; 7 is
verification, not new code.

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — token syntax decision, resolver design, rationale for rejecting `ValueSourceModel`/Jinja
- [ADR-0005](../decisions/0005-dns-model-design-decisions.md), [ADR-0007](../decisions/0007-network-model-design-decisions.md), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md), [ADR-0009](../decisions/0009-module-model-design-decisions.md) — per-kind adoption
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, the `${step:}` token this doc doesn't cover yet
- [docs/_gap_v1.md](../_gap_v1.md) gaps #8, #9, #10 — the real-world evidence (`.v2-haven`) that triggered this section

## Remaining Work / Open Questions

- ~~Build the `environment` kind~~ — **done**, this claim was stale:
  `EnvironmentModel`/`PlatformKind.ENVIRONMENT` already exists and is used
  extensively (`value_controller.py`, `build_value_references()`, etc.).
  What's still open: wire `_validate_dynamic(environment_model=...)` on
  `DnsService`/`NetworkService`/`FirewallService`/`ModuleService` — one
  shared pattern, four call sites (sketched already in ADR-0002's worked
  example).
- Build the actual resolver/router (partial regex substitution + secret-
  shaped-leaf routing) — **decided 2026-09-25: one shared implementation,
  applied uniformly to every kind above, built at deploy time**; **design
  completed 2026-09-28, see the new section above** (per-integration
  delivery mechanisms, phased plan). Still not started — the phased plan
  above is the concrete next step, no longer blocked on undesigned
  questions.
- `DnsRecordModel.name` has no Value-token union at all (only `.value`
  does) — found 2026-09-25 via real evidence (`cfg-deployment/stacks/
  spoke/dns.yaml`'s own comment). A schema change, not a resolution change;
  logged here, not designed yet.
- Add `${step:step_name.output_key}` as a fourth token kind once Context
  ([ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)) is
  built, and re-add an output-sourced binding to `DnsRecordModel`.

## Changelog

- 2026-09-28: **Designed (not yet implemented)** the concrete deploy-time
  resolver closing `docs/_gap_v1.md` gaps #8/#9/#10, triggered by the
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
