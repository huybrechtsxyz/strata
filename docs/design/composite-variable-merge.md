# Composite/Mergeable Variable Values — v2 Design

- Status: **Decided (2026-10-02) — no new schema needed.** Use
  `spec.properties` instead of `spec.variables`/`store: constant` for any
  value that needs composing from multiple documents
  ([docs/how-to/composite-variable-fragments.md](../how-to/composite-variable-fragments.md)).
  Confirmed against real code, experimentally verified, and confirmed
  against a second real case (`ring_subnet_cidrs`, same shape as
  `appgateway_config`) — see Decision below. Not yet rolled out to the real
  `appgateway_config` consumer (that's a migration of existing config, a
  separate follow-up, not a strata change).
- Date: 2026-10-02
- Related: [ADR-0007](../decisions/0007-network-model-design-decisions.md) §5,
  [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) §4 (both
  deferred a version of this exact problem — "revisit when a v2
  workspace/environment overlay concept is designed"), [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  (constrains any fix: strata stays opaque to a value's internal shape),
  [docs/work/gap_fit_v1.md](../work/gap_fit_v1.md) gap #19

## Overview

Raised directly, from real production experience: *"with azure waf /
firewall you now sometimes have a lot [of] parts coming together where you
can only have one really big TF declaration so all customer paths on agw are
in one block — that is not really a manageable solution."*

**The real Terraform shape, confirmed directly** (`iac_aks_core`'s
`src/modules/az-platform-core/_components/agw/agw.tf`, vendored again into
`iac-int-deployment/core/terraform/components/agw/agw.tf`): one
`resource "azurerm_application_gateway" "main"`, built entirely from
`dynamic "http_listener"`/`dynamic "request_routing_rule"`/
`dynamic "backend_address_pool"`/`dynamic "url_path_map"`/
`dynamic "path_rule"` blocks, every one of them `for_each`-driven off a
*single* input variable, `appgateway_config` — a deeply nested
`map(object({ hosts, url_path_map, path_rules, waf_policy, ... }))`. This is
inherent to `azurerm_application_gateway` itself: the provider gives no way
to declare a listener/path-rule/backend-pool as its own independent
resource — they only exist as repeated sub-blocks of one gateway resource.

**The real consumer config, confirmed directly**
(`cfg-int-deployment/deploy/hubs/z00/s01/environment.yaml`): that entire
nested structure is today authored as the literal `value:` of *one*
`store: constant` variable, in *one* `environment.yaml` file:

```yaml
- key: appgateway_config
  store: constant
  value:
    unisonplanning:
      domain: "unisonplanning.com"
      backend_hostnames: {...}
      hosts:
        c0224:
          domain_name: "c0224-agw-dev.westeurope.cloudapp.azure.com"
          backend_pools: ["apim", "dispatcher-dev", "datahub-admin-dev"]
          request_routing_rule: {...}
```

A comment at this exact site dates it: *"WAF/AGW first real test
(2026-09-30)"* — this is live with exactly **one** customer
(`unisonplanning`) today. Adding a second customer means a second top-level
key, added by hand, in this same shared file — there is no way for a
different team/customer to contribute their own path/listener config from
their own file without editing this one shared blob. That is the real,
already-anticipated scaling problem.

## Why this isn't solved already

- **v2's only existing multi-document variable merge is whole-value
  override, not a combine.** `environment_service.merge_environment_models()`,
  confirmed directly:

  > "Later entries in `environments` override earlier ones on a key
  > collision — the actual store definition (not just its presence)..."

  i.e. `variables.update({v.key: v for v in spec.variables or []})` per
  document. Two Environment documents both declaring `key:
  appgateway_config` would have the **second one's value win entirely** —
  the first customer's whole config is silently discarded, not merged with
  the second's.

- **v1 had two narrower precedents for "multiple documents contribute
  fragments of one thing," neither generalized, neither ported to v2:**

  1. `merge_networks()`/`merge_firewalls()` (`src/strata/services/
     network_service.py`/`firewall_service.py`) — real document-level merge:
     networks merge by name, subnets/peerings merge by `(network_name,
     subnet_name)`/`(network_name, peering_name)`, firewall rules merge by
     `(direction, proto, port, from_, to, interface)` — "last definition
     wins" per composite key. Scoped to the Network/Firewall *kinds* only.
     **Already explicitly deferred in v2**, not an oversight:
     [ADR-0007](../decisions/0007-network-model-design-decisions.md) §5 /
     [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) §4:
     *"a workspace/environment composition feature, not core schema
     validation... revisit when a v2 workspace/environment overlay concept
     is designed."* This request is that trigger.
  2. `EnvironmentIncludeModel`/`TerraformLoader` (`src/strata/models/
     environment_model.py`, `src/strata/utils/terraform_loader.py`) — a
     real, schema-defined `includes:` field (`spec.overrides.includes`,
     environment-wide, and `spec.overrides.resources[].includes`,
     per-resource), each entry a `source` glob/`@repo/` ref, `target`
     output filename, and a `strategy: concatenate | merge`. Traced both
     strategies all the way through, since this looks the closest to a
     real precedent:
     - `concatenate` — raw **text** paste (`TerraformLoader.concatenate()`),
       explicitly named for this exact scenario in its own docstring
       example (`source: "@haven/terraform/waf/listeners/*.tf"`,
       *"merging WAF listener blocks that must live in one file"*).
     - `strategy: merge` — traced into `TerraformLoader.merge()` →
       `_deep_merge_terraform()`: this is **HCL-block-list-aware**, not a
       generic deep merge — `resource`/`variable`/`data`/etc. top-level
       keys get their *lists concatenated*, never recursively combined.
       Two files each declaring `resource "azurerm_application_gateway"
       "main" {...}` would become a list with **two entries for the same
       resource address** — Terraform rejects that as a duplicate, not a
       working merge.
     - `TerraformLoader` *does* separately define `merge_tfvars()`/
       `_deep_merge_values()`, which recursively merges nested dicts
       correctly (exactly the `{"customerA": {...}} + {"customerB":
       {...}}` shape needed here) — but confirmed by direct search:
       **it has zero callers anywhere in v1's source.** Built, never wired
       to `includes:` or anything else.
     - None of `EnvironmentIncludeModel`/`IncludeMergeStrategy`/
       `TerraformLoader` were ported to v2 (zero references in v2 source).

  **Net finding: even if this whole v1 feature were ported as-is, it would
  not solve the real AGW case** — its only working strategy operates on
  independent `.tf` resource/variable declarations across files, not on
  fragments of one nested variable's *value*. The method that would
  actually do the right thing (`merge_tfvars()`) was built but never
  connected to anything, in either version.

- **[ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  constrains the fix.** Strata must stay opaque to what's *inside*
  `appgateway_config`'s value — it already doesn't understand AGW/WAF
  schema today (it just passes the value through as one
  `TF_VAR_appgateway_config`-shaped JSON blob), and that must stay true.
  The fix has to operate at the "assemble one opaque value from several
  opaquely-typed fragments" level — strata combines fragments by whatever
  *key* identifies them (a customer/domain name), never validating or
  understanding what's underneath that key.

- **Correction: v2 is not actually starting from zero on generic deep
  merge — it already has one, just not wired to variable values yet.**
  Checked every real merge function in v2 directly (not just
  `merge_environment_models()`), and found `merge_environment_models()` is
  the *odd one out* — the other two both already use a shared, generic,
  recursive `deep_merge()` utility (`strata/utils/dict_merge.py`):
  override wins per leaf key, dicts merge recursively at any depth, lists
  replaced wholesale (a deliberate choice left to the caller). Real,
  already-shipped callers: `merge_deployment_specs()`
  (`deployment_service.py`, Deployment `extends:`/`partial:` resolution —
  base+child spec, plus special-cased `stages` merged by `step` and
  `environments` list-concatenated) and
  `merge_workspace_environment_deployment_properties()`
  (`value_controller.py` — a *three-way* deep merge: Workspace →
  each reachable Environment → Deployment's own `properties`/`custom`).
  This is, in effect, the exact capability v1's orphaned `merge_tfvars()`/
  `_deep_merge_values()` was built for and never wired up — except v2
  already has it, general-purpose, proven in two real call sites. Building
  the AGW fragment merge on this existing primitive (rather than a new
  bespoke two-level-only function) is less new code, not more, and more
  consistent with how v2 already solves this exact shape of problem
  elsewhere. See the updated "Proposed direction" below.

## Proposed direction (in progress — two parts already confirmed solved)

Let multiple documents each contribute a fragment to the same variable,
with strata merging all fragments for a given variable `key` into one
assembled value before delivery. Checked two parts of this directly
against real v2 code rather than assuming either needs new work:

- **Fragment discovery needs zero new plumbing.** v2's real document walk
  (`SolutionController._walk()`, `solution_controller.py`) already treats
  every `.yaml`/`.yml` file anywhere under the solution root as an
  independent candidate document — filename-agnostic, parsed by its own
  `kind:`, indexed by `(kind, meta.name)` identity (ADR-0015). A file named
  e.g. `tenant-env-paths.yaml` dropped anywhere in the tree (naturally,
  right next to the `tenant.yaml`/`deployment.yaml` it's about) is already
  discovered today with no change to the walk at all. The only genuinely
  new things are (a) a schema for that file's content and (b) merge logic
  that combines rather than overwrites — not discovery.
- **Per-target rendering needs zero new work either.** Checked each real
  consumer directly: Terraform's `tf_var_env()` (`deploy_controller.py`)
  already does `json.dumps()` on any non-string resolved value before
  injecting `TF_VAR_<key>`; Helm's `_render_values()` (`integrations/
  helm.py`) already `yaml.safe_dump()`s resolved values straight into a
  real `values.yaml`; Compose already merges resolved values into its own
  `.env`-shaped process environment. Each integration already renders
  whatever Python value it's handed in its own native format. The merge
  feature only has to produce **one correct, assembled Python value**
  (a dict) — the "support json/yaml/tf/.env output" need is already met,
  for free, by code that exists today. No new rendering/format-conversion
  work belongs in this design.

What's left to decide is the actual merge **granularity** — and the real
`appgateway_config` sample complicates the "shallow, top-level-key only"
answer sketched in the first pass of this doc. Re-reading it closely:

```yaml
unisonplanning:              # customer — the outer key
  domain: "unisonplanning.com"
  hosts:
    c0224:                   # looks like one ring/leaf under that customer
      domain_name: "..."
      backend_pools: [...]
      request_routing_rule: {...}
```

If "add a new ring to an existing customer" means adding a new key under
that customer's own `hosts:` map — not a new top-level customer — then a
fragment some ring's own file contributes isn't a whole top-level key at
all, it's one entry **two levels down**, inside a subtree a *different*
fragment (that customer's own top-level definition) already owns. A fixed
two-level-only merge function would need bespoke new code to reach that
depth — but since v2 already has a real, generic, depth-agnostic
`deep_merge()` (see above, now proven at two real call sites), reusing it
directly costs nothing extra to also handle arbitrary nesting beyond two
levels. That flips the earlier "stay minimal, hand-roll a two-level-only
merge" instinct: reusing the existing generic primitive as-is is *less*
new code than deliberately restricting it to two levels would be. Still
unconfirmed, though, whether fragments should key at the top level
(customer) or need to reach inside an existing customer's subtree
(ring) — that's an ownership/collision question `deep_merge()`'s own
"override wins per leaf key" semantics answer neutrally either way, not a
mechanism question. See Open Question 2 (updated) below.

### Candidate answer: `spec.properties`/`spec.custom` may already solve this today, for Terraform, with zero new code

Checked directly whether `EnvironmentSpecModel` has anywhere closer to a
ready-made answer than `spec.variables`, since the generic `deep_merge()`
found above has to be reachable from *somewhere* real. It does:
`EnvironmentSpecModel.properties`/`.custom` (plain `dict[str, Any] | None`,
already existing fields, confirmed directly) already run through exactly
the right machinery:

- **Already recursively deep-merged**, right now:
  `merge_workspace_environment_deployment_properties()`
  (`value_controller.py`) chains Workspace → each reachable Environment →
  Deployment's own `properties`/`custom` through `deep_merge()`, confirmed
  by reading it directly — arbitrary depth, override wins per leaf key.
- **Already delivered to Terraform**, right now: `_build_properties_payload()`
  (`terraform_projection.py`) is a literal **pure passthrough** of that
  merged dict — `return graph.properties`, confirmed by reading it
  directly — written to `properties.auto.tfvars.json`
  (`properties`/`custom` are both in `FLAT_CATEGORIES`, so every top-level
  key becomes its own independent real Terraform variable, e.g.
  `variable "appgateway_config" {}` — not wrapped under anything).

**If `appgateway_config` were authored under each Environment's
`spec.properties` instead of `spec.variables`/`store: constant`, this
might already work today** — one Environment document per customer (or
per ring), each contributing only its own nested fragment
(`spec.properties.appgateway_config.unisonplanning.hosts.c0224: {...}`),
merging automatically through code that already ships, delivered via a
file Terraform already auto-loads. No new document kind, no new merge
function, no new delivery path.

The one real cost: moving out of `spec.variables` gives up
`VariableStoreModel`'s typed wrapper (`type`/`description`/`store`
metadata). Checked the real sample directly, though — it declares neither
`type:` nor `description:` for `appgateway_config`, so nothing would
actually be lost in this specific real case. Still needs confirming
whether any *other* real `store: constant` variable relies on that
metadata in a way a `properties`-based fragment couldn't, and whether
`properties`/`custom` reach non-Terraform targets (Helm/Compose) the same
way `variables` do today — not yet checked, since the real AGW case is
Terraform-only. See updated Open Question 1.

**Experimentally confirmed (2026-10-02)**, not just read from source: ran
three real `EnvironmentModel` documents through the real
`merge_workspace_environment_deployment_properties()` and real
`planned_files()` — one per customer, plus a *second* document adding a new
ring (`c0225`) to an already-declared customer (`unisonplanning`), each
contributing only its own fragment under `spec.properties.appgateway_config`.
Result: both rings survived under `unisonplanning` (correctly combined from
two separate documents), `unisonplanning`'s own top-level fields
(`domain`) survived alongside the ring added later, and the second customer
(`acmecorp`) landed alongside the first untouched — the exact
`properties.auto.tfvars.json` content Terraform would auto-load, fully
assembled, nothing silently dropped. Confirms the "zero new code" claim
above is not just plausible from reading the source, it actually works.

## Decision (2026-10-02)

**No new schema, no new document kind, no new merge function.** Author any
variable that needs composing from multiple documents under
`spec.properties` (or `spec.custom`) instead of `spec.variables`/
`store: constant`, following
[docs/how-to/composite-variable-fragments.md](../how-to/composite-variable-fragments.md).
Resolving each Open Question against the evidence gathered:

1. **Resolved: `spec.properties` is the mechanism, confirmed not just
   plausible.** Read from source, then experimentally verified end to end
   (three real `EnvironmentModel` documents through the real merge +
   projection code, correct result). No new mechanism needed for the real,
   Terraform-only `appgateway_config` case.
2. **Resolved as moot.** `deep_merge()` already handles arbitrary nesting
   depth uniformly — whether a fragment contributes a whole top-level
   customer or one entry two levels down inside an existing customer's
   `hosts:` map needs no schema decision either way; the same mechanism
   handles both without modification.
3. **Resolved: yes, a second real case exists**, found directly in the same
   real repo, same file:
   `cfg-int-deployment/deploy/hubs/z00/s01/environment.yaml`'s
   `ring_subnet_cidrs` — `store: constant`, `type: object`, `value:` keyed
   by ring name (`dev`/`qa`/`acc`), each a nested object of per-purpose
   subnet CIDRs. Identical "nested-by-key" shape to `appgateway_config`
   (there keyed by customer, here by ring) — confirms this is a recurring
   authoring pattern in the real codebase, not an AGW-only oddity, and
   validates writing the how-to as a general pattern rather than an
   AGW-specific workaround. (Today `ring_subnet_cidrs` is still declared in
   one file, not yet split per-ring — it hasn't hit the same scaling pain
   `appgateway_config` has, but the schema shape is the same real
   precedent.)
4. **Answered as a convention, deliberately not a new enforced rule:** one
   Environment document per owner (customer/ring/team), filed naturally
   alongside that owner's own directory under `deploy/hubs/**` (matching
   the real, already-used path-convention structure —
   [path-conventions.md](path-conventions.md)), each named in the relevant
   Deployment's (or Tenant's) `environments:` list. Nothing new validates
   this structurally — same "don't build enforcement machinery nothing has
   asked for yet" discipline as everywhere else in this codebase; revisit
   if a real case needs it enforced.

**Remaining known gap, deliberately not resolved here:** whether
`properties`/`custom` reach non-Terraform provisioners (Helm/Compose) the
same way `spec.variables` does — not checked, since the one real need
(`appgateway_config`) is Terraform-only. Flagged in both how-to docs; not a
blocker for the decision above.

## Related Decisions

- [ADR-0007](../decisions/0007-network-model-design-decisions.md) §5 —
  deferred Network multi-file merge, named this exact trigger condition.
- [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) §4 —
  deferred Firewall multi-file merge, same trigger condition.
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) —
  strata supplies input, never rewrites IaC source; any fix here must keep
  a fragment's internal shape (AGW/WAF or otherwise) fully opaque to
  strata.
- [docs/work/gap_fit_v1.md](../work/gap_fit_v1.md) gap #19 — the tracked-gap
  entry for this problem.
- [docs/how-to/composite-variable-fragments.md](../how-to/composite-variable-fragments.md) —
  the worked how-to for the `spec.properties` pattern above.
- [docs/how-to/generate-per-instance-terraform-blocks.md](../how-to/generate-per-instance-terraform-blocks.md) —
  a connected real need: looping `output.template` (Jinja2) over this same merged `properties`
  value to generate one static `provider`/`module` block per entry, working around Terraform's
  own static-per-instance `providers` meta-argument constraint.


## History

- Does this need a new mechanism at all, or does `spec.properties` (already deep-merged, already delivered) already solve it for a Terraform-only case like `appgateway_config`? Decided no new mechanism for now - if `properties`/`custom` ever turn out not to reach Helm/Compose the same way, the earlier alternatives (a `merge_key`/`fragment_of` field, a dedicated per-customer kind, a workspace/environment overlay concept) are still on the table.
- Whether "adding a ring to a customer" means a new key under that customer's own `hosts:` map (two levels down from the variable's root) rather than a whole new top-level customer key is still open - a fixed two-level composite key (customer, ring) looks like the right evidence-sized granularity, not a whole-top-level merge or a fully generic dotted-path mechanism.
- Whether this is specific to `appgateway_config`-shaped variables or there's a second real case with a similar nested-by-customer shape is still open - worth checking before assuming the mechanism generalizes.
- Who authors a fragment and where (per-customer, per-environment, per-team ownership) is still open.
- v1 has two narrower merge precedents (`merge_networks`/`merge_firewalls` document-level merge; `EnvironmentIncludeModel`/`TerraformLoader.concatenate()` raw-text merge) - neither generalizes to arbitrary variables, neither was ported to v2, and the text-merge one would not even correctly solve the real AGW case (duplicate resource declaration, not a merge).
