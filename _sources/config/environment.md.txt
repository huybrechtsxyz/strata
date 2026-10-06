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
    name (`environment`/`github`), or a secret path/ID/flag key for an integration-backed store
  - Secrets additionally support `generate: {type, length}` (auto-generate when the key is missing)
    and `rotate` (age-based or automatic rotation policy) — integration-backed stores only
- `spec.properties` — merged as a layer into every deployment using this environment (tenant
  properties merge first, then this, then the deployment's own)
- `spec.custom` — free-form data for scripts/extensions

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
