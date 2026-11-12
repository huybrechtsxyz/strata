# Parameter set (`kind: parameterset`)

One named set of plain values: the instance data of a spoke, a hub, a customer or a tenant. The
values a whole family of documents differs by (a name, a sequence number, a subscription ID) live
in one place, so the documents that use them can share one base.

> **Status: deployments, free-form fields only.** A deployment names sets in `meta.params`, and
> `${param:<role>.<key>}` works in any field that takes free text (`properties`, `custom`,
> `kube_context`, ...). Typed and reference fields (`tenant`, `environments`, `providers`,
> `layers.segments`), environments and tenants come in the next steps of
> `base-parameters-v2.md` (work doc).

## Schema

- `spec.values` — required: a map of any YAML (scalars, lists and nested maps). Its shape is the
  solution's own; strata does not prescribe keys. An empty map is valid
- `meta.name` — the name a document will use in `meta.params`
- `meta.labels` — optional, as on any kind. A label such as `role: spoke` is only for
  [attributeconfig](attributeconfig.md) selectors: a set does not know its role. The document that
  names the set gives the role

A parameter set has no `extends`, no `partial` and no `params`: it builds on nothing.

## Values are plain

No `${...}` token may appear in `spec.values`, in a key or in a value: not `${var:}`, not
`${secret:}`, not `${param:}`. A parameter set depends on nothing, so it can be loaded first, and a
value can never insert a token into the document that reads it. A token in a parameter set is a
load error that names every key holding one:

```text
spec.values: a parameter set holds plain values, but values.name holds a ${...} token. Move the
token into the document that reads the value, or write $${...} for the literal text.
```

The usual escape works: `$${TOKEN}` is the literal text `${TOKEN}`, as in any other document (for
example a Helm chart's own substitution syntax).

A parameter set is plain text in git, so it must not hold secrets. Keep `${secret:}` in the `spec`
of the document that needs the secret.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: parameterset
meta:
  name: spoke-acme-001
  labels: { role: spoke }   # optional: for attributeconfig selectors
spec:
  values:
    name: acme-001
    hub: hub01
    tenant: acme
    code: acme
    seq: "001"
    subscription_id: 00000000-0000-0000-0000-000000000001
    network:
      cidr: 10.1.0.0/16
    customers: [acmep01, acmep02]
```

Quote values YAML would otherwise read as another type: `seq: "001"` stays the text `001`, while
`seq: 001` is the number `1`.

## Using a parameter set

A deployment names sets per **role** in `meta.params`, and any document of its `extends` chain
reads them with `${param:<role>.<key>}`. The role is chosen where the set is used, so a base reads
`${param:spoke.name}` whichever spoke a leaf names:

```yaml
# the base: shared by every spoke
kind: deployment
meta:
  name: spoke_deployment_base
  params: { hub: hub-defaults }          # optional: defaults, like a chart's values.yaml
spec:
  partial: true
  workspace: spoke
  environments: [prd]
  properties:
    subscription_name: sub-${param:spoke.name}
    provider_region: ${param:hub.region}
    customers: ${param:spoke.customers}  # the whole value: the list is inserted as a list
---
# a leaf: one per spoke
kind: deployment
meta:
  name: spoke-acme-001
  params: { spoke: spoke-acme-001 }      # or a list per role, merged in order
spec:
  extends: spoke_deployment_base
```

- **Merging.** Every document of the chain may name sets. Per role they merge root first, then
  down the chain, then the leaf's, later winning: maps merge per key, lists and scalars are
  replaced. A base's sets are defaults; the leaf's override them.
- **Whole value or embedded.** A token that is the entire value is replaced by the value with its
  type (a list, a map, a number). A token inside a longer string needs a single value; `true` and
  `false` are written as YAML writes them.
- **`${param:spoke}`** alone is the whole map of the role. Nested keys and named list items use
  dots, as `${value:}` does: `${param:spoke.network.cidr}`.
- **Once, at load.** Values are substituted when the chain is resolved, before anything runs.
  Run-time tokens (`${var:x}`, `${secret:x}`) are left for run time, and `$${param:x}` stays the
  literal text `${param:x}`.
- A `partial: true` base may hold tokens no value fills yet; only a deployable deployment must
  resolve every token of its chain.

## Validation

- A `${...}` token in a key or value of `spec.values` is a load error (`value_error`)
- Two parameter sets with the same name are a `duplicate_document` error, as for every kind
- A name in `meta.params` that is no parameter set is an `unknown_reference`
- A `${param:}` whose key the chain's sets do not hold, or whose role no document of the chain
  names, is `param_missing`. It is reported on the document that holds the token, for each
  deployment it was resolved for:
  `base spec.properties.envs[1]: ${param:spoke.hub} not found in spoke: [spoke-acme-001] (for spoke-acme-001)`
- A list or map inside a longer string is `param_not_scalar`
- A `${param:}` in a document of another kind (an environment, a workspace, a module, ...) is not
  substituted, and the token check reports it as a `malformed_value_token`

## Notes

- Where parameter sets live is up to the solution: strata finds documents by `kind` and
  `meta.name`, never by folder. Keeping a spoke's set next to the documents that use it reads well.
- A parameter set is not an [environment](environment.md): an environment declares run-time
  variables, secrets and feature flags that `${var:}`/`${secret:}`/`${feature:}` resolve at build
  and deploy time. A parameter set's values are read when the solution is loaded, before any of
  that runs.
