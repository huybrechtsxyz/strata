# Tenant (`kind: tenant`)

A customer/organization identity: who they are, which data-residency geographies they may deploy
into, and the defaults every deployment on their behalf inherits. Typically one per customer
directory (e.g. `customers/c0062.yaml`).

## Schema

- `meta.name` — the tenant code itself (e.g. `c0062`) — there is no separate `spec.code`
- `spec.display_name` — human-readable name (e.g. "Example Corporation")
- `spec.geographies[]` — data residency boundaries this tenant may deploy into; each must appear
  as a `geography` on some region in a [providerconfig.md](providerconfig.md) document
- `spec.onboarded` — informational ISO date
- `spec.environments[]` — names of [`environment`](environment.md) documents merged in *before* a
  deployment's own environments (tenant-wide defaults; deployment values still win)
- `spec.properties` — merged as a base layer into every deployment referencing this tenant
  (deployment's own properties win)
- `spec.configuration` — tenant-specific settings emitted verbatim to the provisioner, never merged
- `spec.custom` — merged as a base layer into every deployment's own `spec.custom`

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0062
  annotations:
    description: "Example customer tenant"
spec:
  display_name: "Example Corporation"
  geographies:
    - europe
  onboarded: 2026-03-15
  properties:
    deploy_tier: standard
  configuration:
    crm_id: "42"
```

## Notes

- `spec.geographies` is v2's name for what v1 called "zones" — the same concept as
  [providerconfig.md](providerconfig.md)'s region `geography` tag, so it's checked against a
  registry that already exists rather than a separately maintained list.
- No cloud tags — a tenant is an identity/grouping concept, not a provisioned resource.
