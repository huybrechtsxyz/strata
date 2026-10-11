# Unified Terraform Projection: One `strata` Variable, One Secrets Map

- Status: accepted
- Date: 2026-10-10
- Related: [docs/design/unified-terraform-projection.md](../design/unified-terraform-projection.md)
  (the finished design — current payload shape, how it works end to end),
  [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md)
  (the v1-parity shape this decision replaces), [flat-category-secret-taint-size-limit.md](../work/flat-category-secret-taint-size-limit.md)
  (the per-category patch this decision supersedes structurally rather
  than extending), sequenced after [flatten-provider-resource-module-properties.md](../work/flatten-provider-resource-module-properties.md),
  ADR-0022 (build renders, deploy executes), ADR-0023 (build output
  rendering — `InfraIntegration.prepare()`/`default_output()`), ADR-0025
  (strata supplies input, not source rewriting — the rule this decision's
  two generated `.tf` files fall under), ADR-0031 (deploy-time tfvars file
  rewrite — the mechanism this decision's `deploy run` phase reuses)

## Context and Problem Statement

Strata's Terraform projection (`terraform_projection.py`) inherited v1's
shape wholesale: thirteen independently-named categories, each delivered
as its own `*.auto.tfvars.json` file and/or `TF_VAR_<name>` environment
variable, with no single consumer-facing contract describing what reaches
Terraform or how. This shape causes real, evidenced defects, not merely
inconsistency:

- **Three incompatible output shapes** coexist (wrapped collections,
  flat multi-variable bags, pure passthrough) — every consumer and every
  piece of strata's own delivery code has to know which category is which.
- **A hardcoded rename table** (`_REAL_VARIABLE_NAME`) maps three category
  names to different real Terraform variable names, for no reason a
  consumer can infer from the data.
- **`resx_<type>` silently loses data.** Every resource type's file
  declares the same Terraform variable (`resources`); Terraform processes
  `*.auto.tfvars.json` files in lexical order and the last one wins
  outright, with no warning. Verified empirically (Terraform v1.12.2)
  against strata-v2's own `config/` dogfood build: a standalone
  `terraform plan` saw 1 of 4 declared resources.
- **Secret taint is per-category, not per-value.** One `${secret:}`
  anywhere inside `providers` blanks the entire `platform_providers`
  variable, not just the one provider that holds the secret.
- **The env-var delivery channel is sized for a scalar** but can be
  handed an arbitrarily large structure that merely contains a secret,
  with no check against real OS environment-variable size limits.
- **`FLAT_CATEGORIES`** is a hardcoded set of five category names that
  behave differently from every other category, for no inferable reason.

None of this follows from the actual requirements (a resolved document
graph reaching Terraform; secrets never touching disk; a standalone
`terraform plan` working without a strata wrapper; build renders
unresolved, deploy renders resolved; Terraform's own fixed
`*.auto.tfvars.json` > `TF_VAR_*` precedence) — it is entirely inherited
complexity.

## Considered Options

- **Keep the per-category shape and patch the secret-taint gap in
  place** — the path originally pursued in
  [flat-category-secret-taint-size-limit.md](../work/flat-category-secret-taint-size-limit.md).
  Rejected: a per-category patch cannot fix the `resx_<type>` data-loss
  bug or the three-incompatible-shapes problem, both caused by the same
  category taxonomy the patch would leave in place.
- **Collapse the entire projection into one `strata` object and one flat,
  path-keyed secrets map** (chosen).
- **`type = any` for the single `strata` variable, with no generated
  type declaration.** Rejected as the default — it gives up Terraform's
  own type-checking at the one boundary where it matters most (a typo
  fails at `plan`/`apply`, not `validate`). Kept as a supported opt-out
  for a consumer who declares their own `variable "strata"`.

## Decision Outcome

Chosen: **one `strata.auto.tfvars.json` file holding a single `strata`
object, and one `TF_VAR_strata_secrets` flat `map(string)` keyed by
dotted path into that object** — replacing every per-category file,
variable, and the `_REAL_VARIABLE_NAME`/`FLAT_CATEGORIES` machinery
entirely. No compatibility mode, no dual-write: with two known consumers
(`cfg-int-deployment`, `haven`), both owned by the same team, and a
pre-1.0 alpha version, a clean break is cheaper than a transitional shape
nobody wants to live in.

Four points settled as part of this one decision (full reasoning and
evidence in the linked design doc — not repeated here):

- **Resources are a flat map keyed by name, not nested by resource
  type.** This is what fixes the `resx_<type>` data-loss bug: there is
  exactly one `resources` key in the whole structure, so there is nothing
  left to collide. Confirmed safe by `WorkspaceSpecModel`'s own
  pre-existing global-uniqueness check on resource names, and confirmed
  cheap by both real consumers (one has zero `Resource` documents, the
  other already declares a flat `variable "resources"` and works around
  today's type-nesting itself).
- **Secrets are delivered as a sentinel, never an omitted key.**
  `deploy run` writes a self-describing sentinel string
  (`"__strata_secret__:<path>"`) in place of each secret rather than
  removing it, so the object's shape never changes between `build run`
  and `deploy run`. This is what removes the need for any `try()`/
  `lookup()`-with-fallback pattern at every secret-bearing use site — the
  alternative (omitting the key) was traced back to being the actual
  source of the "silent null" weakness, not an inherent cost of a
  single-object design.
- **Strata generates the type**, not the consumer. `build run` emits
  `strata_variables.tf` (a real `object({...})` matching the exact shape
  of the same run's `strata.auto.tfvars.json`) and `strata_locals.tf`
  (reassembling resolved secrets into `local.strata.*`), unless the root
  already declares its own `variable "strata"`. The one consumer rule
  this leaves: read `local.strata.*`, never `var.strata.*` — a single,
  greppable rule, not per-secret knowledge spread across every module.
- **`modules` is projected for the first time** (read-only, alongside
  the existing Helm/Compose workload pipeline, which this does not
  change) — the prior exclusion was sound only because adding one more
  category to a per-category design had real cost; in a single-object
  shape, leaving it out would itself be the special case.

### Consequences

- Good: every structural defect above is deleted outright, not worked
  around — the resx collision, the three incompatible shapes, the
  rename table, the per-category secret-taint granularity, and the
  `FLAT_CATEGORIES` special set all stop existing rather than being
  patched.
- Good: secret-taint granularity becomes per-leaf by construction (a
  secret is one entry in one flat map, keyed by its own exact path),
  rather than something a future patch has to keep re-narrowing.
- Good: type safety at the `strata` boundary is preserved despite the
  single-variable shape — `strata_variables.tf` is regenerated every
  build, so it cannot drift from the payload the same run produced,
  unlike a hand-maintained consumer-side type.
- Bad: every real consumer `.tf` root breaks. `var.platform_providers` →
  `var.strata.providers`, `var.resources` → `local.strata.resources`,
  `var.strata_tenant` → `var.strata.tenant`, and so on — mechanical, but
  touching every file that reads a strata-supplied value in both known
  consumers.
- Bad: v1 output parity is abandoned entirely — the migration story is a
  real rewrite, not "same variables, new tool," which
  [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md)
  previously treated as a goal worth preserving.
- Bad: two generated `.tf` files (`strata_variables.tf`,
  `strata_locals.tf`) now land in every Terraform build output where
  previously there were none — both are build artifacts under ADR-0025's
  existing carve-out, not synced sources, but they are more generated
  surface area to keep correct.
- Neutral: `ProvisionerModel` itself (tool/source/backend/integration
  binding) is deliberately never part of this object — it describes how
  strata drives the tool, not the target infrastructure the tool reads,
  and continues to reach Terraform through `prepare()`/`init()` exactly
  as it does today.
