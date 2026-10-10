# Unified Terraform Projection — One Variable, One Secrets Map

- Status: **proposed, full design** — deliberately breaking, accepted as such
- Last updated: 2026-10-10
- Decision recorded: [ADR-0035](../decisions/0035-unified-terraform-projection.md)
  (the decision itself; this doc remains the live evidence record, full
  payload shape, migration plan, and open implementation questions)
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
      "description": "",
      "tags": [],
      "deployment": "prd-deploy",
      "deployment_version": "1.0.0",
      "deployment_description": "",
      "deployment_tags": [],
      "environment": "prd",
      "platform_version": "strata.omp.com/v2",
      "labels": {},
      "configuration": {},
      "custom": {}
    },
    "tenant": {
      "code": "c0062",
      "name": "GSK",
      "geographies": ["europe"],
      "onboarded": "2026-03-15",
      "configuration": { "dns_slug": "gsk" },
      "custom": {}
    },
    "providers": {
      "azure":    { "type": "azurerm",  "region": "westeurope", "configuration": {}, "custom": {}, "default_tags": {} },
      "kamatera": { "type": "kamatera", "region": "eu",         "configuration": {}, "custom": {}, "default_tags": {} }
    },
    "resources": {
      "web-vm": {
        "provider_type": "azure",
        "resource_type": "virtual_machine",
        "configuration": {},
        "custom": {},
        "default_tags": {},
        "custom_tags": {},
        "firewalls": [],
        "subnet": null,
        "storage": {
          "install_path": "/opt/app",
          "disks": [{ "name": "os", "size": 64, "label": "os-disk", "mount": "/" }],
          "volumes": [],
          "parameters": {}
        }
      },
      "web-storage": {
        "provider_type": "azure",
        "resource_type": "storage_account",
        "configuration": {},
        "custom": {},
        "default_tags": {},
        "custom_tags": {},
        "firewalls": [],
        "subnet": null,
        "storage": null
      },
      "edge-waf": {
        "provider_type": "azure",
        "resource_type": "application_gateway",
        "configuration": {},
        "custom": {},
        "default_tags": {},
        "custom_tags": {},
        "firewalls": [],
        "subnet": null,
        "storage": null
      }
    },
    "namespaces": {
      "hearth": {
        "modules": ["authentik"],
        "configuration": {},
        "custom": {},
        "default_labels": {},
        "custom_labels": {}
      }
    },
    "modules": {
      "authentik": {
        "type": "helm",
        "source": { "...": "..." },
        "release_name": "authentik",
        "kubernetes_namespace": "hearth",
        "properties": {
          "mounts":    [{ "name": "media", "target_path": "/media", "storage_class": "fast-ssd" }],
          "checks":    [{ "name": "http", "type": "http", "endpoint": "/healthz" }],
          "endpoints": [{ "name": "web", "type": "http", "port": 9000 }]
        },
        "configuration":  {},
        "custom":         {},
        "default_labels": {},
        "custom_labels":  {}
      }
    },
    "dns":        { "example-zone": { "provider": "azure", "zones": {} } },
    "networks":   { "spoke-net": { "networks": {} } },
    "firewalls":  { "edge-fw": { "allow": [], "deny": [] } },
    "topologies": { "main": { "type": "kubernetes", "components": [], "namespaces": [] } },
    "properties": { "deploy_tier": "standard" },
    "custom":     { "cost_center": "1234" },
    "flags":      { "enable_x": true },
    "variables":  { "customer_code": "c0062" },
    "default_tags": { "managed-by": "strata" }
  }
}
```

`strata.default_tags` is the **solution-wide** tag baseline — hoisted out
of `workspace` to sit alongside `properties`/`custom`/`flags`/`variables`
rather than nested under it, since `WorkspaceSpecModel.default_tags` was
already documented as cross-cutting ("applied on top of/underneath *each
Provider's* own default_tags"), not workspace-scoped data. Every other
`default_tags` in the structure (`providers.<name>.default_tags`,
`resources.<name>.default_tags`, each `dns`/`networks`/`firewalls` entry's
own) stays exactly where it is — those are genuinely per-entity baselines,
a different concept from the one solution-wide default every entity
starts from.

`topologies.main.namespaces` (the `[]` above) is a real field —
`TopologySpecModel.namespaces: list[TopologyNamespaceReferenceModel] |
None` — but, like `resources.custom` above, found 2026-10-09 to be
validated and never read by today's `_build_topologies_payload()`
(returns only `type`/`components`/`volumes`). Tracked as another
unprojected-gap fix below, same class as `storage`/`custom`.

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
    })

    tenant = object({
      code          = string
      name          = string
      geographies   = optional(list(string))  # TenantSpecModel.geographies is Optional — "no data-residency constraint" is the common case
      onboarded     = optional(string)        # TenantSpecModel.onboarded is Optional
      configuration = any
      custom        = any
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
      # ResourceSpecModel.custom — found 2026-10-09 (via the example JSON): a real field
      # ("Custom user-defined data for scripts or extensions") but, like storage below,
      # never read by today's _merge_resource_entry() at all. No default_labels/custom_labels
      # here — ResourceSpecModel has no such fields at all (only default_tags/custom_tags,
      # cloud tags; labels are a Namespace/Module-only concept, see those models' own comments).
      custom        = any
      default_tags  = map(string)
      custom_tags   = map(string)
      firewalls     = list(string)
      subnet        = optional(any)
      # ResourceSpecModel.storage — found 2026-10-09: validated/round-trip-tested but never read by
      # today's _merge_resource_entry() at all, despite being exactly the shape Terraform needs to
      # actually create a VM's disks (azurerm_managed_disk/data_disk_attachment or equivalent).
      storage = optional(object({
        install_path = optional(string)
        disks = optional(list(object({
          name  = optional(string)
          size  = number
          label = string
          mount = string
        })))
        volumes = optional(list(object({
          name = optional(string)
          path = string
        })))
        parameters = optional(any)
      }))
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
      # mounts/checks/endpoints are NOT top-level ModuleSpecModel fields — they live under
      # ModuleSpecModel.properties (ModulePropertiesModel | None), hence nested + optional here.
      properties = optional(object({
        mounts    = optional(list(any))
        checks    = optional(list(any))
        endpoints = optional(list(any))
      }))
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
    default_tags = map(string)  # solution-wide baseline, hoisted out of workspace — see note above
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
- **`variables = map(string)` is likely wrong.** `ResolvedWorkspaceGraph.
  variable_refs` is `list[ValueReference]`, and `ValueReference.value: Any`
  (`resolved_context.py`) — not string-constrained. `_build_variables_payload()`
  passes `ref.value` through unmodified ("native-typed where
  `build_value_references()` could tell", per its own docstring). A
  variable resolved to a bool/int/list would violate a strict
  `map(string)` declaration. Needs either confirming `build_value_references()`
  always coerces to string before this point, or loosening the generated
  type to `map(any)`.
- **`map(string)`-typed `labels` fields are unverified, workspace-wide.**
  Every `*MetaModel.labels` field (`WorkspaceMetaModel`, `ProviderMetaModel`,
  `NamespaceMetaModel`, etc.) is typed `dict[str, Any]` in Pydantic, not
  `dict[str, str]` — nothing stops a label value from being a bool/number/
  nested dict. The generated `object({...})`'s `labels = map(string)`
  entries (workspace, providers, namespaces, ...) are optimistic, not
  verified against the schema. Worth a real check of whether any
  consumer's YAML actually puts a non-string label value, or whether to
  just type these `map(any)` too.

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

### Why `ProvisionerModel` itself stays out entirely

Every category projected so far (`workspace`/`tenant`/`providers`/
`resources`/`namespaces`/`modules`/`dns`/`networks`/`firewalls`/
`topologies`) describes the **target infrastructure** — data the copied
`.tf` code reads. `ProvisionerModel` (`name`, `description`, `tool`,
`source`, `backend`, `properties`, `gitops`, `configuration`,
`integration`, `output`, `depends_on`) describes something else entirely:
**how strata drives the tool that reads that data** — and belongs on the
opposite side of the build/deploy boundary from everything else here, for
reasons already decided elsewhere, not new ones invented for this design:

- **`source`** tells `build run` *where to copy the `.tf` code from*
  (ADR-0022/ADR-0025) — consumed by `prepare()` before any projection
  exists, never by the `.tf` code itself.
- **`backend`** is deliberately **never** delivered via the tfvars/env-var
  surface at all — ADR-0023 D2 resolves its tokens separately and hands
  the result straight to `integration.init(path, backend_config=...)`
  (real Terraform `-backend-config`), "never written back into
  [`prepare()`'s] output." Folding it into `strata.*` would mean a second,
  redundant delivery path for the same data.
- **`integration`** binds a provisioner to an `Integration` document
  (version/transport/auth, ADR-0021 D4) — strata's own plumbing concern,
  meaningless to a `.tf` file.
- **`output.template`** (ADR-0023 D3) *replaces* the default projection
  for a provisioner that opts in — it's a control knob over whether/how
  this whole design's output gets generated, not data this design's
  output would itself carry.
- **`properties`/`gitops`** are tool-specific (ansible/sync-only) and
  structurally can't apply to a Terraform root at all (`validate_properties_
  only_for_ansible`/`validate_gitops_only_for_sync_tools` reject them for
  `tool: terraform`).
- **`depends_on`** orders provisioner *source materialisation* at build
  time (ADR-0029) — a build-sequencing concern, not workspace data.
- **One more structural reason, not just "wrong layer":** a workspace can
  declare *several* Terraform provisioners (e.g. `cfg-int-deployment`'s
  `customer/terraform` and `spoke/terraform` roots), each with its own
  `source`/`backend`/`configuration`. `strata.*` is one shared object for
  the whole workspace; there is no single answer for "the" provisioner's
  config to put there even if it belonged.

Confirmed, not assumed: `build_platform_projection(graph, provisioner)`
already takes a `provisioner` argument today and immediately `del`s it —
its own docstring says so explicitly ("`provisioner` is currently unused
by any category built so far"). Nothing in this design changes that;
`ProvisionerModel`'s fields reach Terraform through `prepare()`/`init()`,
never through `strata.*`.

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

## Implementation Plan

Eight phases inside this repo (Migration steps 4–5, rewriting the two
consumers' `.tf` roots, happen in their own repos afterward — step 9
below only tracks that it must happen, not how). Each phase is a small,
independently mergeable, fully-tested change, per this repo's own
"small, tested, documented phases" convention — never a big-bang rewrite
landed in one commit.

**Resolved (2026-10-10), not just left open — checking the real
mechanisms killed the "generalize claiming" option.** Initial framing
below treated this as one open question with three candidate answers;
tracing the actual code found **two separate claiming mechanisms**, and
checking each one against the categories candidate (c) proposed
generalizing to closed the question outright.

- **Mechanism 1 — validate-time acceptance**
  (`WorkspaceService.claimed_document_names()`, `workspace_service.py`):
  covers `dns_zones ∪ networks ∪ firewalls ∪ namespaces` — decides
  whether a `${output:...}` token is *accepted* at all. Deliberately
  excludes `resources`, with a reason stated in its own docstring: "a
  Resource document has no Value-token-bearing field of its own that
  `${output:...}` could ever appear in." Not an oversight — a considered
  exclusion.
- **Mechanism 2 — deploy-time resolution scoping**
  (`deploy_controller.py`'s `claimed_by_category`/`_contains_output_token()`):
  narrower still — only `dns`/`networks`/`firewalls`. Its own comment
  states the two mechanisms were **deliberately never unified**.
- **Modules already have real, independent per-step output access** —
  confirmed in `docs/work/gap_fit_v1.md`'s gap #12 investigation:
  Helm/Compose's `deploy_namespace()` does its own
  `tokens = {**resolved.values, **visible_outputs}` merge, entirely
  separate from Terraform's TF_VAR delivery ("modules (namespace-owned)
  are already safe"). Generalizing *this* redesign's claiming to modules
  would be solving an already-solved problem via the wrong mechanism.
- **Topologies were never in the claimable vocabulary at all** — not a
  `targets`-able kind, so there's no existing "exclusion" to even
  reconsider there.

So candidate (c) wasn't "nearly free," it was three different things
wearing one label: modules need nothing from here (already solved
elsewhere), resources were already deliberately excluded by a considered
decision (overturning it needs new evidence, the same bar gap #12 itself
was held to — not assumed free just because the new shape makes the
*mechanical* check cheaper), and topologies were never a candidate to
begin with.

**Decision: (b).** Preserve today's exact scope — only `dns`/`networks`/
`firewalls` are claim-scoped, same historic three categories, inside the
new single-`strata`-object shape (with the per-provisioner-source-
directory correction below applied). No generalization.

(a) (drop per-step scoping entirely) was never seriously in the running —
it's a real behavior regression (an unclaimed step would start seeing
documents it has no business seeing), not a simplification. (c)
(generalize to every category) is rejected per the three findings above:
there's nothing left to generalize *to* once modules (already solved) and
topologies (never applicable) are set aside, and extending it to
resources specifically would mean reversing a considered decision with no
new evidence — exactly the bar this repo's own "evidence over assumption"
convention holds every other extension to.

**Correction (2026-10-10): it is not "one directory per step."** Both
`build_controller.py` and `deploy_controller.py` key the build directory
by the **provisioner's own source location**
(`provisioner.source.target_path or .source_path`), not by `step.name` —
that fallback only applies to a source-less sync/GitOps provisioner.
`deploy_controller.py`'s own comment states this explicitly: "a
provisioner with a `source` is materialised once, keyed by its *source*
location, not by whichever step happens to reference it (two steps can
share one provisioner/source...)." So two execution steps that both
reference the same Terraform-root provisioner (different `targets`,
same `provisioner:`) write into the **same** directory and would share
the **same** `strata.auto.tfvars.json` — not one each. Decision (b)
above needs to account for this: "per-step" scoping is really
"per-provisioner-source-directory" scoping, and a directory can be
written to by more than one step's claims in the same `deploy run`.

### Phase 1 — `modules` on `ResolvedWorkspaceGraph`

**Design:** add `modules: dict[str, ModuleModel] = field(default_factory=dict)`
to `ResolvedWorkspaceGraph` (`resolved_context.py`), populated in
`build_resolved_workspace_graph()` (`build_controller.py`) the same way
every other category already is: collect every `ModuleReferenceModel.module`
name referenced by `workspace.spec.namespaces`' resolved `NamespaceModel.
spec.modules`, then `_lookup_all(index, PlatformKind.MODULE, module_names)`.
Namespaces must already be resolved first (the existing `_lookup_all` call
for `namespaces` stays before this one in the same function).

Zero behavior change to any existing output — this phase only makes the
data available on the graph; no builder reads it yet.

**Tests:** extend `build_controller.py`'s existing graph-construction
tests with a workspace whose namespace references two modules (one
referenced twice under different names, to confirm dict-by-name
dedup/uniqueness doesn't break), asserting `graph.modules` contains the
resolved `ModuleModel` instances.

### Phase 2 — The new payload assembler

**Design:** add `build_strata_payload(graph: ResolvedWorkspaceGraph) -> dict[str, Any]`
to `terraform_projection.py`, replacing `build_platform_projection()`.
Keep one private helper per category for readability
(`_strata_workspace()`, `_strata_tenant()`, `_strata_providers()`,
`_strata_resources()`, `_strata_namespaces()`, `_strata_dns()`,
`_strata_networks()`, `_strata_firewalls()`, `_strata_topologies()`,
`_strata_flags()`, `_strata_variables()`, `_strata_properties()`,
`_strata_custom()`, `_strata_default_tags()`) but each one returns its
*final* nested shape directly — no more "flat dict today, wrapped later
by `planned_files()`" split, since there is only one file to wrap into
now. `build_strata_payload()` itself just assembles the top-level dict
from these fourteen calls.

Folded into this phase, not patched into the old functions first and
then discarded: the three pre-existing projection gaps found during
review (`resources.storage`, `resources.custom`, `topologies.namespaces`)
land directly in the new `_strata_resources()`/`_strata_topologies()`
from day one. `modules` is new (`_strata_modules()`, reading `graph.modules`
from Phase 1, nesting `mounts`/`checks`/`endpoints` under `properties`
per the model's real shape). `default_tags` becomes its own top-level
call reading `graph.workspace.spec.default_tags`, not nested under
`_strata_workspace()`.

Every per-entry `configuration`/`custom` stays a raw, unresolved value at
this stage (tokens literal) — this function is `build run`-time only,
matching `build_platform_projection()`'s existing contract; resolution
happens in Phase 3.

Delete in this same phase (no longer called by anything once this
lands): `build_platform_projection()`, `build_configuration_payloads()`,
`build_dns_networks_firewalls_payloads()`, `_REAL_VARIABLE_NAME`,
`FLAT_CATEGORIES`, `real_variable_name()`. `resolved_context.py` unaffected.

**Tests:** port every existing `test_integrations_terraform_projection.py`
category test to assert against the new nested shape instead of the old
flat/wrapped one; add new tests for the three folded-in gap fixes and for
`modules`/`default_tags`; one full-shape test against a graph built from
`config/`'s real dogfood workspace, asserting the complete `strata` object
matches field-for-field what this design doc's own example documents.

### Phase 3 — Secret scanning and the deploy-time rewrite

**Design:** two new functions, replacing `resolve_deploy_time_files()`
entirely:

- `find_secret_paths(payload: dict) -> list[str]` — walks the *build-time*
  (token-literal) payload and returns every dotted path whose leaf value
  is `${secret:...}`-shaped, using the same walk
  `resolve_value_tokens_tracking_secrets()` already does internally (reuse
  that primitive's path-tracking rather than re-implementing it) but
  without needing live token values yet — needed at *build* time by
  Phase 6 (which paths get a merge in `strata_locals.tf`), independent of
  whether a real secret value is available yet.
- `rewrite_strata_payload(payload: dict, tokens: dict[str, str]) -> tuple[dict, dict[str, str]]` —
  the `deploy run`-time counterpart. Resolves every token in `payload`
  (reusing `resolve_value_tokens_tracking_secrets()` exactly as today),
  then walks the result once more: every secret-shaped leaf is replaced
  in-place with the sentinel string `f"__strata_secret__:{path}"`, and
  collected separately into a flat `{path: resolved_value}` dict (never
  written back into `payload`) for `TF_VAR_strata_secrets`. Returns both —
  the sentinel-substituted payload (for `strata.auto.tfvars.json`) and the
  flat secrets dict (for the env var).

No category branching of any kind (unlike `resolve_deploy_time_files()`'s
`FLAT_CATEGORIES`-vs-everything-else split) — one shape, one walk, because
there is only one object now.

**Related, not required for this phase:** the `${output:...}`-sensitivity
gap under Remaining Work (an `${output:}` token is never secret-shaped
regardless of whether the referenced Terraform output is itself
`sensitive = true`) lives in the same taint-detection primitive
(`resolve_value_tokens_tracking_secrets()`'s `_is_secret_shaped()`) this
phase reuses. Worth fixing in the same pass if the `tokens` dict gains
per-key sensitivity metadata before this phase lands — not a blocker,
since it's a pre-existing bug independent of this redesign either way.

**Tests:** unit tests on a synthetic nested payload with secrets at
multiple depths (1-level, e.g. `variables.db_password`; 3-level, e.g.
`providers.kamatera.configuration.api_token`) confirming: the sentinel
appears at the secret's path in the rewritten payload; the real resolved
value appears only in the returned secrets dict, never in the payload;
a non-secret sibling value is untouched. One real-terraform regression
test (same pattern as `test_terraform_variable_precedence.py`) proving a
real `terraform plan`'s effective value for a secret-bearing field is the
resolved value, not the sentinel.

### Phase 4 — One file, one `default_output()`

**Design:** replace `planned_files()` with a function returning exactly
one `(filename, data)` pair: `("strata.auto.tfvars.json", {"strata":
build_strata_payload(graph)})`. Update `TerraformIntegration.default_output()`
(`terraform.py`) to return this single file instead of iterating
`planned_files()`'s multi-file list. No other `InfraIntegration` subclass
is touched (`AnsibleIntegration`/`BicepIntegration` don't call any of this
module's functions).

**Tests:** update `test_integrations_terraform.py`'s `default_output()`
assertions to expect one file; confirm no other integration's tests
changed.

### Phase 5 — `strata_variables.tf`: a static template, not a generator

**Design — the one simplification worth calling out explicitly:** the
`object({...})` *type* shape is identical for every workspace (only the
map *values* vary at runtime — every workspace's `strata` object has the
same top-level keys and the same per-category field set, by construction,
since it's derived from strata's own fixed model schema, not from
workspace content). So `strata_variables.tf` does **not** need to be
generated from `ResolvedWorkspaceGraph` at all — it's a static asset,
written once as a template file (e.g.
`strata/templates/terraform/strata_variables.tf`, alongside the existing
`strata/templates/examples/` convention), and `default_output()` copies it
verbatim on every build. The only per-build decision is binary: skip
writing it if the copied `.tf` sources already declare their own
`variable "strata"` (extending the existing source-scan check that
already looks for this kind of opt-out).

Resolve the two still-open type sub-questions as part of authoring this
template, not deferred further: test `sensitive = true` on
`strata_secrets` against a real root's `terraform plan` output for
redaction side effects before committing to it; type `variables`/every
`labels` field `map(any)` rather than `map(string)` per the review
finding (`ValueReference.value`/`*MetaModel.labels` are `Any`-typed in the
real models, not string-constrained).

**Tests:** a real-terraform test that `terraform validate`s a minimal root
declaring no `variable "strata"` of its own, confirming strata's copied
template is both present and syntactically valid against a real
Terraform ≥ 1.3 binary; a second case confirming the file is *not*
written when the root already declares its own `variable "strata"`.

### Phase 6 — `strata_locals.tf`: genuinely generated

**Design:** unlike Phase 5, this file's content is workspace-specific —
it depends on exactly which dotted paths (from Phase 3's
`find_secret_paths()`) this workspace's documents actually have a
`${secret:}` token at. New function `build_strata_locals(secret_paths:
list[str]) -> str` emits the HCL text directly (template string, not a
third-party HCL-writing library — matches this module's existing
"functions returning strings/dicts" convention, nothing here needs HCL
AST manipulation). For each path, builds the nested `merge()` chain shown
in this design doc's own "Secret delivery" section; a workspace with an
empty `secret_paths` list gets the one-line fallback
`locals { strata = var.strata }`.

**Tests:** paths sharing a common parent (two secrets under
`providers.kamatera.*`) must merge into one nested `merge()` call per
parent, not two independent top-level merges that would clobber each
other; a real-terraform test confirming `local.strata.<path>` evaluates
to the real resolved value for a secret path and an ordinary literal
value for everything else, in the same `terraform plan`.

### Phase 7 — `deploy_controller.py` rewiring

**Design:** per the resolved decision above (option (b) — preserve
today's exact `dns`/`networks`/`firewalls`-only claim scope, now keyed by
provisioner source directory rather than step name), replace the entire
existing per-category `TF_VAR_<name>` delivery block and the
`resolve_deploy_time_files()` call with: (1) call `rewrite_strata_payload()`
(Phase 3) and overwrite `strata.auto.tfvars.json` in the step's build
directory; (2) set exactly one environment variable,
`{integration.ENV_VAR_PREFIX}strata_secrets = json.dumps(secrets_dict)`.
Delete the `FLAT_CATEGORIES`/`resx_`-prefix branching entirely — there is
no longer a case split to make.

**Tests:** port the existing real-terraform deploy-time precedence tests
(`test_terraform_variable_precedence.py`) to the new single-file/
single-env-var shape; confirm a secret's resolved value wins in a real
`terraform plan` and that the on-disk file never contains it (direct text
scan of the rewritten file, matching this repo's existing convention for
this class of test).

### Phase 8 — Delete the rest of the dead machinery, bump version

**Design:** sweep for any remaining reference to deleted names
(`_REAL_VARIABLE_NAME`, `FLAT_CATEGORIES`, `real_variable_name`,
`planned_files`, `resolve_deploy_time_files`, `build_platform_projection`,
`build_configuration_payloads`, `build_dns_networks_firewalls_payloads`)
across `src/` and `tests/` — comments/docstrings included, not just live
code (the precedence-fix work already found that a path/name sweep misses
line-wrapped occurrences; grep tolerant of whitespace/newlines between
fragments, not just the literal joined string). Bump `VERSION.txt` with
an explicit breaking-change note per Migration step 3.

**Tests:** full suite (`mypy`, `ruff`, `pytest`, `lint-imports`) green;
no remaining reference to any deleted symbol anywhere in the repo.

### Phase 9 — Consumer migration (tracked here, executed elsewhere)

Rewrite `cfg-int-deployment`'s and `haven`'s `.tf` roots against the new
variable shape (Migration steps 4–5) — happens in those repos, after
Phase 8 ships a real alpha release they can pin to. Not part of this
repo's own test suite; tracked here only so it isn't forgotten.

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
      wants them. **Note (2026-10-09 review): they must be projected nested
      under `properties` (`ModuleSpecModel.properties: ModulePropertiesModel
      | None`), not as flat siblings of `type`/`source` — they are not
      top-level `ModuleSpecModel` fields.**
- [ ] **Add a `modules: dict[str, ModuleModel]` to `ResolvedWorkspaceGraph`**
      (2026-10-09 review finding) — unlike `providers`/`resources`/
      `namespaces`/`firewalls`/`dns`/`networks`, there is currently no
      resolved-modules collection at all on the graph. Projecting `modules`
      needs a real graph-resolution step (gathering the `Module` documents
      referenced by `NamespaceSpecModel.modules`), not just a new
      `_build_modules_payload()` function — the data isn't in the graph yet.
- [ ] Resolve the `variables = map(string)` / `labels = map(string)` type
      mismatches (2026-10-09 review finding, see "Type design" sub-questions)
      — `ValueReference.value` and every `*MetaModel.labels` value are
      typed `Any` in the real models, not guaranteed strings.
- [ ] Implement the `strata.default_tags` hoist — `_build_workspace_payload()`
      currently nests `WorkspaceSpecModel.default_tags` under `workspace`;
      the new top-level `default_tags` category needs its own builder
      (reads the same field, just placed at the top of the payload).
- [ ] **Add `storage` to the `resources` projection** (2026-10-09 review
      finding — pre-existing bug, not specific to this redesign):
      `ResourceSpecModel.storage` (`disks`/`volumes`/`parameters`) is
      validated and round-trip tested but `_merge_resource_entry()` never
      reads it — a VM's disks silently never reach any `.tf` artifact
      today. Same gap-#17 class as the old `ProviderSpecModel.configuration`
      bug, just never extended to this field. Fix applies to the *current*
      `terraform_projection.py` independent of whether this redesign ships
      — worth doing now rather than waiting.
- [ ] **Add `custom` to the `resources` projection** (2026-10-09 review
      finding, same pre-existing-bug class as `storage` above):
      `ResourceSpecModel.custom` is a real, documented field but
      `_merge_resource_entry()` only ever merges `configuration` — `custom`
      is silently dropped. (`default_labels`/`custom_labels` do **not**
      apply here — `ResourceSpecModel` has no such fields at all; that was
      an incorrect addition to the example, reverted.)
- [ ] **Add `namespaces` to the `topologies` projection** (2026-10-09
      review finding, same class): `TopologySpecModel.namespaces` is a
      real, validated field but `_build_topologies_payload()` only returns
      `type`/`components`/`volumes` — the namespace references are
      silently dropped.
- [ ] **Fix: `${output:...}` is never treated as secret-shaped, regardless
      of Terraform's own `sensitive = true` on the referenced output**
      (2026-10-10 review finding — real, currently-live security gap,
      independent of this redesign). `resolve_value_tokens_tracking_secrets()`'s
      `_is_secret_shaped()` checks only the token's *kind*
      (`kind == "secret"`); an `${output:step.key}` token is never
      secret-shaped by that check no matter what the referenced value
      actually is. `collect_step_outputs()` compounds this — it reads
      `entry["value"]` from `terraform output -json` and ignores
      `entry["sensitive"]` entirely, even though real Terraform always
      returns the true value (never redacted) in `-json` output alongside
      that flag. Net effect: a workspace author who marks a Terraform
      output `sensitive = true` and references it elsewhere via
      `${output:...}` gets that real value written straight into a plain
      on-disk `*.auto.tfvars.json` today — never routed through the
      `${secret:}`-only, env-var-safe delivery path. Fix needs to thread a
      `sensitive: bool` alongside each collected output through the flat
      `tokens: dict[str, str]` pipeline (which currently carries no
      per-key metadata at all) so `_is_secret_shaped()`/Phase 3's
      `find_secret_paths()` can treat it the same as a `${secret:}` leaf.
      Worth fixing on its own merits regardless of this redesign's fate.
- [ ] **Persist each step's collected outputs to disk** (2026-10-10
      review finding, same independent-of-this-redesign class): `deploy_
      controller.py`'s `step_outputs: dict[str, dict[str, str]]` is
      purely in-memory, rebuilt fresh every `deploy run` by calling
      `collect_step_outputs()` only for steps inside *this* invocation's
      `resolved_steps` loop. A scoped `deploy run` (the code already has a
      `scope` concept) that excludes an upstream dependency leaves that
      upstream's outputs completely unavailable to a dependent step this
      run, even though the upstream's real Terraform state/outputs still
      exist on disk and `terraform output -json` could be called against
      its directory on demand regardless of whether it ran this
      invocation. Confirmed there is already a dormant, designed-but-
      never-wired schema for roughly this:
      `audit_manifest_model.py`'s `outputs_artifact: ManifestOutputsReferenceModel
      | None` (path + sha256, pointing at a separate `DeploymentOutputsModel`
      file, per [docs/work/audit-trail.md](audit-trail.md)) — grep confirms
      zero real writer anywhere today. Wiring `collect_step_outputs()`'s
      result into that file would close both this gap and the dormant
      schema field in one change, and double as the audit trail it was
      originally designed for.
- [x] Write the ADR (this is a breaking schema/output change; it needs
      one before implementation). **Done 2026-10-10 —
      [ADR-0035](../decisions/0035-unified-terraform-projection.md).**
- [ ] Implement: new `terraform_projection.py` shape, new
      `planned_files()`/`resolve_deploy_time_files()`, the
      `strata_variables.tf` **and `strata_locals.tf`** generators, delete
      the dead machinery listed under Migration.
- [ ] Rewrite both consumers' `.tf` roots.

## Changelog

- 2026-10-10: **Resolved the per-step/per-directory claiming open
  question — decision (b), no generalization.** Tracing the real code
  found two separate mechanisms, not one (`WorkspaceService.
  claimed_document_names()` for validate-time `${output:}` acceptance —
  `dns_zones ∪ networks ∪ firewalls ∪ namespaces`, resources deliberately
  excluded by its own docstring; `deploy_controller.py`'s narrower
  `claimed_by_category` for deploy-time resolution — `dns`/`networks`/
  `firewalls` only, explicitly never unified with the first). Confirmed
  via `docs/work/gap_fit_v1.md`'s gap #12 that modules already have
  real, independent per-step output access through Helm/Compose's own
  `deploy_namespace()` merge — nothing to generalize there. Resources
  were already a considered exclusion, not an oversight; topologies were
  never in the claimable vocabulary at all. Net: candidate (c)
  ("generalize to every category, it's nearly free now") doesn't
  survive contact with the actual mechanisms — decided (b), preserve
  today's exact three-category scope, inside the new per-provisioner-
  source-directory keying.
- 2026-10-10: **Found two more real, currently-live gaps in the
  `${output:...}`/cross-step-output mechanism, both independent of this
  redesign, tracked under Remaining Work.** (1) Security: `${output:}` is
  never treated as secret-shaped regardless of whether the referenced
  Terraform output is itself `sensitive = true` —
  `resolve_value_tokens_tracking_secrets()`'s `_is_secret_shaped()`
  checks only the token's *kind*, and `collect_step_outputs()` ignores
  Terraform's own `"sensitive"` JSON metadata entirely, so a value an
  author explicitly marked sensitive can be written straight into a
  plain on-disk `*.auto.tfvars.json` today. (2) Scope: `step_outputs` is
  purely in-memory, rebuilt only for steps inside the *current*
  invocation's `resolved_steps` loop — a scoped `deploy run` that
  excludes an upstream dependency loses access to its outputs even
  though the real Terraform state backing them still exists on disk.
  Confirmed `audit_manifest_model.py` already has a dormant,
  designed-but-unwired `outputs_artifact` field for roughly this, with
  zero real writer today (`docs/work/audit-trail.md`). Both noted as
  worth fixing on their own merits, and cross-referenced from Phase 3
  (same taint-detection primitive) since fixing (1) there would be the
  natural place to pick it up.
- 2026-10-10: **Corrected a wrong assumption in the Phase 7 open
  question**: build directories are keyed by the provisioner's own
  source location (`target_path`/`source_path`), not by execution step
  name — `step.name` is only the fallback for a source-less sync/GitOps
  provisioner. Confirmed directly in `deploy_controller.py`'s own
  comment: two steps referencing the same provisioner share one
  directory, and therefore would share one `strata.auto.tfvars.json`.
  Reworded the three candidate answers and Phase 7 to account for
  this — "per-step" scoping is really "per-provisioner-source-directory"
  scoping, where more than one step's claims can land in the same file.
- 2026-10-10: **Added the "Implementation Plan" section** — eight
  in-repo phases (graph plumbing → new payload assembler → secret
  scanning/rewrite → single-file `default_output()` → the two generated
  `.tf` files → `deploy_controller.py` rewiring → dead-code deletion),
  plus a ninth tracking the two consumers' own migration. Grounded against
  the real call sites (`build_resolved_workspace_graph()`, `planned_files()`/
  `resolve_deploy_time_files()`, `deploy_controller.py`'s `TF_VAR_` loop),
  not just the target shape — each phase names exactly which existing
  functions it replaces or deletes. Surfaced one real open design question
  in the process, not previously addressed anywhere in this doc: what
  happens to today's per-step `dns`/`networks`/`firewalls` *claiming*
  scope (gap #12) once there's only one shared `strata` object — flagged
  as needing a decision before Phase 7, not assumed away. Also named the
  one implementation simplification worth calling out explicitly:
  `strata_variables.tf`'s type shape is identical for every workspace by
  construction, so it's a static template asset copied verbatim, not
  something generated from the resolved graph like `strata_locals.tf`
  genuinely is.
- 2026-10-10: **Wrote [ADR-0035](../decisions/0035-unified-terraform-projection.md)**,
  recording the core decision (one `strata` object, one flat secrets map,
  flat resources, sentinel-based secret delivery, strata-generated type)
  separately from this doc's ongoing evidence/migration/open-questions
  record — per this repo's ADR convention, the ADR captures the decision
  only; this doc keeps tracking everything still in motion.
- 2026-10-09: **Cleaned up the example payload after a manual edit
  introduced invalid JSON and two inaccurate fields.** Fixed a stray
  double comma and a trailing comma (both hard JSON syntax errors); removed
  `default_labels`/`custom_labels` from the `resources` entries —
  `ResourceSpecModel` has no such fields at all (that distinction is
  Namespace/Module-only; resources get `default_tags`/`custom_tags`
  instead, already present). Reformatted `resources`/`namespaces` to
  multi-line, matching the `modules` entry's existing style. Kept
  `resources.custom` and `topologies.main.namespaces` — both are real,
  validated model fields the manual edit happened to surface, and both
  turned out to be two more instances of the `storage` finding below:
  real fields the *current* projection silently never reads. Added both
  to Remaining Work.
- 2026-10-09: **Added `storage` to the `resources` projection** —
  `ResourceSpecModel.storage` (`disks[]`/`volumes[]`/`parameters`,
  `resource_model.py`) is exactly the shape Terraform needs to actually
  create a VM's disks (size/label/mount), but `_merge_resource_entry()`
  never reads it — validated and round-trip tested, silently dropped from
  every `.tf` artifact today. A pre-existing bug in the *current* code,
  not something this redesign introduces; added to both the example
  payload and the generated `resources` object type, and tracked under
  Remaining Work as a fix that applies regardless of whether this
  redesign ships.
- 2026-10-09: **Hoisted `workspace.default_tags` to a top-level
  `strata.default_tags`**, alongside `properties`/`custom`/`flags`/
  `variables`. It was never workspace-scoped data to begin with —
  `WorkspaceSpecModel.default_tags`'s own docstring already called it a
  "solution-wide baseline... applied on top of/underneath each Provider's
  own default_tags," i.e. a cross-cutting default every entity starts
  from, not an attribute *of* the workspace. Every other `default_tags`
  (`providers.<name>`, `resources.<name>`, each `dns`/`networks`/
  `firewalls` entry) stays nested where it is — those are genuinely
  per-entity baselines, not the solution-wide one.
- 2026-10-09: **Reviewed the proposed payload against the real models**
  (`workspace_model.py`, `tenant_model.py`, `module_model.py`, et al.) and
  `terraform_projection.py`/`resolved_context.py`. Found and fixed: the
  `workspace` example silently dropped the `metadata` fields
  (`deployment_version`/`*_description`/`*_tags`) the current code already
  emits; `tenant` was missing `custom` (a real `TenantSpecModel` field) and
  its generated type wrongly made `geographies`/`onboarded` non-optional
  despite both being documented as commonly absent; `modules`' `mounts`/
  `checks`/`endpoints` were modeled as flat siblings of `type`/`source` but
  are actually nested under `ModuleSpecModel.properties` (`ModulePropertiesModel
  | None`) — not top-level fields at all. Also surfaced two open gaps now
  tracked under Remaining Work: `ResolvedWorkspaceGraph` has no resolved
  `modules` collection yet (projecting modules needs a new resolution step,
  not just a new builder function), and `variables`/`labels` are typed `Any`
  in the real models, making the generated `map(string)` declarations
  unverified.
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
