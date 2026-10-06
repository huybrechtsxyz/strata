# ProviderConfig (`kind: providerconfig`)

The registry entry for one provider *type* (azure, aws, kamatera, gcp, ...): its valid regions
and supported resource types. `meta.name` is the type key itself (e.g. `azure`) — matched
against a real [`provider`](provider.md) document's `spec.properties.type`.

Promoted to its own kind (rather than a bare list on [`configuration`](configuration.md)) so a
platform with many provider types doesn't need one shared, ever-growing file — each type gets
its own file, own PR, own reviewer.

## Schema

- `spec.description` — description of the provider type
- `spec.version` — optional provisioner version constraint (e.g. `~>3.0`)
- `spec.regions[]` — supported regions, each `{name, geography, description, custom}`.
  `geography` is an optional compliance/deployment boundary tag grouping several regions
  (mirrors Azure's own "geography" concept) — referenced by [`tenant.md`](tenant.md)'s
  `spec.geographies`.
- `spec.additional_regions` — allow regions not listed above (default `false`)
- `spec.resources[]` — supported resource types, each `{name, category, subcategory, description,
  additional_configurations, configuration}` — `configuration` is a map of field name to a regex
  pattern (or a structured `{pattern, required, description}`) that a `Resource` document's own
  `spec.configuration` values must match
- `spec.additional_resources` — allow resource types not listed above (default `false`)

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: providerconfig
meta:
  name: azure
spec:
  description: "Microsoft Azure provider type registry"
  version: "~>3.0"
  additional_regions: false
  regions:
    - name: westeurope
      geography: europe
      description: "West Europe (Netherlands)"
    - name: northeurope
      geography: europe
      description: "North Europe (Ireland)"
  additional_resources: false
  resources:
    - name: storage_account
      category: storage
      subcategory: blob
      description: "Azure Storage Account"
      additional_configurations: false
      configuration:
        account_tier: "^(Standard|Premium)$"
    - name: virtual_machine
      category: compute
      subcategory: virtualmachine
      description: "Azure Virtual Machine"
      additional_configurations: true
```

## Notes

- There is no native boolean configuration-field type — use `pattern: "^(true|false)$"` and pass
  the literal string `"true"`/`"false"` as the value.
