# Unified Terraform Projection — One Variable, One Secrets Map — Design

- Status: current
- Last updated: 2026-10-11

## Overview

Every document strata resolves for a Terraform provisioner — workspace,
tenant, providers, resources, namespaces, modules, dns, networks,
firewalls, topologies, plus the flat `properties`/`custom`/`flags`/
`variables`/`default_tags` categories — reaches Terraform as **one**
nested object, `var.strata`, delivered via one `strata.auto.tfvars.json`
file. Exactly one secret-delivery channel exists alongside it: one
`TF_VAR_strata_secrets` environment variable, a flat `map(string)` keyed
by dotted path into the object above.

This replaces v1's inherited shape (thirteen independently-named
categories, each its own `*.auto.tfvars.json` file and/or `TF_VAR_<name>`
env var, a hardcoded category-name-to-variable-name rename table, a
`resx_<type>`-per-resource-type file split that silently lost data, and
per-category rather than per-secret taint granularity) — see
[ADR-0035](../decisions/0035-unified-terraform-projection.md) for why that
shape was replaced rather than patched.

## Current Design

### The output surface

Exactly four things reach a Terraform provisioner's build directory, and
nothing else:

1. **`strata.auto.tfvars.json`** — every non-secret value, one file, one
   top-level variable (`{"strata": {...}}`). Built by
   `build_strata_payload()` (`terraform_projection.py`); written by
   `TerraformIntegration.default_output()`.
2. **`TF_VAR_strata_secrets`** — the only environment variable strata
   sets for a Terraform step, a flat `map(string)` keyed by dotted path
   (e.g. `providers.kamatera.configuration.api_token`). Set by
   `deploy_controller.py`'s per-step delivery loop.
3. **`strata_variables.tf`** (generated) — declares `variable "strata"
   { type = object({...}) }` and `variable "strata_secrets" { type =
   map(string), sensitive = true }`, matching the exact shape
   `build_strata_payload()` produces. The authoritative schema lives in
   [`src/strata/templates/terraform/strata_variables.tf`](../../src/strata/templates/terraform/strata_variables.tf)
   — a static template (the type shape is identical for every workspace;
   only the values vary), copied verbatim on every `build run`.
4. **`strata_locals.tf`** (generated) — a `local.strata` that is
   `var.strata` with every secret-bearing leaf replaced by its real value
   from `var.strata_secrets`, so a consumer reads `local.strata.*`
   identically whether or not a given value is a secret. Built by
   `build_strata_locals(find_secret_paths(payload))`
   (`terraform_projection.py`).

Files 3 and 4 are both skipped when the copied `.tf` sources already
declare their own `variable "strata" {}` (`TerraformIntegration.
_sources_declare_own_strata_variable()`, a plain regex scan, no HCL
parsing) — a consumer who wants full control over the type takes on its
own secret-merging too, not just its own type.

`strata.default_tags` is the solution-wide tag baseline, hoisted out of
`workspace` to sit alongside `properties`/`custom`/`flags`/`variables`
(`WorkspaceSpecModel.default_tags` is cross-cutting, not workspace-scoped
data). Every other `default_tags` (`providers.<name>.default_tags`,
`resources.<name>.default_tags`, each `dns`/`networks`/`firewalls`
entry's own) stays where it is — those are genuinely per-entity
baselines.

**Every one of the fifteen top-level keys is always present**, empty
(`{}`) when unused rather than omitted — this is what lets the generated
type stay strict with no top-level `optional()`. The one exception:
`tenant` collapses to a bare `{}` (zero keys, not just empty values) when
the deployment references no tenant, so every `tenant.*` field is
`optional()` — the only place in the whole type that needs it.

### Build vs deploy

|                           | `build run`                                                     | `deploy run`                                                                                                                                                                                                |
| ------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata.auto.tfvars.json` | written, every token left literal (`"${secret:api_token}"`)     | regenerated from the resolved graph; every non-secret token replaced with its real value; every secret-shaped leaf replaced with a sentinel (`"__strata_secret__:<path>"`) — never the value, never omitted |
| `strata_variables.tf`     | generated (unless the root declares `variable "strata"` itself) | unchanged — shape is stable across both phases                                                                                                                                                              |
| `strata_locals.tf`        | generated — one merge/rebuild per secret-bearing path           | unchanged — the paths are known at build time                                                                                                                                                               |
| `TF_VAR_strata_secrets`   | not set                                                         | set to `{path: resolved_value}` for exactly the sentinel-marked leaves                                                                                                                                      |

`deploy run` regenerates the file from the freshly-resolved graph rather
than patching it in place — the full resolution pass runs either way, so
writing the whole file is both simpler and inherently idempotent.

### Secret delivery

`deploy run` writes a **sentinel string** (`"__strata_secret__:<path>"`)
in place of each secret rather than omitting the key. This keeps the
object's shape identical in both phases, which removes the need for any
`try()`/`lookup()`-with-fallback pattern at every secret-bearing call
site — a secret and a non-secret are read through the exact same
`local.strata.*` syntax:

```hcl
resource "azurerm_resource_group" "main" {
  location = local.strata.providers.azure.region                      # ordinary value
}

provider "kamatera" {
  api_token = local.strata.providers.kamatera.configuration.api_token  # secret — identical syntax
}
```

`strata_locals.tf` reassembles this by recursing per nesting level:

- A **dict level** (a key whose children are dict keys, e.g.
  `providers.kamatera`) renders `merge(var.strata[...], { "key" =
  <nested> })`, overriding only the keys that lead to a secret.
- A **list level** (a key whose children are all-numeric list indices,
  e.g. a DNS zone's `records`) renders `[for idx, item in <expr> : idx ==
  N ? <nested> : item]` instead — `merge()` rejects a list/tuple argument
  outright, so a secret reached through a list index needs this rebuild
  form, not a `merge()` call.
- A **leaf** renders `lookup(var.strata_secrets, "<path>",
  <sentinel-fallback-expression>)`.

A real key can contain characters a bare HCL identifier can't (a DNS
zone name like `example.com`, a hyphenated name like `public-dns`), so
every generated key is JSON-quoted and every access is bracket-indexed
(`var.strata["public-dns"]`), never a bare dot-chain. A real key
containing a literal `.` is escaped (`escape_path_segment()`/
`split_path()`, `value_tokens.py`) before being embedded in a dotted path
string, so it can't be confused with the `.` separator joining path
segments — the lookup key is then quoted via a dedicated
`_quote_full_path()` helper (doubling the literal backslash once, for
HCL's own string-escape rules), never through `json.dumps()` on top of
that doubling, which would escape it a second time and produce a key
that can never match the real one.

A workspace with no secrets gets the one-line fallback `locals { strata =
var.strata }`.

**What's left of the risk:** a module that reads `var.strata.*` directly
instead of `local.strata.*` gets the sentinel string instead — visibly
wrong and greppable, never a crash and never the real value. `build run`
does not currently reject such a reference in copied `.tf` sources (it
only scans for the `variable "strata"` opt-out declaration); doing so is
a possible future tightening, not a correctness gap today.

### Resources are a flat map, not nested by type

`strata.resources` is keyed directly by resource **name**
(`strata.resources.web-vm`), never nested by resource type the way v1's
`resx_<type>` file split was. Safe by construction —
`WorkspaceSpecModel` already validates globally-unique resource names
across the workspace, so flattening can never collide. A consumer that
wants per-type grouping writes an ordinary `for ... if res.resource_type
== "..."` expression — both real consumers checked at design time either
had zero `Resource` documents at all, or already wrote exactly that
expression to undo the old per-type split themselves.

### `modules` is projected

`strata.modules` is a per-document-name map (`type`, `source`,
`release_name`, `kubernetes_namespace`, `configuration`, `custom`,
`default_labels`, `custom_labels`, plus `mounts`/`checks`/`endpoints`
nested under `properties`) — read-only, alongside the existing Helm/
Compose workload pipeline (`prepare_namespace()`), which this does not
change. `ResolvedWorkspaceGraph.modules` is populated from every resolved
namespace's own `spec.modules[].module`, not from a topology's
`TopologyComponentModel.modules` (confirmed zero real use across every
workspace checked). `ModuleSpecModel.services` is deliberately excluded —
Helm chart/workload-builder internals, not something a Terraform root
plausibly needs.

### Why `ProvisionerModel` stays out entirely

Every projected category describes the **target infrastructure** the
copied `.tf` code reads. `ProvisionerModel` (`source`, `backend`,
`integration`, `output`, `depends_on`, ...) describes something else:
*how strata drives the tool* — and reaches Terraform through
`prepare()`/`init()` exactly as before, never through `strata.*`. A
workspace can declare several Terraform provisioners, each with its own
`source`/`backend`; `strata.*` is one shared object for the whole
workspace, so there is no single answer for "the" provisioner's own
config to put there even if it belonged.

### Type design

`configuration`/`custom`/`properties` are typed `any` — these are
genuinely opaque, consumer-defined passthrough dicts by design (confirmed
throughout the models: "not validated by strata, passed through as-is"),
so `any` is the accurate type, not a lazy one. `variables`/every
`labels` field is typed `map(any)`, not `map(string)` — `ValueReference.
value`/`*MetaModel.labels` are genuinely `Any`-typed in the real models.
Everything else strata knows the real shape of gets a concrete type —
see the template file linked above for the authoritative, current schema.

`strata_variables.tf` declares `terraform { required_version = ">= 1.3"
}` unconditionally (required for `optional()`-typed object attributes) —
verified empirically that multiple `required_version` blocks across
different `.tf` files in the same root merge/intersect their constraints
rather than erroring as a duplicate declaration, so this is safe to emit
even when the consumer's own root already has one. `sensitive = true` on
`strata_secrets` was tested against a real `terraform plan` and causes no
unhelpful output redaction.

## Related Decisions

- [ADR-0035](../decisions/0035-unified-terraform-projection.md) — the
  decision this doc describes the shape of: one `strata` object, one
  secrets map, replacing the per-category projection entirely.
- [ADR-0022](../decisions/0022-strata-build-run.md) — build renders
  unresolved, deploy renders resolved; same directory, regenerated not
  patched.
- [ADR-0023](../decisions/0023-build-output-rendering.md) — the
  `InfraIntegration.prepare()`/`default_output()` hook mechanism this
  design's output flows through (D2-D5 unchanged; D1's per-category
  shape is what this design replaced).
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) —
  strata supplies input rather than rewriting sources; the rule the two
  generated `.tf` files fall under (build artifacts, not synced sources).
- [ADR-0031](../decisions/0031-terraform-deploy-time-tfvars-file-rewrite.md) —
  the deploy-time file-rewrite mechanism (a `*.auto.tfvars.json` file
  outranks `TF_VAR_*`) this design's `deploy run` phase reuses, now
  rewriting one unified file instead of many per-category ones.

## History

- v1's inherited per-category shape caused a confirmed, evidenced data-
  loss bug: every `resx_<type>` file declared the same `resources`
  Terraform variable, and Terraform's own lexical-order
  `*.auto.tfvars.json` loading meant the last file won outright, with no
  warning — a standalone `terraform plan` against strata-v2's own `config/`
  dogfood build saw 1 of 4 real resources. Flattening `resources` into
  one name-keyed map (globally-unique names, already validated) deletes
  the bug structurally rather than patching the symptom.
- Secret taint was per-category, not per-leaf (one `${secret:}` anywhere
  inside `providers` blanked the entire category, not just the one
  provider holding it) — fixed by construction, not by narrowing: a
  secret is now one entry in one flat map, keyed by its own exact path.
- The first design for secret reassembly (`lookup(var.strata_secrets,
  path, try(var.strata.foo.bar, null))`) was rejected after tracing why
  it fails: `lookup()` evaluates all arguments eagerly, so an *omitted*
  secret key would make the fallback attribute access itself error —
  wrapping that in `try(..., null)` to dodge it is what actually
  manufactures a silent-`null` failure mode, not an inherent cost of a
  single-object design. Fixed by never omitting the key (write a
  sentinel instead) and having strata itself generate the reassembly
  (`strata_locals.tf`), rather than asking every module author to
  remember a helper at every secret-bearing use site.
- `type = any` for the whole `strata` variable was considered and
  rejected as the default (gives up Terraform's own type-checking at the
  one boundary where it matters most — a typo fails at `plan`/`apply`,
  not `validate`) but kept as a supported opt-out for a consumer who
  declares their own `variable "strata"`.
  `optional()`-typed object attributes need Terraform ≥ 1.3; verified
  against a real v1.12.2 binary, floor cleared by a wide margin.
- The `resx_<type>` per-resource-type file split and the `modules`
  exclusion were both re-checked against both real consumers
  (`cfg-int-deployment`, `haven`) before being changed: neither consumer
  depended on the resource-type split (one has zero `Resource`
  documents; the other already undoes the split itself with a `for ...
  if` expression), and adding `modules` costs nothing in a single-object
  shape where it would have cost a whole new file/variable/rename-table
  entry in the old per-category shape.
- A requirements-traceability pass after the implementation was believed
  complete (checking the delivered code against ADR-0035's own accepted
  decision text, not just the phase-by-phase implementation checklist)
  found `strata_locals.tf` generation had never actually been wired into
  `build run`'s `default_output()` — only built and unit-tested
  standalone — despite the ADR explicitly committing to it. Wiring it in
  for real immediately surfaced two latent bugs no existing test had
  exercised end-to-end: `merge()` rejecting a list-contained secret path
  (a DNS record's `value`, since `records` is a list — fixed with the
  `for`-expression list-rebuild form described above), and a lookup-key
  double-escaping bug (an HCL-string-escaping fix was itself wrapped in
  a redundant `json.dumps()`, so any secret reached through a dotted key
  silently resolved to the wrong, sentinel value with no error at all).
  Both are now covered by a real-terraform regression test using a DNS
  zone literally named `example.com` with a secret inside its `records`
  list — the one shape that exercises both bugs at once. Lesson: a
  phase-by-phase implementation checklist can be fully checked off while
  still not matching the actual accepted decision; worth re-checking
  against the ADR's own text directly, not just the plan derived from it.
- Two independent, pre-existing gaps were found during this design's own
  review but are explicitly out of scope for it (not caused by, and not
  fixed by, this redesign) — tracked separately in
  [deploy-time-output-sensitivity-gaps.md](../work/deploy-time-output-sensitivity-gaps.md)
  rather than silently dropped: `${output:...}` tokens are never treated
  as secret-shaped regardless of the referenced Terraform output's own
  `sensitive = true`, and a step's collected outputs are never persisted
  to disk, so a scoped `deploy run` that excludes an upstream step loses
  that step's outputs entirely for the run even though the real
  Terraform state still has them.
