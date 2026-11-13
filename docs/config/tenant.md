# Tenant (`kind: tenant`)

A customer/organization identity: who they are, which data-residency geographies they may deploy
into, and the defaults every deployment on their behalf inherits. Typically one per customer
directory (e.g. `customers/contoso.yaml`).

## Schema

- `meta.name` — the tenant code itself (e.g. `contoso`) — there is no separate `spec.code`
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
- `spec.extends` — a base tenant whose spec is merged into this one (see "Bases" below)
- `spec.partial` — `true` for a base meant only to be extended; it may leave out `display_name`
- `meta.params` — [parameter sets](parameterset.md) per role, read with `${param:<role>.<key>}`
- `meta.name_rule` — the name (tenant code) every tenant built on this one must have, with
  `${param:}` tokens filled in

`display_name` is required, unless the tenant is `partial: true` or gets it through `extends`.

## Bases: `extends` and `partial`

Tenants that share their defaults can share one base (a worked example:
[How To: tenant and environment bases](../how-to/environment-and-tenant-bases.md)). The child is
merged over the base when the solution loads, before deployments merge the tenant's defaults in:

| Field                                   | Merge with `extends`                                                            |
| --------------------------------------- | ------------------------------------------------------------------------------- |
| `display_name`, `onboarded`             | child wins                                                                      |
| `geographies`                           | replaced by the child's list when it sets one                                   |
| `environments`                          | base's list, then the child's, as on a deployment; still unique after the merge |
| `properties`, `custom`, `configuration` | deep merge per leaf key, child wins                                             |
| `extends`, `partial`                    | consumed, not part of the result                                                |

`geographies` is replaced, never joined: it is a data-residency limit, and a child that writes
`[us]` must not also allow its base's `europe`.

```yaml
kind: tenant
meta: { name: customer_tenant_base }
spec:
  partial: true
  display_name: ${param:tenant.display_name}
  geographies: ${param:tenant.geographies}   # the whole value: inserts the list
  properties:
    deploy_profile: { tenancy: isolated, audience: customer }
  configuration:
    dns_slug: ${param:tenant.dns_slug}
    sso: { enabled: false }
---
kind: parameterset
meta: { name: tenant-initech }
spec:
  values: { display_name: Initech, dns_slug: initech, geographies: [us] }
---
kind: tenant
meta:
  name: initech
  params: { tenant: tenant-initech }
spec:
  extends: customer_tenant_base
  configuration:
    sso: { enabled: true }
```

- Every reader (tenant defaults on deployments, geography checks, `${value:}`, attributeconfig)
  sees the merged tenant; findings about it name the child's file.
- A `partial: true` tenant is a base only. A deployment whose `tenant` names it is an error
  (`tenant_not_selectable`), and so is a `${value:tenant.<base>...}` token
  (`value_reference_to_base`). `meta.name` is the tenant code, so give a base a name that cannot
  be mistaken for one (`customer_tenant_base`).
- A spec that holds `${param:}` is validated once per tenant built on it, after the values are
  filled in. A cycle is `circular_extends`; a base that does not exist is an `unknown_reference`
  on `spec.extends`.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: contoso
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
