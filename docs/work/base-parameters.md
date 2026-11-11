# Parameterised bases (`input`, `params`, `${param:}`) - Work

- Status: draft
- Last updated: 2026-11-11

## Overview

A solution that runs the same stack in many places ends up with many leaves that are copies of each other. They differ
only in names built from a few identifiers, plus a handful of real values. `extends` removes duplicated *values*. It
cannot remove duplicated *identity*: a base cannot say "my environments are `hub-<this hub>-env` and
`sub-<this spoke>-env`", because it has no way to refer to the leaf that extends it.

This doc compares seven ways to template those leaves and proposes one (option G): a base declares typed **inputs** in
`meta.input`, uses them in its `spec` as `${param:key}`, and a leaf binds them in `meta.params`. It works like a generic
type with constraints: the base is `Spoke<hub, spoke, tenant>`, and the leaf is `Spoke<hub01, acme-001, acme>`.

Nothing is built. The doc is here to settle the shape before any code.

## The case: a hub-and-spoke solution

Measured on a consumer solution that deploys one spoke per customer behind a few regional hubs. Names in this doc are
stand-ins (customers Acme, Globex and Initech, hubs `hub01` and `hub02`); the structure and the counts are real.

Every spoke has the same seven files under `deploy/hubs/<hub>/<spoke>/`. Every customer project under it adds three
more. Lines that differ between two spokes of the same hub:

| File                          | Kind        | Lines | Differ |
| ----------------------------- | ----------- | ----: | -----: |
| `deploy-bootstrap.yaml`       | deployment  |    39 |     10 |
| `deploy-spoke.yaml`           | deployment  |    40 |     11 |
| `deploy-ingress.yaml`         | deployment  |    40 |     12 |
| `env-bootstrap.yaml`          | environment |    54 |      8 |
| `env-spoke.yaml`              | environment |    73 |      5 |
| `env-ingress.yaml`            | environment |    43 |     10 |
| `env-subscription.yaml`       | environment |    66 |     17 |
| `<proj>/deploy-customer.yaml` | deployment  |    43 |     14 |
| `<proj>/env-customer.yaml`    | environment |    61 |     11 |
| `<proj>/env-deploy.yaml`      | environment |    27 |      9 |

The differing lines fall into three groups:

1. **Identity, built from (hub, spoke, tenant).** `meta.name`, `tags`, `annotations.description`,
   `layers.segments`, the `environments` list (`hub-hub01-env`, `sub-acme-001-env`, `bootstrap-acme-001-env`), and
   comments ("Acme's own spoke"). This is most of the diff.
2. **Values that follow a naming rule.** `deploy_code`, `sequence_number`, `kv-acme-tfstate-001`,
   `rg-acme-tfstate-prd`, `sttfstateacme001`.
3. **Real per-spoke data and exceptions.** `subscription_id`, `spn_object_id`, the provider rebinding of a hub in
   another region, feature flags switched on, the ingress `customers` block.

So a spoke holds about ten real facts in about 355 lines. Other copied kinds in the same solution:

| Kind                                             | Copies | Differ   | Worth templating                                         |
| ------------------------------------------------ | -----: | -------- | -------------------------------------------------------- |
| `tenant` (`customers/<t>/tenant.yaml`)           |      7 | ~8 of 43 | yes, same mechanism                                      |
| `environment` (`customers/<t>/tenant.env.yaml`)  |      7 | ~5 of 14 | yes                                                      |
| `provider` (`providers/<region>.yaml`)           |      8 | 7 of 51  | marginal: region and display name only                   |
| `environment` (`deploy/hubs/<hub>/env-hub.yaml`) |      3 | 9 of 29  | no: the differences are real data (VNet ID, firewall IP) |
| CI pipeline definitions                          |      4 | 5 of 115 | not strata's: the CI system's own templates cover it     |

The solution already generates one kind of leaf: a script writes ring leaves from `.tmpl` files with
`{{spoke}}`-style placeholders.

## What strata does today

Verified against the source on 2026-11-11.

- **Only `deployment` has `extends`.** `DeploymentSpecModel.extends`
  ([deployment_model.py](../../src/strata/models/deployment_model.py)). `environment`, `tenant` and `provider` have
  no inheritance.
- **Merge rules.** `merge_deployment_specs()` ([deployment_service.py](../../src/strata/services/deployment_service.py))
  works on raw dicts. Nested blocks merge per leaf key. `stages`, `providers` and `assets` merge by key. `environments`
  is the base's list followed by the child's. Other lists are replaced. Chains, cycles and the tenant-defaults axis
  (ADR-0024) are handled in
  [deployment_resolution.py](../../src/strata/controllers/deployment_resolution.py).
- **A base is a validated document.** A `partial: true` base goes through the model like any other deployment. The
  resolver takes `model.spec.model_dump()` from the index. `tenant`, `environments[]` and `providers[].provider` are
  `PlatformName` (`^[a-z][a-z0-9_-]*$`), declared as references, and checked by `validate_references()`. **A string
  such as `hub-${param:hub}-env` in a base fails today, both on the pattern and as a reference.** That fact shapes the
  proposal (see "Templates are validated per instance").
- **`meta` is never merged.** Each document's `meta` is its own.
- **Tokens are resolved at run time.** `var`, `secret`, `feature`, `output`, `value`, `deployment` and `artifact`
  ([value_tokens.py](../../src/strata/utils/value_tokens.py)). Each of them resolves *values*. None of them may
  appear in a reference field, and `${deployment:}` only knows `name` and `tenant`.

## Options

### A. Generic base, bound in `extends`

The base declares parameters. The leaf binds them inside the reference:
`extends: bootstrap_deployment_base<hub: hub01, spoke: acme-001>`.

Explicit and checkable, but the binding lives in a string that is also a reference, so `extends` stops being a plain
`PlatformName` and every tool that reads it needs a parser. G keeps the same idea with the binding in its own field.

### B. Use the layer segments as the parameters

`layers.segments.hub/spoke` and `tenant` are already on every leaf, and the segments are already checked against the
folder. A general `${deployment:spec.layers.segments.spoke}` (sketched in `deployment-token-paths.md`, work doc) used
in the base's `environments` and a naming rule in `configuration.yaml` would shrink a leaf to about six lines.

Rejected: `${deployment:}` is resolved at run time from the resolved deployment, but `environments` and `tenant` are
references, resolved at load time *before* tokens. Making that work needs a load-time pre-pass for one token kind
only. The parameters also stay implicit (by convention, not declared).

### C. Generate the files with a script

Add `.tmpl` files for a spoke and a generator script (as the solution already has for rings), plus a CI check that
regenerates and compares.

No strata change and can ship now, but the copies stay. A template change has to be re-run for every spoke, and a
hand edit makes a file drift. **Kept as the stopgap** until G exists.

### D. Expansion: one table, N deployments

A `deployment-set` document lists the spokes, and strata expands each stack per row, like Terraform `for_each`.

Rejected: deployments stop being files. Audit paths, `deploy list`, the per-leaf pipeline targets and the
`layers.follows` folder check all assume one document per deployment in a folder. Exceptions (flags, ingress
customers) still need a per-row override, which brings the per-spoke files back.

### E. Overlays (Kustomize style)

A base plus a per-spoke patch. `extends` already is this for `spec`. The gaps are `meta` and identity, so this
reduces to B or G.

### F. Derive the naming-rule values

Build `kv-${code}-tfstate-${seq}` and similar names in the stack's base instead of typing them per spoke. Only
`subscription_id` and `spn_object_id` are real data. **Folded into G**: with `input` on an environment base, group 2
of the diff disappears too.

### G. Inputs on the base, `${param:}` in its spec, bound in `meta.params` (proposed)

The rest of this doc.

## Proposal (option G)

### Shape

A base declares its inputs in `meta.input`, with the same shape as an environment's variables:

```yaml
# stacks/bootstrap/deployment.yaml
apiVersion: strata.omp.com/v2
kind: deployment
meta:
  name: bootstrap_deployment_base
  input:
    - key: hub
      type: string
      pattern: "^hub[0-9]{2}$"
      description: "Hub of the spoke."
    - key: spoke
      type: string
      pattern: "^[a-z][a-z0-9]*-[0-9]{3}$"
    - key: tenant
      type: string
      references: tenant          # must name an existing tenant
    - key: provider
      type: string
      references: provider
      default: azure-westeurope
spec:
  partial: true
  workspace: bootstrap
  tenant: ${param:tenant}
  providers:
    - slot: azure
      provider: ${param:provider}
  layers:
    follows: spoke-path
    segments:
      hub: ${param:hub}
      spoke: ${param:spoke}
  environments:
    - solution-env
    - hub-${param:hub}-env
    - sub-${param:spoke}-env
    - bootstrap-${param:spoke}-env
```

A leaf binds them in `meta.params` and adds only what is its own:

```yaml
# deploy/hubs/hub02/initech-001/deploy-bootstrap.yaml
apiVersion: strata.omp.com/v2
kind: deployment
meta:
  name: bootstrap-initech-001
  params:
    hub: hub02
    spoke: initech-001
    tenant: initech
    provider: azure-westus2
spec:
  extends: bootstrap_deployment_base
```

The same works for environments, once they have `extends`. The naming-rule values move into the base, and the real
data stays in the leaf:

```yaml
# stacks/bootstrap/subscription-env.yaml
kind: environment
meta:
  name: subscription_env_base
  input:
    - { key: code, type: string, pattern: "^[a-z][a-z0-9]*$" }
    - { key: seq, type: string, pattern: "^[0-9]{3}$" }
    - { key: subscription_id, type: string }
    - { key: spn_object_id, type: string }
spec:
  properties:
    environment_info:
      subscription_id: ${param:subscription_id}
      sequence_number: ${param:seq}
      deploy_code: ${param:code}
      environment_name: prd
    state_vault:
      name: kv-${param:code}-tfstate-${param:seq}
      resource_group_name: rg-${param:code}-tfstate-prd
  variables:
    - { key: tf_state_storage_account, store: constant, type: string, value: sttfstate${param:code}${param:seq} }
    - { key: spn_object_id, store: constant, type: string, value: ${param:spn_object_id} }
    # ...

# deploy/hubs/hub02/initech-001/env-subscription.yaml
kind: environment
meta:
  name: sub-initech-001-env
  params:
    code: initech
    seq: "001"
    subscription_id: 00000000-0000-0000-0000-000000000001
    spn_object_id: 00000000-0000-0000-0000-000000000002
spec:
  extends: subscription_env_base
```

Exceptions stay ordinary `spec` in the leaf, merged over the base as `extends` already does (a flag switched on, the
ingress `customers` block).

### Rules

1. **`input`** is a list of `{key, type, pattern?, references?, default?, description?}`. `key` is unique within
   the document. `type` is `string`, `number`, `boolean`, `object` or `list`. An input without `default` is required.
2. **`params`** binds the inputs of the document named in `spec.extends`, and only those. A key that the parent does
   not declare is an error. A required input of the parent left unbound is an error. Both name the document that
   holds `params` and the input.
3. **Scope is per document.** `${param:k}` in a document reads that document's *own* `input`, never a parent's or a
   child's. Inputs are not inherited and not shared across a chain: each level states what it accepts and what it
   passes on (see "Chains").
4. **Where `${param:}` may appear:** any string in a template's `spec`, including reference fields (`tenant`,
   `environments[]`, `providers[].provider`) and `layers.segments`. Not in `meta.name`, and not in `spec.extends`:
   names and the chain stay static, so the index and the chain never depend on substitution.
5. **Whole value or embedded.** A token that is the entire value is replaced by the param's value with its type, so
   an `object` param can supply a block. A token inside a longer string needs a scalar param. This is the
   `value_reference_not_scalar` rule of `${value:}`.
6. **`${param:a.b}`** walks into an `object` param with the same dotted walk as `${value:}`.
7. **Param values are literals or own inputs.** A value in `params` may contain `${param:k}` that reads the same
   document's `input` (that is how a middle base passes a value on). Any other `${` is an error. Substituted text is
   never scanned again, so a param cannot insert a `${secret:}`. Run-time tokens written in a template's `spec`
   (`${var:x}`) are left alone and resolved at run time as today.
8. **`params` needs a template parent.** `params` on a document whose parent declares no `input` is an error. A
   document that extends a template without `params` is valid only when every input of the parent has a `default`.

### Chains: `input` and `params` on every level

There is no depth limit. `extends` has none today (`_resolve_dict()` recurses until a document without `extends`
and stops only on a cycle), and G adds none.

Any document in a chain may hold both fields, and they face in opposite directions:

- **`input`**: what this document accepts from the document that extends it.
- **`params`**: what this document gives to the document it extends.

A middle base therefore does three things with each input of its parent: it **forwards** one of its own inputs
(`hub: ${param:hub}`), **fixes** a value (`stack: bootstrap`), or **leaves it unbound** if the parent has a default.
It can also **rename** (`spoke: ${param:spoke_id}`) or **combine** (`name: ${param:hub}-${param:spoke}`), because a
`params` value is a string with tokens.

This is C# generics:

```csharp
class StackBase<TStack, THub, TSpoke, TTenant> { ... }                 // base1
class BootstrapBase<THub, TSpoke, TTenant>
    : StackBase<Bootstrap, THub, TSpoke, TTenant> { ... }                // base2: fixes TStack, forwards the rest
class BootstrapAcme : BootstrapBase<Hub01, Acme001, Acme> { }          // leaf: binds everything
```

The same three levels in strata:

```yaml
# base1: stacks/common/stack-deployment.yaml. Shape shared by every spoke stack.
kind: deployment
meta:
  name: stack_deployment_base
  input:
    - { key: stack, type: string, pattern: "^[a-z]+$" }
    - { key: hub, type: string, pattern: "^hub[0-9]{2}$" }
    - { key: spoke, type: string, pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" }
    - { key: tenant, type: string, references: tenant }
    - { key: provider, type: string, references: provider, default: azure-westeurope }
spec:
  partial: true
  tenant: ${param:tenant}
  providers:
    - { slot: azure, provider: "${param:provider}" }
  layers:
    follows: spoke-path
    segments: { hub: "${param:hub}", spoke: "${param:spoke}" }
  environments:
    - solution-env
    - hub-${param:hub}-env
    - sub-${param:spoke}-env
    - ${param:stack}-${param:spoke}-env
```

```yaml
# base2: stacks/bootstrap/deployment.yaml. The bootstrap stack.
kind: deployment
meta:
  name: bootstrap_deployment_base
  input:                                    # what a bootstrap leaf must give
    - { key: hub, type: string }
    - { key: spoke, type: string }
    - { key: tenant, type: string }
    - { key: provider, type: string, default: azure-westeurope }
  params:                                   # what base2 gives base1
    stack: bootstrap                        # fixed
    hub: ${param:hub}                       # forwarded
    spoke: ${param:spoke}
    tenant: ${param:tenant}
    provider: ${param:provider}
spec:
  partial: true
  extends: stack_deployment_base
  workspace: bootstrap
```

```yaml
# leaf: deploy/hubs/hub01/acme-001/deploy-bootstrap.yaml
kind: deployment
meta:
  name: bootstrap-acme-001
  params: { hub: hub01, spoke: acme-001, tenant: acme }
spec:
  extends: bootstrap_deployment_base
```

Constraints are checked at every level the value passes through: `hub` against base2's input (only a type here) and
again against base1's (`pattern`). A middle base may make a constraint stricter for its own children, but it cannot
relax its parent's.

**A middle level may also close the chain.** A document with `params` and no `input` binds its parent completely:
it is a closed type (`class C : Base<int>`). It is validated once on its own, after its chain is filled in, and its
children extend it like any plain base. This allows, for example, a `hub02_bootstrap_base` that fixes
`provider: azure-westus2` once for every spoke of that hub.

**Resolution order.** Two passes over the chain:

1. **Bind, top-down from the leaf.** The leaf's `params` become the scope of its parent. That parent's `params` are
   evaluated in that scope and become the scope of the grandparent, and so on up to the root. Defaults fill in at
   each level, and constraints are checked at each level.
2. **Fill in and merge, root first.** Each template's `spec` is filled in with its own scope, then merged as today
   (`merge_deployment_specs()`, root first, child wins).

Errors are reported against the document whose `params` caused them, with the chain: `bootstrap-acme-001 ->
bootstrap_deployment_base -> stack_deployment_base: input 'hub' = "hub1" does not match ^hub[0-9]{2}$ (from
bootstrap_deployment_base params.hub = ${param:hub})`.

**Why per-document scope, not one shared set of params.** With one shared set (all levels read the leaf's
`params`), a middle base cannot fix a value for its parent, two bases that use the same key for different things
collide, and a leaf has to know the inputs of every level above it. Per-document scope costs one forwarding line
per input (`hub: ${param:hub}`). The open questions include a shorthand for that.

### Templates are validated per instance

This is the central change, and the one place where G is closer to C++ templates than to C# generics: the body of a
template cannot be fully checked until it is bound.

- A document with `meta.input` is a **template**. It is loaded, its `meta` is validated, and its `spec` is kept as a
  raw dict. The template's own check covers its `input` list and verifies that every `${param:k}` in its `spec`
  names a declared or inherited input. Its `spec` is *not* validated against the model, and its references are not
  resolved.
- **Instantiation** happens in `_resolve_dict()`, before `merge_deployment_specs()`, in the two passes of "Chains":
  bind each level's scope from the leaf up (with defaults and constraints), then fill in each template dict with
  its own scope and merge from the root down as today. A template's result can no longer be cached by name alone,
  because it depends on the scope it got. The cache key becomes (name, scope).
- **The merged result is validated as an ordinary document**, which includes `validate_references()`. A bad
  `hub-${param:hub}-env` shows up as an unknown environment on the leaf. The diagnostic names the leaf, the template
  and the template's field (`bootstrap_deployment_base: spec.environments[1] = "hub-hub09-env"`).
- A template is never deployable and never listed directly. A template that is not `partial: true` is an error.
  For environments, a deployment that lists a template environment by name (instead of its leaf) is an error.
- Bases without `input` are unchanged: they are still validated standalone.

`DocumentIndex` holds validated models today, so templates need a place for their raw `spec`, next to the model of
their `meta`.

### `extends` for environments and tenants

G needs `spec.extends` on `environment`, and the tenants of the example (seven copies, about eight differing lines
each) ask for it on `tenant`. Both reuse the deployment design: same field, same chain rules (no depth limit, cycles
rejected, `input`/`params` per level), and a kind-specific merge.

#### One resolver, three kinds

`deployment_resolution.py` walks a chain for deployments only. The walk (cycle detection, missing ancestor,
poisoning, cache, and now the two param passes) is not deployment-specific. Lift it into a resolver that takes a kind
and that kind's merge function, and call it per kind in dependency order:

1. **Environments.** They reference nothing that has `extends`.
2. **Tenants.** A tenant lists environments, which are resolved by then.
3. **Deployments.** A deployment lists environments and names a tenant. Then `_merge_tenant_defaults()` runs as
   today (ADR-0024), but on the *resolved* tenant, so a default inherited from a tenant base reaches the deployment.

The tenant-to-deployment axis stays separate: a deployment never `extends` a tenant.

#### Where the resolved documents live

Deployments are resolved into a separate map (`resolve_deployments()` returns them), and a few consumers read it.
Environments and tenants are read in many more places: per-environment token checks (`declared_keys()`,
`validate_document_tokens()`), `merge_environment_models()`, geography checks, and `${value:}`, which reads
`index.get(kind, name)` directly. The example already depends on the last one: its ingress environment reads a
tenant's hostname label as `${value:tenant.acme.spec.configuration.dns_slug}`.

So for these two kinds, **the resolved model replaces the raw one in the index**, right after resolution and before
any check that reads them. Every consumer then sees inherited fields with no change of its own. The raw document is
kept beside it, so diagnostics can still point at the file and line the author wrote.

A base (`partial: true`) or template stays in the index so `extends` can find it, but it is marked, and every other
lookup rejects it:

- a deployment or tenant that lists it in `environments` (`environment_not_listable`);
- a deployment whose `spec.tenant` names it (`tenant_not_selectable`);
- `${value:tenant.<base>...}` or `${value:environment.<base>...}` (`value_reference_to_base`);
- `validate` and listing commands skip it, as they do for a partial deployment.

#### Environment

| Field                              | Merge with `extends`                                                        | Same as today's run-time merge of a list |
| ---------------------------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| `properties`, `custom`             | deep merge per leaf key, child wins                                         | yes (`deep_merge`)                       |
| `variables`, `secrets`, `features` | by `key`: a child entry **replaces the whole entry**; new keys are appended | yes (`merge_environment_models()`)       |
| `extends`, `partial`               | consumed, stripped from the result                                          | n/a                                      |

Stores are deliberately *not* merged field by field, unlike a deployment's keyed lists (`stages`, `providers`): a
variable moved from `store: constant` with a `value` to a Key Vault store with a `ref` must not keep the stale
`value`. The unique-key checks run on the merged result.

`partial` is optional for an environment. Every environment is valid on its own (an empty `spec` is valid), so a plain
base is just an environment that others extend. Mark it `partial: true` only to stop it from being listed directly. A
template (`meta.input`) is partial whether or not it says so.

Without params, `extends` on an environment gives the same values as listing the base before the child in the
deployment's `environments`. Its value is that deployments list one name instead of two, and that it gives a template
an instance.

Sketch: the subscription template in "Shape" above, plus a stack default that a spoke overrides:

```yaml
# stacks/spoke/flags.yaml: the spoke stack's flags
kind: environment
meta: { name: spoke_flags_base }
spec:
  partial: true
  features:
    - { key: enable_nsp, store: constant, value: true }
    - { key: enable_publish, store: constant, value: true }
    - { key: enable_apim, store: constant, value: false }
    # ...

# deploy/hubs/hub02/initech-001/env-spoke.yaml
kind: environment
meta: { name: spoke-initech-001-env }
spec:
  extends: spoke_flags_base
  features:
    - { key: enable_apim, store: constant, value: true }   # replaces the base's entry
```

#### Tenant

| Field                                   | Merge with `extends`                                                            |
| --------------------------------------- | ------------------------------------------------------------------------------- |
| `display_name`, `onboarded`             | child wins                                                                      |
| `geographies`                           | replaced by the child's list when it sets one                                   |
| `environments`                          | base's list, then the child's, as on a deployment; still unique after the merge |
| `properties`, `custom`, `configuration` | deep merge per leaf key, child wins                                             |
| `extends`, `partial`                    | consumed, stripped from the result                                              |

`geographies` is replaced, not joined: it is a data-residency limit, and a child that writes `[us]` must not
silently also allow its base's `europe`.

A tenant base needs `partial: true`: `display_name` is required, so a base without one would fail validation on its
own, which is exactly what `partial` is for on a deployment. A partial tenant is validated after the merge, and only
its leaves are checked against the provider configs' geographies.

`meta.name` is the tenant code, so a base's name must not look like one. A naming rule (for example `_base` suffix) is
a solution choice. Strata only guarantees that a base can never be selected.

Sketch:

```yaml
# customers/_bases/customer-tenant.yaml
kind: tenant
meta:
  name: customer_tenant_base
  input:
    - { key: display_name, type: string }
    - { key: dns_slug, type: string, pattern: "^[a-z][a-z0-9-]*$" }
    - { key: geographies, type: list, default: [europe] }
spec:
  partial: true
  display_name: ${param:display_name}
  geographies: ${param:geographies}         # whole-value token: inserts the list
  properties:
    deploy_profile: { tenancy: isolated, audience: customer }
  configuration:
    dns_slug: ${param:dns_slug}
    sso: { enabled: false }

# customers/initech/tenant.yaml
kind: tenant
meta:
  name: initech
  params: { display_name: Initech, dns_slug: initech, geographies: [us] }
spec:
  extends: customer_tenant_base
  configuration:
    contact: "…"
    sso: { enabled: true }
```

Each tenant in the example also has a `tenant.env.yaml` with an empty `spec`, and its tenant lists it in
`environments`. That is a placeholder, not something to template: drop both until a tenant has a value to put there.

### Effect on the example (estimate)

A spoke goes from about 355 lines in seven files to about 80, and what is left is mostly group 3 (subscription ID,
SPN object ID, flags, customers). The ring generator could then write a leaf of a dozen lines, or be retired. These
numbers are estimates from the diffs above, not a migration that was carried out.

## Related Decisions

- [ADR-0024](../decisions/0024-tenant-defaults-merge.md): tenant defaults, the second merge axis next to `extends`,
  applied after it
- [deployment-tokens.md](../design/deployment-tokens.md): `${deployment:name}` and `${deployment:tenant}`, run-time
  tokens for the running deployment
- [cross-document-value-references.md](../design/cross-document-value-references.md): `${value:}`, its dotted path
  walk and the scalar rule that rules 5 and 6 reuse
- `deployment-token-paths.md` (work doc): the general `${deployment:PATH}` that option B would depend on

## Remaining Work / Open Questions

Phases, if G is accepted:

- [ ] **Phase 1, deployment templates.** `meta.input`, `meta.params`, `${param:}` substitution in
      `_resolve_dict()`, per-instance validation, diagnostics that name the leaf and the template field, schema
      export, config docs (`config/deployment`).
- [ ] **Phase 2, one chain resolver for three kinds.** Lift the walk out of `deployment_resolution.py`. Add
      `extends`/`partial` to `environment` and `tenant` with the merge tables above. Resolve in the order
      environments, tenants, deployments. Replace resolved environments and tenants in the index, and reject bases
      wherever a document can be listed, selected or read by `${value:}`. Then templates for both kinds.
- [ ] **Phase 3, a rendered view.** A way to print the instantiated document of a leaf (likely an option on an
      existing command), since what runs is no longer in any single file.
- [ ] **Phase 4, consumer migration.** Spoke stacks first (bootstrap, spoke, ingress, subscription), then customer
      projects, then rings. Use C (a generator) until then.
- [ ] **Phase 5.** Migrate tenants to a shared tenant base and drop empty placeholder tenant environments.
      `provider` is not worth templating.

Open questions:

- [ ] **`meta` of the leaf.** About half of each diff is `annotations.description`, `tags` and comments. Either the
      leaf may leave them out, or a template may also hold `meta.tags`/`annotations` with `${param:}`, merged into
      the leaf's `meta`. Leaving them out is simpler. The per-spoke notes (customer name, ticket numbers, dates
      values were checked) then need a home, such as the spoke's `README.md`.
- [ ] **Repeated params.** `hub` and `spoke` are still typed in every leaf of a spoke (three deployments, four
      environments). Should a deployment pass its params to the environments it lists? An environment shared by
      several deployments (the subscription's) would then need the same binding from each, so this is left out of
      phase 1.
- [ ] **Name rule.** Should a template be able to require a leaf name pattern (`bootstrap-${param:spoke}`), as a
      check only, so names stay literal but cannot drift?
- [ ] **Template detection.** Is "has `meta.input`" enough, or should a template say so (`spec.partial: true` plus
      `input`, or a `template: true` flag)?
- [ ] **Constraint vocabulary.** Is `pattern` + `references` + `type` + `default` enough, or is `enum` needed (for
      example `environment_name`)?
- [ ] **Removing an inherited entry.** A child can override a base's variable or flag, but not remove it, as for
      deployments today. Is that enough, or is a tombstone (`{ key: x, remove: true }`) needed?
- [ ] **Raw beside resolved.** Keeping both models of an environment or tenant in the index doubles what the index
      holds for those kinds. Is a source-location map on the resolved model enough instead?
- [ ] **Forwarding shorthand.** A middle base that forwards most inputs unchanged repeats each one twice (in
      `input` and as `k: ${param:k}`). A shorthand such as `input: [{ from: parent, except: [stack] }]`
      (redeclare the parent's inputs and forward them as they are) would remove that. Leave it out until a real chain
      shows the repetition hurts. Explicit is easier to read in a review.
- [ ] **Chain depth.** No limit is proposed. Should `validate` warn above some depth (say 4), since every level makes
      the rendered view (phase 3) more necessary?
- [ ] **Interaction with B.** Could an input default to a layer segment (`default: ${layer:hub}`) so a leaf can omit
      what its folder already states? Only worth it once G is in use.

## Changelog

- 2026-11-11: Created. Options A to G compared against a consumer hub-and-spoke solution. G proposed, with F folded
  in and C as the stopgap.
- 2026-11-11: Chains designed. Rules 2, 3, 7 and 8 changed from one shared set of params (the leaf's params visible
  to the whole chain) to per-document scope: each level has its own `input` and binds its parent through `params`,
  so a middle base can forward, fix, rename or combine. Added the "Chains" section with a three-level example, the
  two-pass resolution, the (name, scope) cache key, and open questions on a forwarding shorthand and chain depth.
- 2026-11-11: Designed `extends` for environments and tenants: one chain resolver used per kind in the order
  environments, tenants, deployments; resolved models replace raw ones in the index (because `${value:}` and the
  token checks read the index); bases rejected wherever a document is listed, selected or read; merge tables per
  kind (stores replaced as whole entries, `geographies` replaced, tenant `environments` appended). Phases 2 and 5
  updated.
- 2026-11-11: Made anonymous: the source solution, its customers, codes, IDs and naming scheme replaced by
  stand-ins. Structure and counts unchanged.
