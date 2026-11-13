# Environment (`kind: environment`)

Real declared variables, secrets, and feature flags — what `${var:KEY}`/`${secret:KEY}`/
`${feature:KEY}` tokens (used throughout [network](network.md)/[firewall](firewall.md)/
[dns](dns.md)/[module](module.md) documents) resolve against.

A deliberately small slice of v1's environment model: a census of 26 real environment documents
across two production repos found `overrides`/`lifecycle`/`promotion` at zero real usage, so none
of that machinery is ported.

## Schema

- `spec.variables[]` / `spec.secrets[]` / `spec.features[]` — each entry: `{key, store, value,
  version, description}`
  - Variable `store` values: `constant`, `environment`, `artifact`, `azure-appconfig`, `flagsmith`
  - Secret `store` values: `constant`, `environment`, `github`, `azure-keyvault`, `bitwarden`,
    `vault`, `infisical`
  - Feature `store` values: `constant`, `environment`, `azure-appconfig`, `flagsmith`
  - Only `constant`/`environment`/`github`/`artifact` resolve to anything today (see Notes) — the
    rest validate as recognized names, naming a capability-backed [`integration`](integration.md),
    but have no runtime resolver wired up yet
  - `value` — the store's own addressing scheme: a literal (`constant`), an environment variable
    name (`environment`/`github`), or a secret path/ID/flag key for an integration-backed store.
    A `value` cannot contain a `${var:}`/`${secret:}`/`${feature:}`/`${output:}`/`${deployment:}`/`${artifact:}` token — a key
    cannot be defined through another key, and `strata validate` rejects it
    (`store_value_token_unsupported`). `${value:kind.name.path}` (a field of another document)
    is allowed in a `constant`'s value, except a field a [`version`](version.md) pin overrides
    (an artifact's `image_name` or `image_tag`, a module service's `image`, a module's
    `source.chart_version`, an asset's `location.reference`): `strata validate` rejects it
    (`value_reference_pinnable_field`), since it would read the unpinned value. Use
    [`${artifact:NAME.FIELD}`](artifact.md) or a `store: artifact` variable for an image.
  - Secrets additionally support `generate: {type, length}` (auto-generate when the key is missing)
    and `rotate` (age-based or automatic rotation policy) — integration-backed stores only
- `spec.properties` — merged as a layer into every deployment using this environment (tenant
  properties merge first, then this, then the deployment's own)
- `spec.custom` — free-form data for scripts/extensions
- `spec.extends` — a base environment whose spec is merged into this one (see "Bases" below)
- `spec.partial` — `true` for a base meant only to be extended
- `meta.params` — [parameter sets](parameterset.md) per role, read with `${param:<role>.<key>}`
- `meta.name_rule` — the name every environment built on this one must have, with `${param:}`
  tokens filled in

## Bases: `extends` and `partial`

Environments that differ in a few values can share one base (a worked example:
[How To: tenant and environment bases](../how-to/environment-and-tenant-bases.md)). The child is
merged over the base when the solution loads:

| Field                              | Merge with `extends`                                                         |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `properties`, `custom`             | deep merge per leaf key, child wins                                          |
| `variables`, `secrets`, `features` | by `key`: a child entry **replaces the whole entry**; new keys are appended  |
| `extends`, `partial`               | consumed, not part of the result                                             |

A store entry is replaced, never merged field by field: a variable moved from `store: constant`
to an integration-backed store keeps none of the base's fields. A child can override an inherited
entry, not remove it. This is the same rule a deployment's list of environments follows at run
time, applied when the solution loads.

```yaml
kind: environment
meta: { name: spoke_flags_base }
spec:
  partial: true
  features:
    - { key: enable_nsp, store: constant, value: true }
    - { key: enable_apim, store: constant, value: false }
---
kind: environment
meta: { name: spoke-initech-001-env }
spec:
  extends: spoke_flags_base
  features:
    - { key: enable_apim, store: constant, value: true }   # replaces the base's entry
```

- Every reader (value assembly, `strata values`, token checks, `${value:}`, attributeconfig) sees
  the merged environment; findings about it name the child's file.
- A `partial: true` environment is a base only. A deployment or tenant that lists it in
  `environments` is an error (`environment_not_listable`), and so is a
  `${value:environment.<base>...}` token (`value_reference_to_base`). An environment without
  `partial` may be extended and listed.
- A base may read [parameter sets](parameterset.md): `deploy_code: ${param:customer.code}` in the
  base, `meta.params: { customer: customer-acmep01 }` in the child. A spec that holds `${param:}`
  is validated once per environment built on it, after the values are filled in. A secret's
  `value` never reads a parameter set: that is a load error, since a parameter set is plain text
  in git.
- A cycle (`a` extends `b` extends `a`) is `circular_extends`; a base that does not exist is an
  `unknown_reference` on `spec.extends`.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
  annotations:
    description: "Example production environment"
spec:
  properties:
    deploy_tier: production
  variables:
    - key: PUBLIC_IP
      store: constant
      value: "203.0.113.10"
    - key: REGION
      store: constant
      value: westeurope
  secrets:
    - key: DB_PASSWORD
      store: environment
      value: EXAMPLE_DB_PASSWORD
      description: "Read from the EXAMPLE_DB_PASSWORD environment variable"
```

## Notes

- Keys are unique per store, not globally — `${var:X}` and `${secret:X}` are different tokens, so
  the same key name in both a variable and a secret is legitimate.
- Only `constant`/`environment`/`github`/`artifact` stores resolve to anything today — any other
  `store` value validates as a recognized name but has no runtime resolver behind it yet, same
  discipline as [`provisioner`](workspace.md)'s open `tool` field.
- Inspect what a deployment actually resolves with `strata values get DEPLOYMENT KEY...` before
  building or deploying — see [docs/README.md](../README.md#quick-start).
