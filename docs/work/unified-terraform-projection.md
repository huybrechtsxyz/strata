# Unified Terraform Projection — One Variable, One Secrets Map

- Status: **proposed, full design** — deliberately breaking, accepted as such
- Last updated: 2026-10-09
- Supersedes: [flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md)
  (that doc chased a per-category patch for the secret-taint gap; this
  design deletes the gap structurally instead, along with the category
  taxonomy that caused it — that doc is kept for its evidence record and
  its reasoning trail, not as a live proposal)
- Sequenced after: [flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md)
  (the `properties` rename — this design assumes the final, uniform model
  shape, not today's transitional one)
- Related: [terraform-variable-precedence.md](../design/terraform-variable-precedence.md),
  [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md)
  (the v1-parity catalog this design deliberately walks away from),
  [build-time-value-categories.md](../design/build-time-value-categories.md),
  ADR-0022 (build renders, deploy executes), ADR-0031 (deploy-time file
  rewrite)

## Premise

Strata-v2 is alpha. There are **two** real consumers today
(`cfg-int-deployment`, `haven`), both owned by the same team, both
absorbable. That window will not stay open — every additional consumer
makes the current, inherited-from-v1 projection shape harder to change,
and the complexity it forces is not incidental: it is the direct cause
of at least one real, evidenced correctness gap
([flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md))
and a long tail of special cases that each need their own rule.

This design takes the break now, on purpose, while it costs two repos
instead of twenty.

## What's wrong with the current projection

Not opinions — each of these is a real mechanism in the code today, with
its own special-case handling:

1. **Three different output shapes, not one.** Confirmed in
   [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md),
   which catalogued them empirically against real v1 output: *wrapped
   collections* (`{"platform_providers": {...}}`), *flat multi-variable
   bags* (`workspace.auto.tfvars.json`'s six independent variables), and
   *pure passthrough* (`properties`/`custom`). Every consumer, and every
   piece of strata's own delivery code, has to know which category is
   which.
2. **A rename table between file name and variable name.**
   `_REAL_VARIABLE_NAME` maps `providers` → `platform_providers`, `dns` →
   `dns_zones`, `tenant` → `strata_tenant`. Three arbitrary exceptions
   that exist purely because v1 named them that way.
3. **`resx_<type>` files all declare the *same* `resources` variable —
   and silently lose data.** One file per resource type, every one of
   them setting one shared variable. Terraform does not merge these: it
   processes `*.auto.tfvars.json` in lexical order and the **last one
   wins outright**, with *no warning and no error*. Verified empirically
   against Terraform v1.12.2, using strata-v2's own `config/` dogfood
   build output — which emits four such files
   (`resx_application_gateway`, `resx_key_vault`, `resx_storage_account`,
   `resx_virtual_machine`): a standalone `terraform plan` sees **1 of 4
   resources**. `edge-waf`, `security-kv` and `web-storage` vanish
   silently; only `web-vm` survives, because `resx_virtual_machine`
   sorts last. This is masked at *deploy* time by unconditionally
   blanking every `resx_*` file and supplying one merged
   `TF_VAR_resources` instead — but that workaround does not exist for
   the standalone-plan path, which requirement (3) below says must
   work. So the file-per-type convention is not merely redundant
   v1 parity: **it is actively broken for any workspace with more than
   one resource type.**
4. **Secret taint granularity is per-category, not per-value.** One
   `${secret:}` anywhere inside `providers` blanks the *entire*
   `platform_providers` variable — every provider's data, not just the
   one with the secret. Fully evidenced in
   [flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md).
5. **The env-var channel is sized wrong as a direct result of (4).** It
   was designed for "one short secret scalar" but can be handed an
   arbitrarily large structure that merely *contains* a secret —
   colliding with real OS limits (~32KB/var on Windows, `ARG_MAX` on
   Linux) with no check or warning anywhere.
6. **`FLAT_CATEGORIES` is a hardcoded set of five names** that behave
   differently from every other category, for no reason a consumer could
   infer from the data itself.

Every one of these is **inherited**, not essential. None of them follows
from the actual requirements.

## The actual requirements, restated from scratch

1. Strata resolves a graph of documents into values Terraform consumes.
2. **Secrets must never touch disk** — process environment variable only.
   (ADR-0031: "resolved values must never be written to any file, even
   under the opt-in `--resolve` flag — an existing, absolute rule.")
3. A build artifact must be **standalone-usable**: `terraform plan` works
   with no strata wrapper for everything that isn't a secret (ADR-0022).
4. `build` renders unresolved (tokens literal); `deploy` renders resolved.
   Same directory, file regenerated not patched.
5. Terraform's own precedence is fixed and non-negotiable:
   `*.auto.tfvars.json` > `TF_VAR_*`. There is **no** in-file syntax for
   referencing an environment variable.

That's all of it. Nothing in that list implies categories, rename tables,
per-type files, or a taint taxonomy.

## The design

### The output surface

One data file, one env var, and two generated `.tf` files — and nothing
else. (The first draft said "two artifacts"; generating the type and the
secret-reassembly turned out to be worth two more, each covered in its
own section below.)

**1. `strata.auto.tfvars.json`** — every non-secret value, one file, one
top-level Terraform variable:

```json
{
  "strata": {
    "workspace": {
      "name": "my-workspace",
      "version": "1.0.0",
      "deployment": "prd-deploy",
      "environment": "prd",
      "platform_version": "strata.omp.com/v2",
      "labels": {},
      "configuration": {},
      "custom": {},
      "default_tags": {}
    },
    "tenant": {
      "code": "c0062",
      "name": "GSK",
      "geographies": ["europe"],
      "onboarded": "2026-03-15",
      "configuration": { "dns_slug": "gsk" }
    },
    "providers": {
      "azure":    { "type": "azurerm",  "region": "westeurope", "configuration": {}, "custom": {} },
      "kamatera": { "type": "kamatera", "region": "eu",         "configuration": {}, "custom": {} }
    },
    "resources": {
      "web-vm":      { "provider_type": "azure", "resource_type": "virtual_machine",    "configuration": {} },
      "web-storage": { "provider_type": "azure", "resource_type": "storage_account",    "configuration": {} },
      "edge-waf":    { "provider_type": "azure", "resource_type": "application_gateway", "configuration": {} }
    },
    "namespaces": { "hearth": { "modules": ["authentik"], "configuration": {}, "custom": {} } },
    "modules": {
      "authentik": {
        "type": "helm",
        "source": { "...": "..." },
        "release_name": "authentik",
        "kubernetes_namespace": "hearth",
        "mounts":    [{ "name": "media", "target_path": "/media", "storage_class": "fast-ssd" }],
        "checks":    [{ "name": "http", "type": "http", "endpoint": "/healthz" }],
        "endpoints": [{ "name": "web", "type": "http", "port": 9000 }],
        "configuration":  {},
        "custom":         {},
        "default_labels": {},
        "custom_labels":  {}
      }
    },
    "dns":        { "example-zone": { "provider": "azure", "zones": {} } },
    "networks":   { "spoke-net": { "networks": {} } },
    "firewalls":  { "edge-fw": { "allow": [], "deny": [] } },
    "topologies": { "main": { "type": "kubernetes", "components": [] } },
    "properties": { "deploy_tier": "standard" },
    "custom":     { "cost_center": "1234" },
    "flags":      { "enable_x": true },
    "variables":  { "customer_code": "c0062" }
  }
}
```

**2. `TF_VAR_strata_secrets`** — the only environment variable strata
sets, a flat `map(string)` keyed by dotted path into the structure above:

```
TF_VAR_strata_secrets={"providers.kamatera.configuration.api_token":"<resolved>","variables.db_password":"<resolved>"}
```

That is the entire output surface. No other files, no other env vars.

### Build vs deploy

|                           | `build run`                                                     | `deploy run`                                                                                                                                                                                                        |
| ------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata.auto.tfvars.json` | written, every token left **literal** (`"${secret:api_token}"`) | **regenerated** from the resolved graph; every non-secret token replaced with its real value; every secret-shaped leaf replaced with a **sentinel** (`"__strata_secret__:<path>"`) — never the value, never omitted |
| `strata_variables.tf`     | generated (unless the root declares `variable "strata"` itself) | unchanged — shape is stable across both phases                                                                                                                                                                      |
| `strata_locals.tf`        | generated — one merge per secret-bearing path                   | unchanged — the paths are known at build time                                                                                                                                                                       |
| `TF_VAR_strata_secrets`   | not set                                                         | set to `{path: resolved_value}` for exactly the sentinel-marked leaves                                                                                                                                              |

Writing a **sentinel** rather than omitting the key is what keeps the
structure's shape identical in both phases — see "Secret delivery" below
for why that matters more than it first appears.

Regeneration, not in-place patching — same reasoning already established:
the full resolution pass has to run either way, so writing the whole file
from the freshly-resolved graph is both simpler and inherently
idempotent/driftless.

### Secret delivery, and why the obvious helper is wrong

This is the part that needed the most work. The first draft proposed:

```hcl
# WRONG — do not use. Kept to explain why.
lookup(
  var.strata_secrets,
  "providers.kamatera.configuration.api_token",
  try(var.strata.providers.kamatera.configuration.api_token, null)
)
```

Two distinct problems, both real:

1. **If the deploy-time file *omits* the secret key** (the first draft's
   plan), then `var.strata.providers.kamatera.configuration.api_token`
   is an access to a non-existent attribute. Terraform errors on that —
   *loudly*, which is good — but it means the fallback expression can
   never be written that way, because `lookup()` evaluates **all** its
   arguments eagerly. The default blows up even when the lookup would
   have succeeded.
2. **Wrapping it in `try(..., null)` to dodge that** is exactly what
   creates the silent-`null` failure mode. The `try()` swallows the
   "attribute doesn't exist" error that was the one thing warning you
   the helper was needed. So the first draft's own helper *manufactured*
   the weakness listed as cost #4 — it wasn't inherent to the design.

#### Fix part 1: never omit the key — write a sentinel

`deploy run` writes a **sentinel string** in place of each secret rather
than removing the key:

```jsonc
// strata.auto.tfvars.json, after `deploy run`
"configuration": { "api_token": "__strata_secret__:providers.kamatera.configuration.api_token" }
```

No secret touches disk — requirement (2) is still absolutely satisfied,
since the sentinel *is* the path, not the value. But the **shape stays
stable**, which buys a lot:

- Every attribute access remains valid, so a fallback expression is
  always safe to evaluate — `try()` is no longer needed anywhere.
- The generated `object({...})` type needs no `optional()` for
  secret-bearing fields; the key is always present in both phases.
- A module that reads the value *directly*, bypassing the helper
  entirely, gets `"__strata_secret__:providers.kamatera..."` — an
  obviously wrong, fully self-describing, greppable value that names its
  own correct lookup path. Compare to `null`: no information at all.

**At build time the sentinel is free** — the file already holds the raw
literal `"${secret:api_token}"` (ADR-0022 D4, and confirmed by ADR-0031's
own description of "the stale, unresolved build-time file" being read by
Terraform as-is — `.tfvars.json` values are literal, Terraform applies no
`${}` interpolation to them). That literal is itself a perfectly good
sentinel for the standalone-plan case.

#### Fix part 2: strata generates the merge, so no one hand-writes lookups

Rather than asking every module author to remember a helper at every
secret-bearing use site — the thing cost #4 was really about — `build
run` emits the reassembly itself:

**`strata_locals.tf`** (generated, never hand-edited):

```hcl
# GENERATED BY STRATA — DO NOT EDIT
# local.strata is var.strata with every secret-bearing leaf replaced by
# its resolved value from var.strata_secrets. Read local.strata.* —
# never var.strata.* — and secrets are simply never a special case.

locals {
  strata = merge(var.strata, {
    providers = merge(var.strata.providers, {
      kamatera = merge(var.strata.providers.kamatera, {
        configuration = merge(var.strata.providers.kamatera.configuration, {
          api_token = lookup(
            var.strata_secrets,
            "providers.kamatera.configuration.api_token",
            var.strata.providers.kamatera.configuration.api_token  # sentinel — safe, key always exists
          )
        })
      })
    })

    variables = merge(var.strata.variables, {
      db_password = lookup(
        var.strata_secrets,
        "variables.db_password",
        var.strata.variables.db_password
      )
    })
  })
}
```

Nested and verbose — but **generated**, so nobody reads or maintains it.
Strata knows every secret-bearing path at build time (that's exactly
what a `${secret:}` token *is*), so it can emit precisely the needed
merges and nothing else. A workspace with no secrets gets
`locals { strata = var.strata }`.

#### The resulting consumer rule: one line, no exceptions

```hcl
# Read local.strata.*, always. Never var.strata.*.
resource "azurerm_resource_group" "main" {
  location = local.strata.providers.azure.region        # ordinary value
  tags     = local.strata.workspace.default_tags
}

provider "kamatera" {
  api_token = local.strata.providers.kamatera.configuration.api_token  # secret — identical syntax
}
```

A secret and a non-secret are read **exactly the same way**. There is no
helper to remember, no path string to hand-type, no per-secret knowledge
in the module at all — which means cost #4's "miss one and it silently
breaks" failure mode has nothing left to miss.

#### What's left of the risk

Only one thing: a module that reads `var.strata.*` instead of
`local.strata.*`. That now fails *visibly* (it gets the sentinel string),
and it's a single, greppable, lintable rule — strata could even reject
`var.strata.` references in a copied `.tf` source at build time, since it
already scans those sources for the `variable "strata"` opt-out check.
Worth doing; not required for correctness.

### The `.tf`-side convention

Two generated files, two declared variables, one consumer rule:

```hcl
# strata_variables.tf (generated) — see "Type design" below
variable "strata"         { type = object({ ... }) }
variable "strata_secrets" { type = map(string), default = {}, sensitive = true }

# strata_locals.tf (generated) — see "Secret delivery" above
locals { strata = merge(var.strata, { ... }) }
```

```hcl
# Everything the consumer writes, for every value, secret or not:
local.strata.<category>.<name>.<field>
```

### Type design

The single biggest objection to a one-variable design is "what *is* that
variable, exactly?" — so this needs a real answer, not `type = any` and a
shrug. Three options, in increasing order of strictness:

#### Option A — `type = any` (rejected as the default)

```hcl
variable "strata" { type = any }
```

Works immediately, zero coupling, zero maintenance. But it gives up
everything Terraform's own type system offers at the one boundary where
it matters most: a typo (`var.strata.provders.azure`) fails at
plan/apply time with an unhelpful error, not at `terraform validate`. For
a structure this central, that's a real regression over today's
per-category `variable "platform_providers" { type = map(object({...})) }`
possibility. Keep as an escape hatch, not the recommendation.

#### Option B — strata generates the type (recommended)

`build run` emits one extra file alongside the tfvars:

**`strata_variables.tf`** (generated, never hand-edited):

```hcl
# GENERATED BY STRATA — DO NOT EDIT
# Regenerated on every `strata build run`. Matches the exact shape of
# strata.auto.tfvars.json produced by the same run.

variable "strata" {
  description = "Everything strata projects for this deployment."
  type = object({
    workspace = object({
      name             = string
      version          = string
      deployment       = string
      environment      = string
      platform_version = string
      labels           = map(string)
      configuration    = any          # free-form passthrough — see note
      custom           = any
      default_tags     = map(string)
    })

    tenant = object({
      code          = string
      name          = string
      geographies   = list(string)
      onboarded     = string
      configuration = any
    })

    providers = map(object({
      type          = string
      region        = string
      display_name  = optional(string)
      description   = optional(string)
      labels        = map(string)
      tags          = list(string)
      configuration = any
      custom        = any
      default_tags  = map(string)
    }))

    resources = map(object({
      provider_type = string
      resource_type = string
      subcategory   = optional(string)
      unit_cost     = optional(number)
      role          = optional(string)
      count         = optional(number)
      configuration = any
      default_tags  = map(string)
      custom_tags   = map(string)
      firewalls     = list(string)
      subnet        = optional(any)
    }))

    namespaces = map(object({
      description    = string
      labels         = map(string)
      tags           = list(string)
      modules        = list(string)
      configuration  = any
      custom         = any
      default_labels = map(string)
      custom_labels  = map(string)
    }))

    modules = map(object({
      type                 = optional(string)
      source               = any
      release_name         = optional(string)
      kubernetes_namespace = optional(string)
      mounts               = list(any)
      checks               = list(any)
      endpoints            = list(any)
      configuration        = any
      custom               = any
      default_labels       = map(string)
      custom_labels        = map(string)
    }))

    dns        = map(any)   # per-zone shape, see note
    networks   = map(any)
    firewalls  = map(any)
    topologies = map(any)

    properties = any
    custom     = any
    flags      = map(bool)
    variables  = map(string)
  })
}

variable "strata_secrets" {
  description = "Resolved secret values, keyed by dotted path into var.strata."
  type        = map(string)
  default     = {}
  sensitive   = true
}
```

**Why `any` for `configuration`/`custom`/`properties` specifically**: these
are deliberately free-form `dict[str, Any]` on the strata side — the whole
point is that strata never validates or interprets them (confirmed
throughout the models: "Not validated by strata, passed through as-is").
Generating a concrete type for them is impossible by construction, since
their shape is the *consumer's* business, not strata's. `any` here is
accurate, not lazy — it's the correct type for a genuinely opaque
passthrough.

Everything strata *does* know the shape of gets a real type.

#### Option C — consumer hand-writes the type

Possible, and strictly the most flexible (a consumer could narrow
`configuration` to its own real shape). But it has to track strata's
schema by hand, forever, with no mechanism to catch drift — exactly the
failure mode `strata validate` exists to prevent elsewhere. Supported by
not generating the file (see below), but not recommended.

#### Recommended: generate it, let the consumer opt out

- `build run` writes `strata_variables.tf` **unless** the root already
  declares `variable "strata"` itself (checked by scanning the copied
  `.tf` sources — the same scan `_validate_inputs()`-style checks would
  need anyway). A consumer that wants Option C just declares its own and
  strata stays out of the way.
- The file is regenerated every build, so it never drifts from the
  payload the same run produced — which is the key property Option C
  can't offer.
- It's a *generated build artifact*, same category as the tfvars file
  itself (ADR-0025: strata supplies input rather than rewriting sources,
  and its own generated output is not a synced source) — so writing it
  doesn't conflict with the no-source-rewriting rule.

#### Resolved: `optional()` is allowed

**Decision (2026-10-09): use `optional()` freely.** It requires Terraform
≥ 1.3; the real binary this mechanism was verified against is **v1.12.2**
(confirmed in ADR-0031, which tested the precedence behaviour directly
against it), so the floor is cleared by a wide margin.

Two caveats worth recording rather than discovering later:

- The generated file should state the requirement explicitly, so a
  consumer on an older Terraform gets a clear error instead of a
  confusing parse failure:

  ```hcl
  terraform {
    required_version = ">= 1.3"   # object type optional() attributes
  }
  ```

  Open detail: whether strata emits that `terraform {}` block itself
  (risking a duplicate if the root already declares one — Terraform
  rejects two `required_version` settings in the same module) or just
  documents the requirement. Leaning toward *documenting only* and
  leaving the block to the consumer, since a duplicate is a hard error
  while a missing one is a clear, self-explanatory failure.
- `optional()` with a default (`optional(string, "")`) is also available
  and worth using where strata has a real default, so a consumer never
  has to null-check a field strata always fills in practice.

#### Remaining sub-questions for this section

- **Does `sensitive = true` on `strata_secrets` cause problems?** It
  makes any derived value sensitive too, which can make plan output
  unhelpfully redacted in places. Worth testing against a real root
  before committing to it.
- **Should the generated type be `optional()`-heavy or strict?** Partly
  answered already: the sentinel decision ("Secret delivery" above)
  means secret-bearing fields are *always* present in both phases, so
  none of them needs `optional()`. What's left is the empty-category
  case — a workspace with no `dns` documents. Simplest answer: strata
  always emits every top-level key, empty when unused, letting the type
  stay strict with no top-level `optional()` at all. Worth confirming
  that's what the projection actually does.

### Resources are one flat map, not nested by type

The first draft of this design kept resource types as a nesting level
(`strata.resources.<type>.<name>`) — a reflex carried over from the
`resx_<type>` file split rather than a decision. Checking both consumers
killed it.

**Evidence (2026-10-09, both consumers checked directly):**

- **`cfg-int-deployment`** has *zero* `Resource` documents (no
  `resource_type` anywhere in its config) and references `resources` in
  no `.tf` file at all. It is entirely unaffected either way.
- **`haven`** declares **one** flat `variable "resources"` — `map(object({...}))`
  keyed by resource *name*, with the type as an ordinary field — and
  then does exactly this:

  ```hcl
  vm_resources = {
    for name, res in var.resources : name => res
    if res.type == "virtualmachine"
  }
  storagebox_resources = {
    for name, res in var.resources : name => res
    if res.type == "storagebox"
  }
  ```

  The only real consumer of resources **re-merges what the split
  separated**, then re-derives the type from a field that was already
  inside each entry. The nesting level isn't just unused — it is
  actively worked around.

So the type nesting buys nothing a `for ... if` doesn't already give the
consumer for free, while costing a whole extra map level on every single
access path.

**Decision: `strata.resources` is a flat map keyed by resource name**,
with `resource_type` remaining a field on each entry (it already is).

This is safe by construction, not by convention: `WorkspaceSpecModel`
already validates `check_unique_names([r.name for r in self.resources],
"resource names")`, so resource names are globally unique across the
workspace and cannot collide when flattened. A consumer that genuinely
wants per-type grouping writes the same one-line `for ... if` haven
already writes today.

It also happens to be the *cheapest* migration in the whole design:
`var.resources` → `local.strata.resources` is a pure rename, because the
shape haven already declares is the shape this produces.

### Projecting `modules`

The current projection deliberately skips `modules` entirely. Its stated
reason (`terraform_projection.py`'s own module docstring): zero use of
`TopologyComponentModel.modules` was found across all six real workspaces
checked, and Compose/Helm modules go through the separate
`prepare_namespace()` workload pipeline (ADR-0022 D5–D7) rather than this
one.

That reasoning was sound for a *per-category* design, where adding
`modules` meant inventing another file name, another variable name,
another rename-table entry, and another delivery rule — real cost, for no
proven demand.

**It stops being sound here.** In a single-`strata`-object shape, adding
`modules` is one more nested key — no new file, no new variable, no new
convention, no new special case. And leaving it out is now *itself* the
special case: a consumer reading `local.strata.*` would find every kind
present except one, with no inferable reason why.

**Decision: project `modules` too.** Same per-document-name map as every
other kind, carrying the flattened `ModuleSpecModel` fields (post-rename,
per [flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md)):
`type`, `source`, `release_name`, `kubernetes_namespace`, `mounts`,
`checks`, `endpoints`, `configuration`, `custom`, `default_labels`,
`custom_labels`.

Two things this explicitly does **not** change:

- **The workload pipeline is untouched.** Helm/Compose still render
  `values.yaml`/`docker-compose.yml` through `prepare_namespace()`,
  exactly as today. This adds a *read-only projection* of the same
  documents for Terraform's benefit — it does not route module
  deployment through Terraform.
- **`services` stays out.** `ModuleSpecModel.services` is a
  multi-container/sub-chart definition whose only consumer is the
  workload builder; projecting it would be shipping the Helm chart's
  internals into Terraform for no reason. `mounts`/`checks`/`endpoints`
  are included because they describe things Terraform plausibly *does*
  care about (storage classes, health endpoints, ports) — if that turns
  out to be wrong, dropping them is a one-line change, not a convention
  change.

The real payoff: a Terraform root can finally answer "what modules are
deployed into this namespace, and on what ports?" without a second,
out-of-band source of truth. Whether any consumer wants that yet is
unproven — but unlike before, finding out costs nothing.

## What this fixes, structurally

Each of these is deleted outright, not worked around:

| Current problem                               | How this design removes it                                                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Three output shapes                           | One shape: a nested object under one variable                                                                          |
| `_REAL_VARIABLE_NAME` rename table            | Gone — no second name exists to map to                                                                                 |
| `resx_<type>` same-variable collision         | Gone — resources are one flat name-keyed map (`strata.resources.web-vm`), not N files declaring a shared variable      |
| Per-category secret taint                     | Gone — granularity is **per-leaf by construction**; a secret is one entry in one flat map, keyed by its own exact path |
| Env-var size risk                             | Largely gone — only actual secret *scalars* ride the env var, never a structure that merely contains one               |
| `FLAT_CATEGORIES` special set                 | Gone — `properties`/`custom`/`flags`/`variables` are ordinary nested keys with no special delivery rule                |
| N per-category `.tf` conventions              | One convention, adopted once per root                                                                                  |
| "which categories reach Terraform?" confusion | One answer: everything under `strata`, uniformly                                                                       |

The secret-taint doc's entire subject stops being a problem to solve and
becomes a property of the shape.

## What it costs — stated plainly

1. **Every real consumer `.tf` root breaks.** Every
   `var.platform_providers` → `var.strata.providers`, every
   `var.resources` → `var.strata.resources`, every
   `var.strata_tenant` → `var.strata.tenant`, every
   `var.workspace_name` → `var.strata.workspace.name`. Mechanical, but
   it touches every file that reads a strata-supplied value. (Resources
   are the mildest case — `var.resources` → `local.strata.resources` is
   a pure rename, since the shape is unchanged; see "Resources are one
   flat map" above.)
2. **v1 output parity is abandoned entirely.** The migration story stops
   being "same variables, new tool" and becomes a real rewrite. This is
   the single biggest thing being given up —
   [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md)
   exists specifically because parity was previously treated as a goal.
3. **Type safety at the root boundary needs a deliberate choice.** With
   `type = any`, Terraform no longer validates the shape of what strata
   supplies. Addressed in full by the "Type design" section above —
   strata generates the `object({...})` declaration itself, so the type
   is both real and automatically in sync, at the cost of one new
   generated artifact.
4. **One new rule for module authors: read `local.strata.*`, never
   `var.strata.*`.** That's the whole convention — see "Secret delivery"
   above. Breaking it fails *visibly* (the reader gets a self-describing
   sentinel string, not `null`), and it's a single greppable rule rather
   than per-secret knowledge spread across the module. The first draft's
   `lookup()`-at-every-use-site burden is gone; strata generates the
   reassembly instead.
5. **Two generated `.tf` files now land in the build output**
   (`strata_variables.tf`, `strata_locals.tf`) where previously there
   were none. Both are build artifacts, not synced sources (ADR-0025),
   but it *is* more generated surface area to keep correct.

## Migration

Two consumers, both absorbable, both owned by the same team.

1. Land the `properties` rename first
   ([flatten-provider-resource-module-properties.md](flatten-provider-resource-module-properties.md)) —
   this design's payload shape assumes it.
2. Implement the new projection behind nothing — there is no flag, no
   dual-write, no compatibility mode. A compatibility mode would mean
   maintaining *both* shapes, which is strictly worse than the status
   quo and defeats the point of taking the break.
3. Bump the alpha version with a loud, explicit breaking-change note.
4. Rewrite `cfg-int-deployment`'s `customer/terraform`, `spoke/terraform`
   (and any sibling roots) against the new variable shape.
5. Rewrite `haven`'s equivalent roots.
6. Delete the now-dead machinery: `_REAL_VARIABLE_NAME`,
   `FLAT_CATEGORIES`, `planned_files()`'s per-category filename logic,
   `resolve_deploy_time_files()`'s per-category taint branches, the
   `resx_*` unconditional-blanking special case.

**No deprecation period**, deliberately. With two known consumers and a
pre-1.0 version, a clean break is cheaper for everyone than a
transitional shape nobody wants to live in.

## Open questions

- **Type safety**: answered in full — see "Type design" above
  (recommendation: strata generates `strata_variables.tf`, consumer can
  opt out by declaring its own `variable "strata"`). `optional()` is
  **resolved — allowed** (real binary verified at v1.12.2, floor is 1.3).
  Two sub-questions remain inside that section: whether `sensitive =
  true` on `strata_secrets` hurts plan output, and whether the
  projection always emits every top-level key (which would let the
  generated type stay strict).
- **The silent-`null` risk**: **resolved** — see "Secret delivery, and
  why the obvious helper is wrong" above. The weakness was manufactured
  by the first draft's own `try()`-based helper, not inherent: writing a
  sentinel instead of omitting the key, plus generating the reassembly
  into `strata_locals.tf`, removes both the `try()` and the per-use-site
  helper entirely. Remaining sub-question: should `build run` also
  *reject* `var.strata.` references found in copied `.tf` sources (it
  already scans them for the `variable "strata"` opt-out check), or just
  document the rule?
- **Does anything need the per-resource-type file split for a real
  reason?** **Resolved — no, and it is worse than redundant.** Checked
  against both consumers (2026-10-09): `cfg-int-deployment` has *zero*
  `Resource` documents and never references `resources` in any `.tf`
  file; `haven` declares a single flat `variable "resources"` and
  immediately re-merges what the split separated, filtering by the
  type field inside each entry. Neither depends on the split, and the
  split actively loses data — see "Resources are one flat map" and
  problem #3 above.
- **`modules` is now projected** (new in this design — see "Projecting
  `modules`" below). The old "it's absent" caveat is gone: leaving one
  kind arbitrarily missing from an otherwise-uniform `strata` object was
  exactly the kind of special case this design exists to delete.

## Remaining Work

- [x] Confirm with both consumers that nothing depends on the
      `resx_<type>` file split for a real reason. **Done 2026-10-09 —
      neither does, and the split is actively lossy. See "Resources are
      one flat map".**
- [ ] Resolve the two remaining sub-questions under "Type design"
      (`sensitive = true` plan-output impact, strict-vs-`optional()` top
      level). `optional()` itself is resolved — allowed.
- [ ] Decide whether strata emits its own `terraform { required_version }`
      block or only documents the ≥ 1.3 requirement (leaning: document
      only, a duplicate block is a hard error).
- [ ] Decide whether `build run` rejects `var.strata.` references in
      copied `.tf` sources, or only documents the `local.strata.*` rule.
- [ ] Pick the exact sentinel prefix (`__strata_secret__:` is a
      placeholder) — must be something no real value could collide with.
- [ ] Confirm `mounts`/`checks`/`endpoints` are the right `modules`
      fields to project (see "Projecting `modules`") — `services` is
      deliberately excluded; drop the other three too if no consumer
      wants them.
- [ ] Write the ADR (this is a breaking schema/output change; it needs
      one before implementation).
- [ ] Implement: new `terraform_projection.py` shape, new
      `planned_files()`/`resolve_deploy_time_files()`, the
      `strata_variables.tf` **and `strata_locals.tf`** generators, delete
      the dead machinery listed under Migration.
- [ ] Rewrite both consumers' `.tf` roots.

## Changelog

- 2026-10-09: Created — promoted from
  [flat-category-secret-taint-size-limit.md](flat-category-secret-taint-size-limit.md)'s
  "Clean sheet" appendix into a full, standalone design after confirming
  the break is affordable (two consumers, both absorbable, alpha). That
  doc chased a per-category patch for the secret-taint gap; this one
  deletes the gap structurally by replacing the category taxonomy it
  came from. Accepted explicitly as breaking-everything-and-fixing-
  everything, with no compatibility mode.
- 2026-10-09: Added the **"Type design"** section — the one-variable
  shape's biggest objection is "what *is* that variable?", so `type =
  any` needed a real answer rather than a shrug. Recommends strata
  generating `strata_variables.tf` (a real `object({...})`, regenerated
  every build so it can't drift, consumer opts out by declaring its own
  `variable "strata"`), with `any` kept only for the genuinely opaque
  `configuration`/`custom`/`properties` passthroughs — where it's the
  correct type, not a cop-out.
- 2026-10-09: **Resolved** the `optional()` sub-question — allowed. The
  ≥ 1.3 floor is cleared by the real binary this mechanism was already
  verified against (v1.12.2, per ADR-0031). Corrected a speculative
  claim in the process: the earlier "v1 already asserted `~> 1.9`" note
  was wrong — that string is only an *example* in
  `IntegrationSpecModel.version`'s docstring, not a real assertion
  anywhere; `cfg-int-deployment`'s own `integrations/terraform.yaml`
  declares no version at all. Added the `required_version` caveat and
  the `optional(type, default)` note.
- 2026-10-09: **Resolved the silent-`null` risk** (was cost #4, the
  design's one acknowledged weak point) — and found it was
  self-inflicted. Tracing it properly showed the first draft's own
  `try()`-based helper *created* the failure mode: omitting the secret
  key makes any fallback expression invalid (`lookup()` evaluates all
  arguments eagerly), which forced the `try()`, which then swallowed the
  very error that would have warned you. Two changes remove it entirely:
  (1) `deploy run` writes a **sentinel** (`"__strata_secret__:<path>"`)
  instead of omitting the key, keeping the shape stable in both phases so
  no `try()` and no `optional()` is ever needed for secret fields; (2)
  `build run` generates **`strata_locals.tf`**, doing the reassembly
  merge itself, so the consumer rule collapses to "read `local.strata.*`,
  never `var.strata.*`" — secrets and non-secrets read identically, with
  zero per-secret knowledge in any module. Also confirmed `.tfvars.json`
  values are literal (Terraform applies no `${}` interpolation), per
  ADR-0031's own account of the stale unresolved file being read as-is.
- 2026-10-09: **Added `modules` to the projection** — the last remaining
  "this kind is just missing" caveat. The original reason for skipping it
  (no proven demand, and it would have meant another file + variable +
  rename-table entry + delivery rule) was sound for a per-category
  design, but costs literally one nested key here — and omitting it
  would itself have become the special case this whole design exists to
  delete. Projects the flattened `ModuleSpecModel` fields; deliberately
  excludes `services` (workload-builder internals) and does not change
  the Helm/Compose pipeline at all — this is a read-only projection
  alongside it, not a reroute through Terraform.
- 2026-10-09: **Ran the `resx_<type>` consumer check — and it came back
  worse than expected.** The open question assumed the split was
  harmless v1 parity awaiting a confirmation; it is a live, silent
  data-loss bug. Terraform does not merge multiple `*.auto.tfvars.json`
  files that set the same variable — it processes them in lexical order
  and the last one wins outright, with no warning. Verified empirically
  (Terraform v1.12.2) against strata-v2's **own** `config/` dogfood
  build output, which emits four `resx_*` files: a standalone
  `terraform plan` sees 1 of 4 resources (only `web-vm`, because
  `resx_virtual_machine` sorts last). Masked at deploy time by the
  blanking + merged-`TF_VAR_resources` workaround, but not on the
  standalone-plan path ADR-0022 requires to work. Strengthened problem
  #3 with this evidence.
- 2026-10-09: **Flattened `strata.resources`** from nested-by-type
  (`resources.<type>.<name>`) to a flat name-keyed map
  (`resources.<name>`), as a direct result of the same check. The
  nesting was a reflex inherited from the `resx_<type>` filenames, not a
  decision. `cfg-int-deployment` has zero `Resource` documents and never
  references `resources` in any `.tf`; `haven` declares one flat
  `variable "resources"` and *re-merges* what the split separated,
  filtering on the type field already present inside each entry — so the
  only real consumer actively works around the nesting. Safe by
  construction: `WorkspaceSpecModel` already enforces globally unique
  resource names. Also makes resources the cheapest part of the
  migration (`var.resources` → `local.strata.resources`, a pure rename).
