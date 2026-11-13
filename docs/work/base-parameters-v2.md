# Parameterised bases with parameter sets (`parameterset`, `params`, `${param:}`) - Work

- Status: draft
- Last updated: 2026-11-12

## Overview

A solution that runs the same stack in many places ends up with many leaves that are copies of each other. They differ
only in names built from a few identifiers, plus a handful of real values. `extends` removes duplicated *values*. It
cannot remove duplicated *identity*: a base cannot say "my environments are `hub-<this hub>-env` and
`sub-<this spoke>-env`", because it has no way to refer to the leaf that extends it.

This doc proposes the Helm model:

- a new kind, **`parameterset`**, is a named map of plain values: strata's values file, in Helm's sense;
- a deployment, environment or tenant names its parameter sets in **`meta.params`**, a map from a role to a set
  (`params: { spoke: spoke-acme-001, customer: customer-acmep01 }`);
- **`${param:role.key}`** anywhere in that document, and in every base of its `extends` chain, reads from those
  values, so a base reads `${param:spoke.hub}` or `${param:customer.code}`, and sets combine without collisions;
- `environment` and `tenant` get **`extends`**, as `deployment` has today;
- **constraints** on values are ordinary **attributeconfig** schemas, not a new vocabulary.

This is version 2. Version 1 (`base-parameters.md`, work doc) declared typed inputs on every base and bound them per
level, like C# generics. It worked, but needed three new fields, eleven rules and per-document scoping, while
parameter sets need one kind, one field and one token. "Why not typed inputs on the base" records what changed and why.

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

Verified against the source on 2026-11-12.

- **Only `deployment` has `extends`.** `DeploymentSpecModel.extends`
  ([deployment_model.py](../../src/strata/models/deployment_model.py)). `environment`, `tenant` and `provider` have
  no inheritance.
- **Merge rules.** `merge_deployment_specs()` ([deployment_service.py](../../src/strata/services/deployment_service.py))
  works on raw dicts. Nested blocks merge per leaf key. `stages`, `providers` and `assets` merge by key. `environments`
  is the base's list followed by the child's. Other lists are replaced. Chains, cycles and the tenant-defaults axis
  (ADR-0024) are handled in
  [deployment_resolution.py](../../src/strata/controllers/deployment_resolution.py), which has no depth limit.
- **A base is a validated document.** A `partial: true` base goes through the model like any other deployment. The
  resolver takes `model.spec.model_dump()` from the index. `tenant`, `environments[]` and `providers[].provider` are
  `PlatformName` (`^[a-z][a-z0-9_-]*$`), declared as references, and checked by `validate_references()`. **A string
  such as `hub-${param:hub}-env` in a base fails today, both on the pattern and as a reference.** That fact shapes
  the design (see "Templates are validated per instance").
- **`meta` is never merged.** Each document's `meta` is its own.
- **Tokens are resolved at run time.** `var`, `secret`, `feature`, `output`, `value`, `deployment` and `artifact`
  ([value_tokens.py](../../src/strata/utils/value_tokens.py)). Each of them resolves *values*. None of them may
  appear in a reference field, and `${deployment:}` only knows `name` and `tenant`.
- **Environments merge at run time by list order.** `merge_environment_models()`
  ([environment_service.py](../../src/strata/services/environment_service.py)) merges `variables`, `secrets` and
  `features` by `key`, a later entry replacing the whole earlier one; `properties` and `custom` deep-merge.
- **`${value:}` reads the index directly** (`index.get(kind, name)`). The example already depends on it: its ingress
  environment reads `${value:tenant.acme.spec.configuration.dns_slug}`.
- **attributeconfig checks free-form blocks with plain JSON Schema** (2020-12), with "no strata-invented
  vocabulary" ([attributeconfig_model.py](../../src/strata/models/attributeconfig_model.py)). `view: document` checks
  each matching document's own block (`BLOCKS_BY_KIND`); `view: effective` checks the merged blocks of each
  deployable deployment, with the selector evaluated on the resolved deployment and `partial` bases skipped.

## Options

### A. Generic base, bound in `extends`

`extends: bootstrap_deployment_base<hub: hub01, spoke: acme-001>`. Explicit, but `extends` stops being a plain
`PlatformName`, so every tool that reads it needs a parser.

### B. Use the layer segments as the parameters

`layers.segments.hub/spoke` are on every deployment and checked against the folder. A general
`${deployment:spec.layers.segments.spoke}` (`deployment-token-paths.md`, work doc) in the base's `environments`.
Rejected: references resolve at load time, before run-time tokens, and environments have no `layers`.

### C. Generate the files with a script

As the solution already does for rings. No strata change, but the copies stay and drift. **Kept as the stopgap.**

### D. Expansion: one table, N deployments

A `deployment-set` expands each stack per row. Rejected: deployments stop being files, which breaks audit paths,
`deploy list`, per-leaf pipeline targets and the `layers.follows` folder check.

### E. Overlays (Kustomize style)

`extends` already is this for `spec`. The gaps are `meta` and identity, so it reduces to G.

### F. Derive the naming-rule values

Build `kv-${code}-tfstate-${seq}` in the base. **Folded into G**: with `${param:}` in an environment base, group 2
disappears.

### G. Parameter sets, `meta.params`, `${param:}` (proposed)

The rest of this doc. In effect it is "D, but keeping the files": one file per spoke holds its values, and every leaf
remains a document in its folder.

## Design

### Shape

A parameter set holds plain keys and does not know its role; the document that names it gives the role (rule 13). The
snippets after the first leave out `apiVersion` for brevity; a real document needs one (any `strata.` prefix), or the
loader skips the file without a word.

```yaml
# deploy/hubs/hub01/acme-001/spoke.yaml
apiVersion: strata.omp.com/v2
kind: parameterset
meta:
  name: spoke-acme-001
  labels: { role: spoke }       # optional: for attributeconfig selectors
spec:
  values:
    name: acme-001
    hub: hub01
    tenant: acme
    code: acme
    seq: "001"
    subscription_id: 00000000-0000-0000-0000-000000000001
    spn_object_id: 00000000-0000-0000-0000-000000000002
```

A base uses `${param:}` and binds nothing:

```yaml
# stacks/bootstrap/deployment.yaml
kind: deployment
meta:
  name: bootstrap_deployment_base
  name_rule: bootstrap-${param:spoke.name}  # optional: a leaf's name must match
  params: { hub: bootstrap-defaults }       # optional: defaults, like a chart's values.yaml
spec:
  partial: true
  workspace: bootstrap
  tenant: ${param:spoke.tenant}
  providers:
    - slot: azure
      provider: ${param:hub.provider}       # azure-westeurope, from bootstrap-defaults
  layers:
    follows: spoke-path
    segments:
      hub: ${param:spoke.hub}
      spoke: ${param:spoke.name}
  environments:
    - solution-env
    - hub-${param:spoke.hub}-env
    - sub-${param:spoke.name}-env
    - bootstrap-${param:spoke.name}-env

# stacks/bootstrap/defaults.yaml
kind: parameterset
meta: { name: bootstrap-defaults, labels: { role: hub } }
spec:
  values:
    provider: azure-westeurope
```

A leaf names its values and adds only what is its own:

```yaml
# deploy/hubs/hub01/acme-001/deploy-bootstrap.yaml
kind: deployment
meta:
  name: bootstrap-acme-001
  params: { spoke: spoke-acme-001 }
spec:
  extends: bootstrap_deployment_base
```

Environments work the same way. The naming-rule values move into the base:

```yaml
# stacks/bootstrap/subscription-env.yaml
kind: environment
meta:
  name: subscription_env_base
  name_rule: sub-${param:spoke.name}-env
spec:
  partial: true
  properties:
    environment_info:
      subscription_id: ${param:spoke.subscription_id}
      sequence_number: ${param:spoke.seq}
      deploy_code: ${param:spoke.code}
      environment_name: prd
    state_vault:
      name: kv-${param:spoke.code}-tfstate-${param:spoke.seq}
      resource_group_name: rg-${param:spoke.code}-tfstate-prd
  variables:
    - key: tf_state_storage_account
      store: constant
      type: string
      value: sttfstate${param:spoke.code}${param:spoke.seq}
    - { key: spn_object_id, store: constant, type: string, value: "${param:spoke.spn_object_id}" }
    # ...

# deploy/hubs/hub01/acme-001/env-subscription.yaml
kind: environment
meta:
  name: sub-acme-001-env
  params: { spoke: spoke-acme-001 }
spec:
  extends: subscription_env_base
```

The spoke folder then holds one parameter set and seven short leaves. Every real fact about the spoke (group 3) is in
`spoke.yaml`; the leaves keep only their exceptions (flags, ingress customers, a provider binding):

```
deploy/hubs/hub01/acme-001/
  spoke.yaml             all values of the spoke
  deploy-bootstrap.yaml  params + extends
  deploy-spoke.yaml      params + extends
  deploy-ingress.yaml    params + extends
  env-bootstrap.yaml     params + extends
  env-spoke.yaml         params + extends + flags switched on
  env-ingress.yaml       params + extends + customers
  env-subscription.yaml  params + extends
```

### Rules

1. **`parameterset`** is a new kind. `spec.values` is a map of any YAML: scalars, lists and nested maps. It has no
   `extends` and no `partial`. Values are plain: any `${` in a parameter set is an error, so a parameter set depends on
   nothing and is loaded first. The existing escape is allowed: `$${var:x}` is the literal text `${var:x}`, as in any
   other document. A parameter set must not hold secrets: it is plain text in git. Secrets stay
   `${secret:}` in a document's `spec`.
2. **`meta.params`** on a deployment, environment or tenant is a map from a role to a parameter set, or to a list of
   them (`params: { hub: hub-hub02, spoke: spoke-initech-001 }`). A role is a `PlatformName`. Every set named must
   be a `parameterset` document; one set may be named under more than one role.
3. **A chain has one set of values per role.** Every document in an `extends` chain may name parameter sets. For
   each role, its sets are merged root first, then down the chain, then the leaf's, with later winning: a base's
   sets are the defaults (a chart's `values.yaml`), and the leaf's are the overrides. Within a list, later wins.
   Maps merge per leaf key, as Helm does; lists and scalars are replaced. So a base's `hub: bootstrap-defaults` and a
   leaf's `hub: hub-hub02` combine key by key, and the hub's own `provider` overrides the default one.
4. **`${param:role.key}`** in any document of the chain reads the chain's merged values of that role, including in
   the leaf's own `spec`. `${param:spoke.a.b}` walks into nested maps with the same dotted walk as `${value:}`;
   `${param:spoke}` alone is the role's whole map (rule 6). A role no document of the chain names is a missing key
   (rule 7).
5. **Where it may appear:** any string in `spec`, including reference fields (`tenant`, `environments[]`,
   `providers[].provider`) and `layers.segments`. Not in `meta.name`, `meta.params` or `spec.extends`: names, values
   files and chains stay static, so the index never depends on substitution.
6. **Whole value or embedded.** A token that is the entire value is replaced by the value with its type, so a list or
   a map can be inserted. A token inside a longer string needs a scalar. This is the `value_reference_not_scalar`
   rule of `${value:}`.
7. **A missing key is an error** that names the document, the field, and the parameter sets that were consulted:
   `bootstrap_deployment_base spec.environments[1]: ${param:spoke.hub} not found in spoke: [spoke-acme-001]
   (for bootstrap-acme-001)`. There is no default syntax in the token: defaults belong in a base's parameter set.
8. **Substituted text is never scanned again**, so a value cannot insert a `${secret:}`. Run-time tokens written in a
   `spec` (`${var:x}`) are left alone and resolved at run time as today. `param` is a load-time token: it is not one
   of the run-time `VALUE_TOKEN_KINDS`, so no `${param:}` can reach run-time resolution, and `$${param:x}` stays the
   literal text `${param:x}`.
9. **`partial: true` is always written, never implied.** A base meant only to be extended says `spec.partial: true`
   itself, as a deployment base does today. A document that is not partial is deployable (or listable, or
   selectable) and must resolve every `${param:}` from its chain's values. There is no separate template flag.
10. **`meta.name_rule`** (optional, on any document in a chain) is a string with `${param:}` tokens. After
    substitution, every *leaf* below it must have exactly that `meta.name`, or it is an error (`name_rule_mismatch`)
    that gives both names. Names stay plain text, but cannot drift from their values.
11. **`meta` is not templated.** A leaf's `meta` is its own. `tags`, `labels` and `annotations` stay optional and are
    never filled in from a base or a parameter set. Notes about one leaf go in the folder's `README.md`, or in the
    leaf's own `annotations` when a tool must read them.
12. **Unused values are allowed, and reported.** A parameter set may hold more than any one document needs, as a Helm
    values file does. A spoke's file serves seven leaves, and each reads what it needs. `strata validate` lists the
    keys of a set that no document reads through any role it is named under, as an `info` finding
    (`param_unused`). Not a warning: `validate --strict` fails on warnings, and an unused value is not an error.
13. **The role comes from `params`, not from the set.** A parameter set holds plain keys and does not know its role;
    the document that names it gives the role, and bases read `${param:<role>.<key>}`. `meta.labels.role` on a set is
    optional, for attributeconfig selectors. See "Combining parameter sets" for why.

### Naming

| Name                 | Is                                                       |
| -------------------- | -------------------------------------------------------- |
| `kind: parameterset` | the document: one named set of parameters (instance data) |
| `spec.values`        | its content, a plain field name                          |
| `meta.params`        | which parameter set a document uses for each role        |
| `${param:role.key}`  | reading one value                                        |

Not `values` as the kind, although Helm made "values file" the familiar term. In strata, "value" already means
*run-time, resolved* values in four places: the `${value:}` token, "value token" as the umbrella for `${var:}`,
`${secret:}` and `${feature:}`, Helm's `values.yaml` and the `strata-values.yaml` strata writes for Helm modules, and
the script provisioner's `values` field and `STRATA_VALUES`. A parameter set is substituted at *load* time, and the
design depends on keeping the two apart. A `kind: values` next to a `${value:}` token would also be one letter away.
"param" is used nowhere else.

Not `parameterconfig`. The `*config` kinds (`providerconfig`, `topologyconfig`, `attributeconfig`) are rules about
other documents: the registry entry for a type, or a schema. A parameter set is instance data, one per spoke. The rules
about parameter sets are attributeconfigs ("Constraints").

Not `parameters`. Kind names are singular nouns for one document (`tenant`, `environment`); `parameterset` follows the
compound style of `attributeconfig`.

### Chains

There is no depth limit and no depth warning, as for `extends` today. The rendered view (phase 4) keeps a deep chain
readable.

Each level of a chain writes what is its own directly in its `spec`; the values are only for what differs per leaf.
A stack base and the bootstrap base on top of it:

```yaml
# base1: stacks/common/stack-deployment.yaml. Shared by every spoke stack.
kind: deployment
meta: { name: stack_deployment_base, params: { hub: stack-defaults } }   # provider: azure-westeurope
spec:
  partial: true
  tenant: ${param:spoke.tenant}
  providers:
    - { slot: azure, provider: "${param:hub.provider}" }
  layers:
    follows: spoke-path
    segments: { hub: "${param:spoke.hub}", spoke: "${param:spoke.name}" }
  environments:
    - solution-env
    - hub-${param:spoke.hub}-env
    - sub-${param:spoke.name}-env

# base2: stacks/bootstrap/deployment.yaml. The bootstrap stack.
kind: deployment
meta: { name: bootstrap_deployment_base, name_rule: "bootstrap-${param:spoke.name}" }
spec:
  partial: true
  extends: stack_deployment_base
  workspace: bootstrap
  environments:
    - bootstrap-${param:spoke.name}-env     # appended after base1's list

# leaf
kind: deployment
meta: { name: bootstrap-acme-001, params: { spoke: spoke-acme-001 } }
spec:
  extends: bootstrap_deployment_base
```

Values merged for the leaf: `hub` from `stack-defaults`, `spoke` from `spoke-acme-001`. A spoke behind `hub02` also
names the hub's set (`params: { hub: hub-hub02, spoke: spoke-initech-001 }`), and its `provider: azure-westus2`
overrides the default `hub.provider`. Every leaf behind `hub02` repeats `hub: hub-hub02`: a set does not name the
sets it builds on, and a role is never implied by the folder. Repeating one name per leaf is the price of every
value being explicit, and far less than repeating the values themselves.

### Combining parameter sets

A customer layer needs values from two sets: the spoke's (which hub, which subscription) and the customer project's
(its code). The leaf names both, each under its role:

```yaml
# deploy/hubs/hub01/acme-001/acmep01/customer.yaml
kind: parameterset
meta: { name: customer-acmep01, labels: { role: customer } }
spec:
  values: { code: acmep01, project: p01 }

# deploy/hubs/hub01/acme-001/acmep01/deploy-customer.yaml
kind: deployment
meta:
  name: customer-acme-001-acmep01
  params: { spoke: spoke-acme-001, customer: customer-acmep01 }
spec:
  extends: customer_deployment_base

# stacks/customer/deployment.yaml
kind: deployment
meta:
  name: customer_deployment_base
  name_rule: customer-${param:spoke.name}-${param:customer.code}
spec:
  partial: true
  workspace: customer
  tenant: ${param:spoke.tenant}
  layers:
    follows: project-path
    segments: { hub: "${param:spoke.hub}", spoke: "${param:spoke.name}", project: "${param:customer.code}" }
  environments:
    - solution-env
    - hub-${param:spoke.hub}-env
    - sub-${param:spoke.name}-env
    - deploy-${param:customer.code}-env

# deploy/hubs/hub01/acme-001/acmep01/env-deploy.yaml
kind: environment
meta:
  name: deploy-acmep01-env
  params: { customer: customer-acmep01 }
spec:
  extends: deploy_env_base                  # deploy_code: ${param:customer.code}
```

**Why roles (rule 13).** Without them, both sets would put a flat `code` in one pool: the spoke's `code: acme` (the
subscription's deploy code, in `sttfstateacme001`) and the customer's `code: acmep01` (the project's). Merged, the
later one wins, and in the customer layer every `${param:code}` silently becomes `acmep01`, also where a base meant the
subscription's. With roles, `spoke.code` and `customer.code` are different keys, and each base says which one it means.

The role is given where the set is used, in `params`, not written inside the set:

- **collisions cannot happen**: two sets under two roles never share a key, whatever they hold, so no convention or
  attributeconfig is needed to keep them apart;
- **bases stay reusable**: they name a role, never a parameter set (`${param:spoke.hub}`, not `spoke-acme-001`);
- **defaults layer per role**: a base's `params: { hub: stack-defaults }` and a leaf's `hub: hub-hub02` merge key by
  key (rule 3);
- **a set is reusable under any role**, and holds plain keys, so it reads like a values file;
- **the cost**: a base hard-codes the roles it reads, so moving a key from every spoke's set into the hub's set
  changes its path (`spoke.provider` to `hub.provider`), and the bases that read it change with it. A key a base
  reads under a role no document names is a missing key (rule 7), so a missed base is an error, never a silent
  wrong value.

Rejected alternatives:

- **Address a set by name in a base** (`${param:spoke-acme-001.hub}`). The name is per spoke, so every spoke would
  need its own base: the copying this design removes.
- **One role key inside each set, by convention** (`values: { spoke: { hub: hub01 } }`, `params` a plain list). The
  previous version of this design. It needed every set to follow the convention and an attributeconfig to enforce
  it; one set that wrote another role's key overrode it silently. Replaced by roles in `params` on 2026-11-12.

### Resolution

For each kind, in this order:

1. **Parameter sets.** Loaded and validated first; they reference nothing.
2. **Environments**, then **tenants**, then **deployments**, each with the chain walk below. Then
   `_merge_tenant_defaults()` runs as today (ADR-0024), on the resolved tenant.

For one document:

1. Walk the chain to its root (cycle detection, missing ancestor, as today).
2. Merge the parameter sets along the chain, per role: root first, leaf last (rule 3).
3. Substitute `${param:}` in each document's raw `spec` with those values.
4. Merge the specs as today, root first, child wins.
5. Fold in the tenant's defaults (`_merge_tenant_defaults()`, ADR-0024), after `extends` as today, now with the
   substituted `tenant`.
6. Check `name_rule`, then validate the merged result as an ordinary document, and check its references (below).

**Two passes, not one recursion.** `_resolve_dict()` today recurses from a document to its root and caches the merged
spec per name. With parameter sets the values come from the leaf and are applied to every base, so the walk becomes
two passes: collect the chain and its parameter sets going up, then substitute and merge coming down. It is rewritten,
not extended. Cycle and missing-ancestor tracking stay keyed by name; only the merged-spec cache is keyed by (name,
merged values).

**References are checked on the resolved document.** `validate_references(index)` walks the indexed models. Today
that covers an inherited `tenant`, because the base that wrote it was checked. After substitution the concrete
reference (`hub-hub01-env`) exists only in the resolved document, so each resolved document gets its own reference
check, with the location from the origin map. Without it, `_merge_tenant_defaults()`, which skips an unknown tenant
silently and relies on that check, would drop the defaults of a mistyped `${param:spoke.tenant}` without a word.

**The resolver returns more than models.** `resolve_deployment_chains()` returns `dict[str, DeploymentModel]` to four
callers (`solution_context`, `promote_command`, `value_controller`, `deployment_resolution`). Each resolved
deployment now also carries its merged values (for `block: params` in phase 3 and the rendered view in phase 4) and
the origins of its substituted strings. The return type changes once, in phase 1.

### Templates are validated per instance

A base that uses `${param:}` cannot be validated on its own: its reference fields hold strings such as
`hub-${param:spoke.hub}-env`. So:

- A document whose `spec` contains `${param:}` is a **template**, whether it is a `partial: true` base or a leaf
  (rule 4 allows `${param:}` in a leaf's own `spec`, and the loader validates every document as it reads it, so a
  leaf with `tenant: ${param:spoke.tenant}` would otherwise fail before the resolver runs). Its `meta` is validated,
  and every `${param:}` in it is checked for syntax. Its `spec` is *not* validated against the model, and its
  references are not resolved.
- **The merged result is validated**, per leaf. A bad value shows up on the leaf: an unknown environment, an unknown
  tenant, a pattern the model enforces. The diagnostic names the leaf, the base and the base's field, using the
  origin map below.
- A document without `${param:}` is unchanged: still validated standalone.
- The token checks accept `${param:}` in a template. Today any `${...}` that is not a run-time token is reported as
  malformed (`value_tokens.py`), which would flag every template.

**How a template sits in the index.** `IndexEntry.model` is a validated model today, and about ten consumers rely on
it: the resolver (`entry.model.spec.model_dump()`), and the `resolved.get(name, entry.model)` fallbacks for partial
documents in `semantic_checks.py` (five checks), `promotion_controller.py`, `effective_images.py`,
`value_controller.py` and `reachable_documents.py`. A template gets its own model class: `meta` validated, `spec` a
plain dict. An `isinstance` check tells it apart, and each of those consumers is changed to skip it as it skips a
partial base today, or to read the resolved document.

### Origin map

Resolution records, for each field path of a resolved document, where it came from: the document and field, and for a
substituted value, the parameter set and key:

```
spec.environments[1]            <- stack_deployment_base, "hub-${param:spoke.hub}-env", spoke.hub = hub01 from spoke-acme-001
spec.providers[azure].provider  <- stack_deployment_base, hub.provider = azure-westeurope from stack-defaults
spec.features[enable_apim]      <- spoke-acme-001-env
```

Diagnostics use it to point at the file the author wrote. The rendered view uses it to show where each value comes
from. The raw document is not kept beside the resolved one.

It is built in two steps. Phase 1 records the origin of each *substituted* string, which substitution knows as it
goes; that is all the diagnostics need. The full map, with the document behind every merged field, needs
`merge_deployment_specs()` to track origins, which it does not today; it comes with the rendered view in phase 4.

### `extends` for environments and tenants

Both get the same field and chain rules as deployments, with a kind-specific merge.

**One resolver, three kinds.** The chain walk in `deployment_resolution.py` (cycle detection, missing ancestor,
poisoning, cache) is not deployment-specific. Lift it into a resolver that takes a kind and that kind's merge
function, and call it in the order of "Resolution".

**Resolved documents replace raw ones in the index** for environments and tenants. They are read in many places:
per-environment token checks (`declared_keys()`, `validate_document_tokens()`), `merge_environment_models()`,
geography checks, and `${value:}`. Replacing them in the index means every consumer sees inherited fields with no
change of its own. Deployments keep their separate resolved map, as today.

Two costs, to weigh in phase 2 against a resolved map per kind (as deployments have):

- `DocumentIndex.add()` raises on a duplicate, so the index needs a replace operation, used only by the resolver.
- Checks that run on the index then see merged documents, so a finding about an inherited field names the leaf's
  file, not the base's that wrote it, unless it reads the origin map.

**Bases cannot be used.** A `partial: true` environment or tenant stays in the index so `extends` can find it, but
every other lookup rejects it:

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

Stores are not merged field by field: a variable moved from `store: constant` with a `value` to a Key Vault store with
a `ref` must not keep the stale `value`. The unique-key checks run on the merged result. A child can override an
inherited entry, not remove it, as for deployments today.

Every environment is valid on its own, so `partial: true` on an environment base only stops it from being listed
directly, except when it uses `${param:}`, where rule 9 applies.

```yaml
# stacks/spoke/flags.yaml
kind: environment
meta: { name: spoke_flags_base }
spec:
  partial: true
  features:
    - { key: enable_nsp, store: constant, value: true }
    - { key: enable_publish, store: constant, value: true }
    - { key: enable_apim, store: constant, value: false }

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

`geographies` is replaced, not joined: it is a data-residency limit, and a child that writes `[us]` must not silently
also allow its base's `europe`. A tenant base needs `partial: true` (`display_name` is required). `meta.name` is the
tenant code, so a base's name must not look like one; strata guarantees only that a base can never be selected.

```yaml
# customers/_bases/customer-tenant.yaml
kind: tenant
meta: { name: customer_tenant_base }
spec:
  partial: true
  display_name: ${param:tenant.display_name}
  geographies: ${param:tenant.geographies}  # whole-value token: inserts the list
  properties:
    deploy_profile: { tenancy: isolated, audience: customer }
  configuration:
    dns_slug: ${param:tenant.dns_slug}
    sso: { enabled: false }

# customers/initech/params.yaml
kind: parameterset
meta: { name: tenant-initech, labels: { role: tenant } }
spec:
  values: { display_name: Initech, dns_slug: initech, geographies: [us] }

# customers/initech/tenant.yaml
kind: tenant
meta:
  name: initech
  params: { tenant: tenant-initech }
spec:
  extends: customer_tenant_base
  configuration:
    sso: { enabled: true }
```

Each tenant in the example also has a `tenant.env.yaml` with an empty `spec`, listed in its `environments`. That is a
placeholder, not something to template: drop both until a tenant has a value to put there.

### Constraints: attributeconfig

Values are checked with attributeconfig, in its two existing views. No `input` declarations, no new vocabulary: the
same JSON Schema evaluator, the same `$ref` safety, the same diagnostics.

**One parameter set at a time (`view: document`)**: the shape of what a file holds. A set does not know its role, so
the selector uses the optional `meta.labels.role`:

```yaml
kind: attributeconfig
meta: { name: spoke-values-shape }
spec:
  target:
    kind: parameterset
    block: values
    view: document
    selector: { meta.labels.role: spoke }
  schema:
    type: object
    required: [name, hub, tenant, subscription_id]
    properties:
      name:            { type: string, pattern: "^[a-z][a-z0-9]*-[0-9]{3}$" }
      hub:             { type: string, pattern: "^hub[0-9]{2}$" }
      subscription_id: { type: string, format: uuid }
```

**What a consumer receives (`view: effective`)**: the merged values of a deployable deployment, per role, after its
chain's defaults and its `params`. The top-level keys are the roles.

```yaml
kind: attributeconfig
meta: { name: bootstrap-needs }
spec:
  target:
    kind: deployment
    block: params
    view: effective
    selector: { spec.workspace: bootstrap }   # evaluated on the resolved deployment
  schema:
    required: [spoke, hub]
    properties:
      spoke: { required: [name, hub, tenant] }
      hub:
        required: [provider]
        properties:
          provider: { enum: [azure-westeurope, azure-westus2] }
```

Both views are needed because values are layered: a hub's set does not fill the `spoke` role, so "everything is
there" is an effective-view check, and "each value has the right shape" is a document-view check.

Changes to attributeconfig:

- `BLOCKS_BY_KIND` gains `parameterset: {values}`, and `block` gains `values`.
- `block` gains `params` for `view: effective`: the merged values of a deployment. Environments and tenants also
  receive values; extending the effective view to them is phase 3.
- Without any attributeconfig, values are still checked by rule 7 (missing key) and by validating the merged result.
  attributeconfig makes the error earlier and clearer where a solution wants it.

What a schema cannot check, and does not need to: that a value names an existing tenant or provider. JSON Schema
cannot express it, and the reference check on the merged document catches it.

In Helm, the schema sits next to the chart (`values.schema.json`). Here it is a separate document, matched by
selector, as attributeconfig already works for every other kind. The base does not say which keys it needs; the
missing-key error and the rendered view make up for that distance.

### Effect on the example (estimate)

A spoke goes from about 355 lines in seven files to about 80 in eight (one parameter set of about 15 lines, seven leaves
of 5 to 10 lines, plus the exceptions). What is left is mostly group 3, in one place. These numbers are estimates
from the diffs in "The case", not a migration that was carried out.

## Why not typed inputs on the base (version 1)

Version 1 declared `meta.input` (type, schema, references, default) on each base, bound it with an inline
`meta.params` map on the child, and scoped `${param:}` per document, so a middle base could forward, fix, rename or
combine values for its parent. A shared parameter set was planned as a second part on top.

Version 2 makes the parameter set the design, and drops the rest:

| Version 1                                                  | Version 2                                                              |
| ---------------------------------------------------------- | ---------------------------------------------------------------------- |
| `meta.input` with type, schema, references, default        | attributeconfig on parameter sets and on merged values                   |
| defaults on inputs                                         | a base names a defaults parameter set (rule 3)                           |
| inline `params` maps, strict about unknown keys            | `params` maps roles to parameter sets; unused values reported (rule 12)  |
| per-document scope, forwarding (`hub: ${param:hub}`)       | one set of values per role per chain                                   |
| a middle base fixes a value for its parent (`stack: x`)    | a middle base writes its concrete part in its own `spec`               |
| two passes (bind up, fill down), cache by (name, scope)    | one merge of values, then substitution; cache by (name, values)        |
| shared parameter set as part 2, relaxing the strict rules    | it is the design                                                       |

What version 2 gives up:

- **Up-front constraints in the base.** Moved to attributeconfig: same power (JSON Schema), separate document.
- **Declared keys per base.** A chain shares one set of values per role, and a base does not declare what it reads.
  Roles (rule 13) keep sets from colliding, and the missing-key error names what a base needed.
- **Fixing a value for the parent.** Not needed in the example: every case was a base writing its own part of a list
  or a field, which `extends` already does.

## Related Decisions

- [ADR-0024](../decisions/0024-tenant-defaults-merge.md): tenant defaults, the second merge axis next to `extends`,
  applied after it
- [deployment-tokens.md](../design/deployment-tokens.md): `${deployment:name}` and `${deployment:tenant}`, run-time
  tokens for the running deployment
- [cross-document-value-references.md](../design/cross-document-value-references.md): `${value:}`, its dotted path
  walk and the scalar rule that rules 4 and 6 reuse
- `base-parameters.md` (work doc): version 1, typed inputs on the base
- `deployment-token-paths.md` (work doc): the general `${deployment:PATH}` that option B would depend on

## Remaining Work / Open Questions

Phases. Each step is shippable on its own; tests are listed with the step that needs them.

- [x] **Phase 1, parameter sets and deployment bases.** Done 2026-11-13.
  - [x] 1a. **The `parameterset` kind.** `PlatformKind`, the model (`spec.values`: any YAML), its service in
        `SERVICE_BY_KIND`, the `${` ban with the `$${` escape allowed, schema export (`strata schema`), the config
        reference page. Harmless alone: nothing reads it yet. Tests: a valid set loads; `${var:x}` in a value is an
        error; `$${var:x}` is not; a duplicate name is `duplicate_document`. Done 2026-11-12:
        `parameterset_model.py`, `parameterset_service.py`, a schema example, `docs/config/parameterset.md`. The
        ban covers keys as well as values and names every offending path in one `value_error`.
  - [x] 1b. **`meta.params` and `${param:}` in free-form strings.** `meta.params` on deployments (a map from a role to
        one set or a list, each a `parameterset`), the values merge per role along the chain (rule 3), substitution
        (rules 4, 6, 7, 8) in strings
        that already pass validation as written (`properties`, `custom`, `variables[].value`, ...), the two-pass walk
        and the (name, values) cache, the resolver's new return type. No templates yet: a `${param:}` in a typed or
        reference field still fails at load. Proves the merge and substitution without touching the index. Tests:
        merge order per role (root, chain, leaf; later in a list wins; maps per leaf key, lists replaced); two roles
        with the same key stay apart; one set under two roles; a role no document names is a missing key; whole-value and
        embedded tokens; a list inserted whole; a missing key names the document, field and sets; substituted text
        is not rescanned; `${var:}` is left for run time; one base shared by two leaves with different values (the
        cache). Done 2026-11-12, see the changelog for what differs from this plan.
  - [x] 1c. **Templates.** A document whose `spec` holds `${param:}` loads as a template (meta validated, spec a
        plain dict), partial or not. The token checks accept `${param:}` in it. Every consumer that falls back to
        `entry.model` (the list in "Templates are validated per instance") skips templates or reads the resolved
        document. References are checked on each resolved document, so a wrong `${param:spoke.tenant}` is an error,
        not silently missing tenant defaults. The risky step. Tests: `tenant`, `environments[]`,
        `providers[].provider` and `layers.segments` filled from values; an unknown environment after substitution is
        reported on the leaf; a template leaf; each listed consumer with a template in the index; `${param:}` in
        `meta.name`, `meta.params` or `spec.extends` is an error (rule 5). Done 2026-11-12, see the changelog.
  - [x] 1d. **`meta.name_rule`** (rule 10), `name_rule_mismatch` with both names. Tests: match, mismatch, a rule on a
        middle base applies to every leaf below it. Done 2026-11-12, see the changelog.
  - [x] 1e. **Diagnostics with origins, and unused values.** The origin of each substituted string, so a finding on a
        resolved document names the leaf, the base and the base's field, and the role, parameter set and key. The
        `param_unused` info finding (rule 12): a key of a set that no document reads under any role the set is named
        under. Tests: the message of a bad reference in a base, and of a missing key; an unused key reported once per
        set, a key read by one of seven leaves not reported, `validate --strict` still passing. Done 2026-11-13, see
        the changelog.
  - [x] 1f. **Docs and decision.** An ADR: a new kind, and a token class substituted at load time next to the run-time
        tokens. The config reference (`kind: parameterset`, deployment `meta.params`, `meta.name_rule`). A how-to with
        a full dummy solution: a hub and two spokes, a customer project, the parameter sets, the bases and the leaves,
        file by file, and where each file lives. It shows one layout that works; strata does not prescribe one.
        CHANGELOG "Added". Done 2026-11-13, see the changelog.
- [x] **Phase 2, one chain resolver for three kinds.** Lift the walk out of `deployment_resolution.py`. Add
      `extends`, `partial` and `meta.params` to `environment` and `tenant` with the merge tables above. Resolve in
      the order parameter sets, environments, tenants, deployments. Decide first between replacing resolved
      environments and tenants in the index (a replace operation on `DocumentIndex`, and findings that need the
      origin map to name the right file) and a resolved map per kind. Reject bases wherever a document can be
      listed, selected or read by `${value:}`. Tests: the merge tables row by row; `geographies` replaced, not
      joined; `environment_not_listable`, `tenant_not_selectable`, `value_reference_to_base`; a store change on a
      variable keeps no stale `value`. Docs and CHANGELOG. Done 2026-11-13, see the changelog.
- [x] **Phase 3, attributeconfig.** `kind: parameterset, block: values` (document view) and `block: params` (effective
      view of deployments, from the merged values phase 1 returns), then the effective view for environments and
      tenants. Tests: both examples in "Constraints". Docs and CHANGELOG. Done 2026-11-13, see the changelog.
- [x] **Phase 4, a rendered view.** The full origin map (`merge_deployment_specs()` tracking the document behind
      every merged field). Print the resolved document of a leaf (likely an option on an existing command), with
      which document and which parameter set each value comes from. Tests: the origins of a two-base chain with two
      sets. Docs and CHANGELOG. Done 2026-11-13, see the changelog.
- [ ] **Phase 5, consumer migration.** Spoke stacks first (bootstrap, spoke, ingress, subscription), then customer
      projects, then rings, then tenants (dropping the empty tenant environments). Use C (a generator) until then.
      `provider` is not worth templating.

Open questions:

- [x] ~~**Where parameter sets live.**~~ Answered 2026-11-12: strata prescribes no layout. The 1f how-to shows a full
      dummy solution with one layout that works.
- [x] ~~**Hub values.** Should a parameter set name the sets it builds on, so a leaf names only the spoke's?~~
      Answered 2026-11-12: no. Every leaf names its hub's set (`hub: hub-hub02`); repeating one name per leaf is far
      less than repeating the values, and every value stays explicit (see "Chains").
- [x] ~~**Role aliases.** Should `params` take a map of role to set?~~ Answered 2026-11-12: yes, and it replaces the
      role-key convention: rules 2, 3, 4 and 13 and "Combining parameter sets".
- [x] ~~**Enforce the convention in strata?**~~ Closed 2026-11-12: there is no convention left to enforce; roles come
      from `params`, so sets cannot collide.
- [x] ~~**Unused values.** Worth a lint that reports keys no consumer reads?~~ Answered 2026-11-12: yes, in
      `strata validate`, as the `info` finding `param_unused` (rule 12, plan step 1e).
- [x] ~~**Defaults from the folder (option B).**~~ Answered 2026-11-12: no. Values come only from sets a document
      names explicitly; nothing is implied by the folder.
- [x] ~~**Commands on a template base.**~~ Answered 2026-11-12: a template base is treated as a partial deployment is
      today, and a template leaf is listed by its resolved document. Built in 1c.

## Changelog

- 2026-11-12: Created as version 2 of `base-parameters.md`. Values files (`kind: parameters`) named in `meta.params`
  replace typed inputs on the base: one set of values per chain, merged root first with the leaf winning, so a base
  can carry defaults. Constraints move to attributeconfig (document view on parameter sets, effective view on merged
  values). Kept from version 1: explicit `partial: true`, `name_rule`, per-instance validation, the origin map,
  `extends` for environments and tenants with the same merge tables, and the resolution order.
- 2026-11-12: Kind renamed from `parameters` to `parameterset`, and "values file" to "parameter set" throughout.
  Added "Naming": why not `values` (strata already uses "value" for run-time values, and `${value:}` exists), why not
  `parameterconfig` (`*config` kinds are rules about other documents) and why not `parameters` (kinds are singular).
  The kind-name open question is closed.
- 2026-11-12: Role keys (option 3b). Rule 13: each parameter set keeps its values under one top-level role key
  (`spoke`, `customer`, `hub`, `tenant`), labelled `meta.labels.role`, so bases read `${param:spoke.hub}`. New
  section "Combining parameter sets" with the customer layer (spoke and customer sets together), the `code` collision
  that motivates it, and the rejected alternatives (a set's name in a base; role aliases in `params`). All examples
  moved to role keys; the attributeconfig examples enforce the convention with `additionalProperties: false`. Rule 3
  now states that deep merge is required for role keys, which closes the map-merge-depth question. The
  key-collisions question is replaced by "Role aliases" and "Enforce the convention in strata?"; the hub-values
  question is restated for role keys.
- 2026-11-12: Implementation plan reviewed against the source. Phase 1 split into 1a-1f, each shippable, with tests
  per step; docs, CHANGELOG and an ADR added to the plan. The design gains what the review found: a leaf can be a
  template too (the loader validates every document, so templates are any document with `${param:}`, not only
  partial ones); how a template sits in the index and the ten consumers that read `entry.model`; `param` as a
  load-time token the run-time token checks must accept; references checked on each resolved document (with the
  tenant-defaults consequence); the resolver as two passes with a new return type; the origin map split between
  phase 1 (substituted strings) and phase 4 (every merged field); the costs of replacing documents in the index in
  phase 2; the `$${` escape; a note on `apiVersion` in the snippets. One open question added (commands on a template
  base).
- 2026-11-12: Open questions answered. **Roles move into `params`**: `meta.params` is a map from a role to a set
  (`params: { spoke: spoke-acme-001 }`), values merge per role, and a set holds plain keys without a role key
  (rules 2, 3, 4, 13; every example; "Combining parameter sets", where the role-key convention is now the rejected
  alternative; the attributeconfig document-view example; the version-1 comparison). Unused values are reported by
  `strata validate` as the `info` finding `param_unused` (rule 12, step 1e). No defaults from the folder and no set
  naming another set: values come only from sets a document names, and every leaf behind a hub names the hub's set
  ("Chains"). Strata prescribes no file layout; the 1f how-to gets a full dummy solution. Commands treat a template
  base as a partial deployment (built in 1c). All seven open questions are closed.
- 2026-11-12: Step 1a built: the `parameterset` kind, its plain-value check (keys and values; `$${` allowed), a
  schema example and the config reference page. No CHANGELOG entry yet: that waits for 1f, when the kind is usable.
  Open for 1b: `${value:parameterset.<name>.spec.values.<key>}` already resolves through the generic `${value:}`
  path; decide whether to forbid it, so `${param:}` stays the one way to read a set.
- 2026-11-12: Step 1b built. `DeploymentMetaModel.params` (role -> set or list, `References(parameterset)`);
  `utils/param_tokens.py` (merge per role with `deep_merge`, one-pass substitution); the resolver split into
  `_resolve_chain()` (pass one, cycles and missing ancestors as before) and `_merged_spec()` (pass two, cached per
  (name, sets named), since the sets determine the values). Differences from the plan:
  - **Return type.** `resolve_deployments()` is new and returns `ResolvedDeployment` (model, merged `params`,
    `param_sets`, `chain`); `resolve_deployment_chains()` stays as its projection to models, so its four callers are
    unchanged. Phases 3 and 4 move the callers that need values to `resolve_deployments()`.
  - **Where findings land.** `param_missing` and `param_not_scalar` go on the document that holds the token (often a
    base), with the leaf in the message: `base spec.properties.envs[1]: ... (for leaf)`, once per leaf. The full
    origin map is still 1e.
  - **An unknown set** in `meta.params` is one `unknown_reference` (the reference walk now follows map values,
    `meta.params{}`); the leaf is left unresolved without `param_missing` findings on top.
  - **Token syntax.** `${param:}` is `PARAM_TOKEN_PATTERN` in `value_tokens.py`, outside `VALUE_TOKEN_KINDS`. The
    malformed-token check accepts it only in a deployment (`param_tokens=`), so a partial base keeps its tokens and
    any other kind still reports one as malformed.
  - **Rendering.** An embedded `true`/`false` renders as YAML writes it (`${value:}` writes Python's `True`), a date
    as ISO text, `null` or a list or map is `param_not_scalar`.
  - **Shared walk.** `${value:}`'s dotted walk moved to `utils/value_paths.py`, so `${param:}` walks named list
    items (`${param:spoke.subnets.app.cidr}`) the same way.
  - The `${value:parameterset...}` question above stays open.
- 2026-11-12: Step 1c built. Deployments only; environments and tenants follow in phase 2.
  - **The template model.** `DeploymentTemplateModel`: `meta` as a deployment's, a spec that keeps every field as
    written (`extra="allow"`) except `partial` (a bool) and `extends` (a reference), and a load-time check that
    every `${...}` in the spec is well formed, `${param:}` included. The loader picks it through
    `TEMPLATE_SERVICE_BY_KIND` when the raw spec holds a well-formed `${param:}` (`holds_param_tokens()`; an
    escaped `$${param:}` does not count). `spec.partial` joins rule 5's list: it decides deployability before any
    value is read.
  - **Per-instance validation.** In `_merged_spec()`, a template document's substituted spec is validated as a
    `DeploymentSpecModel` and dumped, so it merges exactly as a loaded document's would. A failure is reported on
    the template with the leaf: `base spec.tenant: invalid after ${param:} substitution: ... (for initech)`.
  - **References on the resolved document.** When a chain holds a template, the merged model's references are
    checked (`references.unresolved_references()`), reported on the leaf. Names a non-template document of the
    chain writes itself are skipped: the ordinary walk reported them already. A literal name inside a template
    (`workspace: mian`) is reported once per leaf until the origin map (1e) can put it on the template.
  - **Consumers.** One helper, `deployment_of(entry, resolved)`: the resolved model, else the document as loaded,
    else `None` for a template that did not resolve. Used by five checks in `semantic_checks.py`,
    `promotion_controller.py`, `list_deployments()` (a template base listed as written) and
    `value_controller.resolve_deployment()` (a `UsageError` for a template base). `artifact_usage.py` skips every
    partial base before resolving, so a template base no longer turns "unused artifacts" off. `effective_images.py`,
    `artifact_tokens.py` and `reachable_documents.py` take a resolved model already; `value_references.py` and
    `graph_controller.py` read any model generically and need nothing.
  - **The `param_tokens` flag** on `unresolved_value_tokens()` (1b) is gone: a deployment that holds a well-formed
    `${param:}` is now always a template and is token-checked only resolved. `find_malformed_value_tokens(
    param_tokens=True)` stays, for the template's load-time check.
  - **Open:** the exported JSON schema does not know templates, so the YAML editor underlines `${param:}` in a
    typed field (`tenant`) that `strata validate` accepts. Candidate for 1f: allow a `${param:...}` pattern
    alongside each `PlatformName` pattern in the deployment schema, or document it.
- 2026-11-12: Step 1d built. `DeploymentMetaModel.name_rule` (a non-empty string; any `${...}` in it must be a
  well-formed `${param:}`, via `value_tokens.find_non_param_tokens()`). `_check_name_rules()` in the resolver runs for
  each deployable deployment that resolved, over every document of its chain, the leaf's own rule included: the rule
  is filled with the leaf's values (`substitute_params()`) and compared with the leaf's `meta.name`.
  - A mismatch is `name_rule_mismatch` on the leaf's file at `meta.name`, naming the expected name, the rule's
    document and the rule as written. The leaf stays resolved: the document is sound, only its name is wrong, as
    with an unknown reference.
  - A rule that reads a missing key is `param_missing` on the rule's document, for the leaf, as for a spec token.
  - A partial base is not held to a rule. `meta` still never makes a document a template: only `spec` does.
  - Not built: a rule check when the spec itself failed to resolve. That leaf already has an error, and a second
    finding about its name would repeat the cause.
- 2026-11-13: Step 1e built.
  - **Origins.** `substitute_params()` takes an optional `origins` map and records, per location
    (`spec.environments[1]`), the text as written and every token filled into it (`ParamOrigin`, `ParamRead`). The
    resolver names the set a value came from as the last set of the role that holds the key.
  - **References are checked per template document, not on the merged leaf.** 1c checked the resolved leaf, so a
    finding sat on the leaf with no way back to the field (merging moves list positions). Now each template's
    substituted spec is reference-checked right after its per-instance validation, where the location is the
    template's own field and the origin map applies directly:
    `base spec.environments[1]: unknown environment 'hub-hub02-env'. Available: [...], from
    'hub-${param:spoke.hub}-env': spoke.hub = 'hub02' (spoke-initech) (for initech)`. A rejected value gets the same
    suffix. The merged-document check (`_resolved_reference_problems()`) and `references.model_references()` are
    gone. Unknown references stay findings, not blocking, as for a loaded document.
  - **Literal names once.** A reference a template writes without a token is reported once, on the template, without
    "(for leaf)": the 1c per-leaf duplicate is fixed. Non-template documents of the chain are covered by the ordinary
    reference walk, and tenant defaults by the tenant's own.
  - **`param_unused`** (rule 12), `info`: per top-level key of a set no document reads, at `spec.values.<key>`. A
    key is read when some chain (any deployment's, partial bases included) holds `${param:<role>.<key>...}` in a
    document's spec or `name_rule` and names the set under that role; `${param:<role>}` alone reads every key. A set
    no `meta.params` names gets one finding at `meta.name`. Top-level keys only: a nested key that is never read
    (`network.dns` beside a read `network.cidr`) is not reported. Revisit if that turns out to matter.
  - Still for phase 4: the origin of every merged field (which document wrote it), for findings on a merged leaf
    and the rendered view.
- 2026-11-13: Step 1f built; phase 1 is done.
  - [ADR-0061](../decisions/0061-parameter-sets-and-load-time-param-token.md): parameter sets named per role, and
    `${param:}` as a load-time token class beside the run-time tokens. Status `accepted` (the design questions were
    answered on 2026-11-12). Its consequences record the two open points below.
  - Config reference: `meta.params` and `meta.name_rule` in `docs/config/deployment.md`, with a "Parameter sets"
    section; `docs/config/parameterset.md` links the how-to and the ADR.
  - How-to `docs/how-to/parameterised-deployment-bases.md`: two hubs, two spokes and a customer project, 20 files,
    each shown with its path. `tests/repo/test_howto_parameter_sets.py` builds the solution from the page's own
    code blocks and requires it to validate strictly with nothing unused, and checks the merged values the page
    shows, so the page cannot drift. Providers are not templated in it: a binding equal to the slot's default is a
    `provider_binding_redundant` warning, which `--strict` would fail on.
  - `docs/index.rst`: the how-to, and `config/parameterset` (missing from the toctree since 1a).
  - CHANGELOG "Added" under Unreleased.
  - Open, for the user: (1) the exported JSON schema does not know templates (the editor may underline `${param:}`
    in a typed field); (2) whether `${value:parameterset...}` should be forbidden.
- 2026-11-13: Both open points answered.
  - **Editor schema: built.** `export_schema()` lets every value position under a deployment's spec, at any depth,
    also take a string holding `${param:<role>.<key>}` (an `anyOf` branch to one `$defs/ParamToken`); `meta`,
    `extends` and `partial` keep their schema, as `strata validate` rejects a token there. `render_schema()` is
    unchanged. See `docs/design/schema-export.md`. Phase 2 adds environment and tenant to `_TEMPLATE_SPECS`.
  - **`${value:parameterset...}` stays allowed.** It reads one named set directly, raw (no merge, no role), which is
    useful where `${param:}` cannot reach yet (an environment, before phase 2) and for solution-wide constants read
    by shared documents (a workspace, a module). No correctness reason to forbid it: unlike an artifact's image fields
    (ADR-0058), nothing is silently wrong. The one flaw was `param_unused` counting only `${param:}` reads, so a key
    read through `${value:}` was reported unused: `_value_token_reads()` now counts those reads too, and a set read
    only that way is not reported as named by nothing. `docs/config/parameterset.md` says when to use which.
- 2026-11-13: Phase 2 built: one chain resolver for deployments, environments and tenants.
  - **Decision: resolved environments and tenants replace their documents in the index**, not a resolved map per
    kind. They are read in about fifteen places (value assembly, `strata values`, token checks, geography checks,
    tenant defaults, `${value:}`, attributeconfig, reachability); replacing them needed no change in those readers, beyond skipping a template base where one iterates every document.
    `DocumentIndex.replace()` is the replace operation, used only by the resolver. The cost named above is real:
    a finding about a merged environment or tenant names the leaf's file. Origins on such findings wait for the
    full origin map (phase 4).
  - **When:** `resolve_environments_and_tenants()` (`controllers/document_chains.py`) runs in `open_solution()`
    right after loading, so nothing reads an environment or tenant before it is resolved. Its findings (a cycle,
    `param_missing`, a merged spec the model rejects, `name_rule_mismatch`) are load findings, like a document that
    fails its schema, so the cross-document checks do not run on top of them. A missing base is left to the
    reference walk (`unknown_reference` on `spec.extends`). Every chain is resolved against the documents as
    written before any is replaced.
  - **Departure: the document as written is kept** on the entry (`IndexEntry.written`, read through `as_written`),
    against "The raw document is not kept beside the resolved one" in "Origin map". The chain walks need it: once a
    leaf is replaced, its `extends` and its own `${param:}` reads are gone from `model`, and `param_unused` and a
    chain through a non-partial environment must still see them. `${value:}` tokens are also scanned as written,
    so a token a base holds is reported once, on the base.
  - **Shared code.** `document_chains.py` holds the chain walk (cycles, missing ancestors, poisoning), the
    parameter sets of a chain, the values and the `name_rule` check, lifted out of `deployment_resolution.py`,
    which now uses them. `models/template_model.py` holds `ParamSetNames` (the `meta.params` type), the
    `name_rule` check and `DocumentTemplateModel`, the base of the three template models. The merge tables are
    `merge_environment_specs()` and `merge_tenant_specs()` in the services.
  - **Models.** `extends`, `partial`, `meta.params` and `meta.name_rule` on both kinds; `EnvironmentTemplateModel`
    and `TenantTemplateModel` in `TEMPLATE_SERVICE_BY_KIND`. A tenant's `display_name` is required unless the
    tenant is `partial` or has `extends` (as a deployment's `workspace`), so the merged result is where it is
    checked. Rule 10 (`name_rule`) applies to environments and tenants as written ("on any document in a chain").
  - **Bases cannot be used:** `environment_not_listable` (a deployable deployment, resolved, or a non-partial
    tenant listing a partial environment), `tenant_not_selectable`, `value_reference_to_base`. A partial deployment
    or tenant is not checked itself, so a base listing a base is reported once per document built on it. Checks that
    iterate every environment or tenant, and attributeconfig's document view, skip a template; a non-template
    partial base is still checked as the valid document it is.
  - `param_unused` counts reads in environment and tenant chains. The exported environment and tenant schemas
    accept `${param:}` (`_TEMPLATE_SPECS`).
  - Tests: `tests/strata/controllers/test_environment_tenant_chains.py` (the merge tables row by row, `geographies`
    replaced, a store change keeping no stale field, templates for both kinds, the three base codes, a cycle,
    `param_missing`, `name_rule`, `param_unused`, a deployment reading inherited variables and tenant defaults);
    schema-export cases for both kinds. Docs: `docs/config/environment.md` and `docs/config/tenant.md` ("Bases"),
    `docs/config/parameterset.md`; CHANGELOG "Added".
- 2026-11-13: Phase 3 built: attributeconfig on parameter sets, both views of "Constraints".
  - `block` gains `values` and `params`. `BLOCKS_BY_KIND` gains `parameterset: {values}`, so `kind: parameterset,
    block: values, view: document` is an ordinary document view (no new code path); the test that pins the table
    against the spec models now covers `ParameterSetSpecModel`.
  - `block: params, view: effective` is valid for deployment, environment and tenant at once (the plan had
    environments and tenants as a later step; with phase 2 they resolve like deployments, so there was nothing to
    wait for). The schema sees `document_chains.chain_params()`: the chain's sets merged per role, the roles as
    top-level keys, computed from the chain as written, so no new plumbing through the resolver. Matched: a
    deployable deployment (selector on the resolved deployment) or a non-partial environment or tenant. Findings at
    `meta.params.<role>.<key>`, with a message naming the document and the attributeconfig; a schema matching
    nothing is `attributeconfig_unused`, as for the other views.
  - A document that names no set receives `{}`, so a `required` schema fails for it: the selector narrows a schema
    to the documents that use the base (`spec.workspace: bootstrap` in the example).
  - Selectors walk into a map by key (`attributeconfig_evaluator._leaves()`), which the example's
    `meta.labels.role: spoke` needs; before, a path through `meta.labels` or `spec.properties` matched nothing.
  - Tests: `tests/strata/controllers/test_attributeconfig_params.py` (both examples: a bad set, a missing role
    value, a leaf's set overriding a base default, environments and tenants resolved, an unused schema, the label
    selector); model cases for the valid and invalid `(kind, block, view)` combinations. Docs:
    `docs/config/attributeconfig.md` ("Parameter sets: two views"), `docs/config/parameterset.md`,
    `docs/design/dynamic-configuration-validation.md`; CHANGELOG "Added".
- 2026-11-13: Phase 4 built: the rendered view.
  - **A new command, not an option: `strata render KIND NAME`** for deployment, environment and tenant. No existing
    command fits all three kinds: `deploy list` is deployments only and one row each, `path get` is about files,
    `graph` about references. Like `deploy list` it runs nothing and does not call `require_valid()`. Console: the
    chain, the parameter sets per role, then one line per field (value, the document that wrote it, and for a filled-in
    value the text as written with each read and its set). `-o json`: the same, plus `params` and `spec`. Exit 3 for
    a name that does not exist, a `partial: true` base (render one built on it) or a document that does not resolve
    (its own resolution findings are shown).
  - **Departure: the origin map is computed beside the merge, not inside `merge_deployment_specs()`.**
    `controllers/rendered_view.py` substitutes each layer as resolution does (a deployment's tenant defaults, then
    its chain root first; an environment's or tenant's chain), walks every layer and the result into leaf paths, and
    gives each leaf of the result the last layer that wrote it. Paths are normalized so the merge rules need no
    tracking of their own: an item of a named list by its name (`stages[step=...]`, `providers[slot=...]`,
    `variables[key=...]`), a plain list item by its value, so an appended `environments` item and a leaf's
    override of a provider slot both find their writer. The three merge functions stay unchanged. A leaf no layer
    wrote is a schema default (`(default)`); an empty block a merge added is left out.
  - Substitution origins come from `substitute_params(origins=)` per layer, as in 1e; a whole-value token that
    inserted a list or map gives its origin to every leaf below it. The supplying set of each read is
    `document_chains.value_supplier()`, now shared with the per-template findings of 1e.
  - **Secrets are always masked** (`***`), empty or not, in `spec` and in the text as written: every `value` under an
    environment's `spec.secrets`.
  - Not done: findings on a merged environment or tenant still name the leaf's file (phase 2); the origin map could
    point them at the base that wrote the field, a small follow-up now that the map exists.
  - Tests: `tests/strata/controllers/test_rendered_view.py` (the two-base chain with two sets; a named item the leaf
    overrides; plain list items from two bases; a whole-value list; tenant defaults as the bottom layer; an
    environment's stores with secrets masked; a tenant; nothing to render), `tests/strata/commands/
    test_commands_render.py`, and the how-to test checks the origins its page shows. Docs: `strata render` in the
    how-to ("Check it") and `docs/config/parameterset.md` ("Seeing the result"), the CLI table in `docs/README.md`, the command table of the scaffolded agent skill;
    the how-to's "environments do not read parameter sets yet" corrected. CHANGELOG "Added".
- 2026-11-13: Code review of the branch (phases 1-4); findings on the rendered view fixed:
  - **A secret never reads a parameter set.** A `${param:}` in an environment's `secrets[].value` is now a load
    error (`EnvironmentTemplateModel`), enforcing rule 1 instead of only stating it. The render masked the value but
    printed the read (`spoke.db_password = hunter2`) and `-o json` carried it in `reads` and `params`. The render
    also drops the reads of a secret field, as a second guard. The exported schema cannot say it: a secret's `value`
    is typed `Any`.
  - **`-o json` with a date value** no longer fails: `params`, each read's value and every layer are JSON-shaped
    (`pydantic_core.to_jsonable_python`, as `model_dump(mode="json")`), so dates and enums also match between the
    layers and the result.
  - **An environment or tenant whose resolution failed** is no longer rendered as written: a document that needs
    resolving (`document_chains.needs_resolving()`) but was not replaced exits 3 (`render_not_resolved`).
  - **Defaults are never credited to a document.** Layers are dumped with only the fields their author wrote
    (`exclude_unset`), and a deployment template's layer is validated and dumped as resolution shapes it, so a
    schema default (a stage's `enabled`, `locking.strategy`) shows as `(default)`.
  - **One table for named lists and one mask:** the render reads `LIST_MERGE_KEYS` (now public in
    `deployment_service.py`) and `STORE_MERGE_KEYS` (`environment_service.py`) by field, and masks with `deploy
    report`'s `********`.
  - Open, noted for later: (1) ~~a deployment's tenant defaults are credited to the tenant as resolved, so a value the
    tenant inherited through its own `extends` or parameter sets names the tenant, not its base; rendering the
    tenant shows the real origin.~~ Fixed the same day, see below. (2) ~~Rendering one deployment resolves every deployment (as slow as `validate` on a
    large solution), and an error reported once on a base (not per deployment) is left out of the explanation,
    which then only says to run `strata validate`.~~ Fixed the same day, see below.
- 2026-11-13: Review item (1) fixed: tenant defaults in a deployment's render name the tenant document that wrote
  them. The tenant layer is no longer the resolved tenant as one block: its own chain is laid out as the tenant's
  render lays it out (its base, its parameter sets), and only the paths that become deployment defaults are kept
  (`spec.environments`, `spec.properties`, `spec.custom`, as `_merge_tenant_defaults()`). A value the tenant
  inherited from `tenant_base` names `tenant_base` and its file; one filled in from the tenant's parameter set shows
  the text as written and that set (the tenant's sets, not the deployment's). The deployment's chain still
  overrides the tenant layer per field. `rendered_view.py` now builds a `_Writer` per normalized path from each
  chain with that chain's own parameter sets, so two chains with different sets can feed one render. Test:
  `test_tenant_defaults_name_the_tenant_document_that_wrote_them`.
- 2026-11-13: Review item (2) fixed, and the agent skill brought up to date.
  - `resolve_deployments(index, names=)` resolves only the named deployments: their chains and findings, and no
    `param_unused` (a whole-solution finding). `strata render deployment` passes the one name, so it no longer
    costs a full resolution, and every finding the resolver returns is about that deployment, so all of them are
    shown when it does not resolve (before, a finding reported once on a base, with no `(for ...)`, was filtered
    out). Test: `test_rendering_one_deployment_resolves_only_its_chain`.
  - The scaffolded agent skill (`src/strata/templates/agent/strata/SKILL.md`) now covers parameter sets: the
    `${param:}` token (load time, where it may appear, never in `meta` or a secret's value), `extends`/`partial` on
    environments and tenants, `meta.name_rule`, both attributeconfig views, and four pitfalls (plain values and no
    secrets in a set, `strata render` instead of working a chain out by hand, partial environments and tenants
    cannot be used, quoting a token in a YAML flow list, `param_unused` is information). The kinds table is
    generated, so it already listed `parameterset`.
  - Phase 5 (consumer migration) is planned for after the release.
