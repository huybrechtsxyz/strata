# Parameter set (`kind: parameterset`)

One named set of plain values: the instance data of a spoke, a hub, a customer or a tenant. The
values a whole family of documents differs by (a name, a sequence number, a subscription ID) live
in one place, so the documents that use them can share one base.

> **Status: the kind only.** strata loads and validates parameter sets, but nothing reads them yet.
> Naming a set in a deployment's `meta.params` and reading it with `${param:<role>.<key>}` come in
> the next steps of `base-parameters-v2.md` (work doc).

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

## Validation

- A `${...}` token in a key or value of `spec.values` is a load error (`value_error`)
- Two parameter sets with the same name are a `duplicate_document` error, as for every kind

## Notes

- Where parameter sets live is up to the solution: strata finds documents by `kind` and
  `meta.name`, never by folder. Keeping a spoke's set next to the documents that use it reads well.
- A parameter set is not an [environment](environment.md): an environment declares run-time
  variables, secrets and feature flags that `${var:}`/`${secret:}`/`${feature:}` resolve at build
  and deploy time. A parameter set's values are read when the solution is loaded, before any of
  that runs.
