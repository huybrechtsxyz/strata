# Parameterised bases (`input`, `params`, `${param:}`) - Work

- Status: abandoned (superseded by [base-parameters-v2.md](base-parameters-v2.md): values files instead of typed
  inputs on the base)
- Last updated: 2026-11-12

## Overview

A solution that runs the same stack in many places ends up with many leaves that are copies of each other. They differ
only in names built from a few identifiers, plus a handful of real values. `extends` removes duplicated *values*. It
cannot remove duplicated *identity*: a base cannot say "my environments are `hub-<this hub>-env` and
`sub-<this spoke>-env`", because it has no way to refer to the leaf that extends it.

This doc compares seven ways to template those leaves and proposes one (option G): a base declares typed **inputs** in
`meta.input`, uses them in its `spec` as `${param:key}`, and a leaf binds them in `meta.params`. It works like a generic
type with constraints: the base is `Spoke<hub, spoke, tenant>`, and the leaf is `Spoke<hub01, acme-001, acme>`.

The solution comes in two parts, built in that order:

- **Part 1, parameterised bases (option G).** Templates, `input`, `params`, `${param:}`, chains, and `extends` for
  environments and tenants. This is the base, and it is designed in detail.
- **Part 2, shared params documents (`params_from`).** A leaf takes its params from one document per spoke instead
  of repeating them. It sits on top of part 1 and changes only where a leaf's params come from. The concept is
  agreed; the details may still change once part 1 is in use.

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

Part 1 below. Part 2 adds shared params documents on top of it.

## Part 1: parameterised bases (option G)

### Shape

A base declares its inputs in `meta.input`, with the same shape as an environment's variables:

```yaml
# stacks/bootstrap/deployment.yaml
apiVersion: strata.omp.com/v2
kind: deployment
meta:
  name: bootstrap_deployment_base
  name_rule: bootstrap-${param:spoke}  # a leaf's name must match, after binding
  input:
    - key: hub
      type: string
      schema: { pattern: "^hub[0-9]{2}$" }
      description: "Hub of the spoke."
    - key: spoke
      type: string
      schema: { pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" }
    - key: tenant
      type: string
      references: tenant          # must name an existing tenant
    - key: provider
      type: string
      references: provider
      default: azure-westeurope
spec:
  partial: true                        # required on a template (rule 10)
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
    - { key: code, type: string, schema: { pattern: "^[a-z][a-z0-9]*$" } }
    - { key: seq, type: string, schema: { pattern: "^[0-9]{3}$" } }
    - { key: subscription_id, type: string, schema: { format: uuid } }
    - { key: spn_object_id, type: string, schema: { format: uuid } }
    - { key: environment_name, type: string, schema: { enum: [dev, tst, prd] }, default: prd }
spec:
  partial: true
  properties:
    environment_info:
      subscription_id: ${param:subscription_id}
      sequence_number: ${param:seq}
      deploy_code: ${param:code}
      environment_name: ${param:environment_name}
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

1. **`input`** is a list of `{key, type, schema?, references?, default?, description?}`. `key` is unique within
   the document. An input without `default` is required.
   - `type` takes the names variables already use (`VariableValueType`): `string`, `number`, `bool`, `object`,
     `list`, `map`.
   - `schema` is a plain JSON Schema fragment (2020-12), checked by the shared evaluator that attributeconfig uses.
     This follows attributeconfig's rule of no strata-invented vocabulary: `pattern`, `enum`, `format`, `minLength`,
     `items` and the rest come with it. A `default` must satisfy its own `schema`.
   - `references` is the one strata keyword: the value must name an existing document of that kind (`tenant`,
     `provider`, `environment`, ...). JSON Schema cannot express that. A base (`partial`) never satisfies it.
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
9. **`name_rule`** (optional, on a template) is a string with `${param:}` tokens. After binding, the `meta.name` of
   every *leaf* below it must equal the result, or it is an error (`name_rule_mismatch`) that gives both names.
   Names stay plain text in the file, so the index never depends on substitution, but they cannot drift from their
   params. A middle base does not have to match its parent's rule. Only leaves do, and with several rules in a
   chain every one of them applies.
10. **A template is a document with `meta.input`, and it must say `spec.partial: true` itself.** `input` without
    `partial: true` (left out, or `false`) is an error (`template_not_partial`) on every kind. Partial is never
    implied: someone reading the `spec` sees that the document is not deployable without scrolling back to `meta`,
    and every base, template or not, is marked the same way. No separate `template` flag.
11. **Templates do not template `meta`.** A leaf's `meta` is its own and holds `name` and `params`. `tags`,
    `labels` and `annotations` are optional, as they are today, and are never filled in from a base. Notes that
    belong to one leaf (who the customer is, which ticket delivered a value, when it was last checked) go in the
    folder's `README.md`, or in the leaf's own `annotations` if a tool needs to read them.

### Chains: `input` and `params` on every level

There is no depth limit and no depth warning. `extends` has none today (`_resolve_dict()` recurses until a document
without `extends` and stops only on a cycle), and G adds none. Any threshold would be arbitrary and soon become
noise; the rendered view (phase 3) is what keeps a deep chain readable.

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
    - { key: stack, type: string, schema: { enum: [bootstrap, spoke, ingress] } }
    - { key: hub, type: string, schema: { pattern: "^hub[0-9]{2}$" } }
    - { key: spoke, type: string, schema: { pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" } }
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
  name_rule: bootstrap-${param:spoke}
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
again against base1's (`schema.pattern`). A middle base may make a constraint stricter for its own children, but it cannot
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
- A template is never deployable and never listed directly: it must declare `partial: true` (rule 10). For
  environments, a deployment that lists a template environment by name (instead of its leaf) is an error.
- `name_rule` (rule 9) is checked on the leaf, after binding and before the merged result is validated.
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
any check that reads them. Every consumer then sees inherited fields with no change of its own.

The raw document is **not** kept beside it. Instead, resolution records an **origin map**: for each field path of
the resolved model, the document and field it came from (`spec.features[enable_apim] <- spoke-initech-001-env`,
`spec.features[enable_nsp] <- spoke_flags_base`, `spec.properties.state_vault.name <- subscription_env_base, filled
in from params.code and params.seq`). Diagnostics use it to point at the file the author wrote, and the rendered view
(phase 3) uses it to show where each value comes from. A second copy of the document would serve neither. Templates
still keep their raw `spec` (see "Templates are validated per instance"), because that is what instantiation reads.
The same map is built for deployments.

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

A child cannot **remove** an inherited entry, only override it (switch a flag off, give a variable another value).
That matches deployments today. There is no remove marker; add one only when a real case cannot be handled by an
override.

`partial` is optional for an environment. Every environment is valid on its own (an empty `spec` is valid), so a plain
base is just an environment that others extend. Mark it `partial: true` only to stop it from being listed directly. A
template (`meta.input`) must mark it (rule 10).

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

A plain tenant base needs `partial: true`: `display_name` is required, so a base without one would fail validation on
its own, which is exactly what `partial` is for on a deployment. A tenant template needs it too, like every template
(rule 10). A partial tenant is validated after the merge, and only
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
    - { key: display_name, type: string, schema: { minLength: 1 } }
    - { key: dns_slug, type: string, schema: { pattern: "^[a-z][a-z0-9-]*$" } }
    - { key: geographies, type: list, schema: { items: { enum: [europe, us] } }, default: [europe] }
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

## Part 2: shared params documents (`params_from`)

Status: concept agreed, details open. Built after part 1, and the design below may change once part 1 is in use.

### The problem it solves

With part 1 alone, every leaf states its own params. One spoke folder has seven leaves, and almost all of them repeat
the same values:

```
deploy/hubs/hub01/acme-001/
  deploy-bootstrap.yaml   params: { hub: hub01, spoke: acme-001, tenant: acme }
  deploy-spoke.yaml       params: { hub: hub01, spoke: acme-001, tenant: acme }
  deploy-ingress.yaml     params: { hub: hub01, spoke: acme-001, tenant: acme }
  env-bootstrap.yaml      params: { spoke: acme-001 }
  env-spoke.yaml          params: { spoke: acme-001 }
  env-ingress.yaml        params: { spoke: acme-001 }
  env-subscription.yaml   params: { spoke: acme-001, code: acme, seq: "001", subscription_id: …, spn_object_id: … }
```

The cost is typing, not correctness: the folder check on `layers.segments`, `name_rule` (rule 9) and references
already catch a typo. That is why part 1 can ship without part 2.

The obvious alternative, a deployment passing its params to the environments it lists, is rejected. An environment
shared by several deployments (the subscription's is listed by every deployment of the spoke) would get several,
possibly conflicting, bindings, and `${value:environment.sub-acme-001-env...}` would no longer name one document.

### Shape

A new, small kind holds plain values. It has no `extends`, no tokens and no constraints; the constraints stay on the
templates that consume the values.

```yaml
# deploy/hubs/hub01/acme-001/spoke.yaml
kind: parameters
meta:
  name: spoke-acme-001
spec:
  values:
    hub: hub01
    spoke: acme-001
    tenant: acme
    code: acme
    seq: "001"
    subscription_id: 00000000-0000-0000-0000-000000000001
    spn_object_id: 00000000-0000-0000-0000-000000000002
```

A leaf points at it with `meta.params_from`, and can still add or override values in `params`:

```yaml
# deploy-bootstrap.yaml
kind: deployment
meta:
  name: bootstrap-acme-001
  params_from: spoke-acme-001
spec:
  extends: bootstrap_deployment_base

# env-subscription.yaml
kind: environment
meta:
  name: sub-acme-001-env
  params_from: spoke-acme-001
spec:
  extends: subscription_env_base

# deploy-ingress.yaml: one value of its own on top of the shared set
kind: deployment
meta:
  name: ingress-acme-001
  params_from: spoke-acme-001
  params: { provider: azure-westus2 }
spec:
  extends: ingress_deployment_base
```

The spoke folder, before and after:

```
part 1 only (each leaf repeats)            with part 2 (one source)
-----------------------------------        -----------------------------------
deploy-bootstrap.yaml  hub, spoke, tenant  spoke.yaml             all 7 values
deploy-spoke.yaml      hub, spoke, tenant  deploy-bootstrap.yaml  params_from
deploy-ingress.yaml    hub, spoke, tenant  deploy-spoke.yaml      params_from
env-bootstrap.yaml     spoke               deploy-ingress.yaml    params_from + provider
env-spoke.yaml         spoke               env-bootstrap.yaml     params_from
env-ingress.yaml       spoke               env-spoke.yaml         params_from + flags
env-subscription.yaml  spoke, code, seq,   env-ingress.yaml       params_from + customers
                       subscription_id,    env-subscription.yaml  params_from
                       spn_object_id
```

Every real fact about the spoke (group 3 in "The case") ends up in `spoke.yaml`: a reviewer reads one file to know
what the spoke is. The leaves keep only their exceptions.

### Rules (draft)

- P1. **`params_from`** names a `parameters` document, or a list of them (`[hub-hub01, spoke-acme-001]`, later wins),
    so values shared by a whole hub (`provider`) can live once per hub.
- P2. **A leaf takes only what its parent declares.** `env-spoke`'s template asks only for `spoke`, so it ignores
    `subscription_id`. This relaxes rule 2 (an unknown key is an error) for values from `params_from` only. Keys
    written in `params` stay strict.
- P3. **`params` wins over `params_from`.** The origin map records which one supplied each value, so the rendered view
    shows "`provider` from `params`, `hub` from `spoke-acme-001`".
- P4. **Every value is still checked** by the parent's `input` (`type`, `schema`, `references`), as if it were written
    in `params`.
- P5. **Unused values are reported** (`unused_parameter`, a warning): a key of a parameters document that no leaf using
    it consumes. This makes up for P2: with a typo such as `spokee: acme-001`, `spokee` is unused and `spoke` is
    reported missing.
- P6. **Plain values only** (rule 7): no `${...}` in a parameters document. It depends on nothing, so it is loaded
    first and can be read at load time.

### How it sits on top of part 1

Part 2 changes only where a leaf's params come from. It adds one step before pass 1 of "Chains":

```
params_from (part 2)  ──┐
                        ├─► one params map ─► pass 1: bind up the chain ─► pass 2: fill in + merge ─► validate
params (part 1)  ───────┘                     (input, schema, references)   (extends, merge rules)
```

Templates, `input`, `${param:}`, `partial: true`, chains and per-document scope, `name_rule`, per-instance
validation, the origin map, and `extends` for environments and tenants are unchanged. Consequences:

- **No breaking change.** A leaf with plain `params` stays valid, so part 1 ships without part 2.
- **Gradual adoption.** One spoke can move to `spoke.yaml` while the others keep inline `params`.
- **No loss if dropped.** If defaults from the folder (open questions) turn out to be enough, part 1 has nothing to
  undo.

### Trade-offs

- **Covers environments.** Defaults from the folder would only cover deployments (only they have `layers`).
- **Cost:** a new kind, `params_from` on three kinds, and one indirection: a leaf is not readable without
  `spoke.yaml`, which makes the rendered view (phase 3) more necessary.
- **Signal:** with part 2, the leaves of a spoke differ only by name and parent. That is close to option D (one table,
  N deployments), which is rejected above. The difference is that the files remain, so audit paths, pipelines and the
  folder check keep working. Part 2 is "D, but keeping the files", and that is the reason it is acceptable.

## Related Decisions

- [ADR-0024](../decisions/0024-tenant-defaults-merge.md): tenant defaults, the second merge axis next to `extends`,
  applied after it
- [deployment-tokens.md](../design/deployment-tokens.md): `${deployment:name}` and `${deployment:tenant}`, run-time
  tokens for the running deployment
- [cross-document-value-references.md](../design/cross-document-value-references.md): `${value:}`, its dotted path
  walk and the scalar rule that rules 5 and 6 reuse
- `deployment-token-paths.md` (work doc): the general `${deployment:PATH}` that option B would depend on

## Remaining Work / Open Questions

Phases 1 to 5 are part 1. Phase 6 is part 2 and starts only once part 1 is in use.

- [ ] **Phase 1, deployment templates.** `meta.input` (with `schema` checked by the shared JSON Schema evaluator,
      and `references`), `meta.params`, `meta.name_rule`, `${param:}` substitution in `_resolve_dict()`,
      per-instance validation, the origin map, diagnostics that name the leaf and the template field, schema
      export, config docs (`config/deployment`).
- [ ] **Phase 2, one chain resolver for three kinds.** Lift the walk out of `deployment_resolution.py`. Add
      `extends`/`partial` to `environment` and `tenant` with the merge tables above. Resolve in the order
      environments, tenants, deployments. Replace resolved environments and tenants in the index, and reject bases
      wherever a document can be listed, selected or read by `${value:}`. Then templates for both kinds.
- [ ] **Phase 3, a rendered view.** A way to print the instantiated document of a leaf (likely an option on an
      existing command), since what runs is no longer in any single file. It shows, from the origin map, which
      document and which param each value comes from.
- [ ] **Phase 4, consumer migration.** Spoke stacks first (bootstrap, spoke, ingress, subscription), then customer
      projects, then rings. Use C (a generator) until then.
- [ ] **Phase 5.** Migrate tenants to a shared tenant base and drop empty placeholder tenant environments.
      `provider` is not worth templating.
- [ ] **Phase 6, part 2: shared params documents.** The `parameters` kind, `meta.params_from` on deployment,
      environment and tenant, rules P1 to P6, the `unused_parameter` warning, origin map entries for values from
      `params_from`. Revisit the draft design against real leaves first.

Open questions:

- [ ] **Part 2 details.** The concept is agreed; to settle when phase 6 starts: the kind's name (`parameters`)
      and where its files live, whether `params_from` takes a list, whether P2's filtering is right or a leaf should
      name the keys it takes, and whether `unused_parameter` is a warning or an error.
- [ ] **Forwarding shorthand.** A middle base that forwards most inputs unchanged repeats each one twice (in
      `input` and as `k: ${param:k}`). A shorthand such as `input: [{ from: parent, except: [stack] }]`
      (redeclare the parent's inputs and forward them as they are) would remove that. Leave it out until a real chain
      shows the repetition hurts. Explicit is easier to read in a review.
- [ ] **Defaults from the folder (option B).** Could an input default to a layer segment (`default: ${layer:hub}`),
      so a deployment leaf can omit what its folder already states? It covers deployments only, so part 2 is the
      planned answer to repeated params. Is this still worth it after part 2, or does part 2 make it unnecessary?

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
- 2026-11-12: Seven open questions settled. Constraints are a JSON Schema `schema` fragment plus `references`, with
  `type` taken from `VariableValueType` (rule 1; this also fixes `boolean` to `bool` and adds `map`). New rules 9
  (`name_rule`, checked on leaves), 10 (`input` implies `partial`, which removes a contradiction between the template
  and environment sections) and 11 (templates do not template `meta`). An origin map replaces keeping the raw
  document beside the resolved one. No remove marker for inherited entries, and no depth limit or warning. Examples
  updated to the `schema:` form. Still open: repeated params, the forwarding shorthand, and defaults from the
  folder.
- 2026-11-12: Rule 10 changed: a template must write `partial: true` itself (`template_not_partial` otherwise),
  instead of `input` implying it. Explicit in the `spec`, and the same marking for every base. `partial: true` added
  back to every template example.
- 2026-11-12: Added part 2, shared params documents (`params_from`), as the planned answer to repeated params:
  the `parameters` kind, draft rules P1 to P6, how it sits on top of part 1, and trade-offs. The doc is now split
  into part 1 (the base, built first) and part 2 (built after, design may change). Phase 6 added. The "Repeated
  params" open question becomes "Part 2 details", and the folder-defaults question asks whether part 2 makes it
  unnecessary.
