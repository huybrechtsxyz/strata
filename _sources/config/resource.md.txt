# Resource (`kind: resource`)

A discrete, provisionable cloud artifact — a VM, storage account, database, etc. Declares its
provider type/resource type and defaults; a [`workspace`](workspace.md) instantiates it (possibly
several times, under different local names) via `WorkspaceResourceModel`.

## Schema

- `spec.properties.provider_type` — must match a [providerconfig.md](providerconfig.md) document
- `spec.properties.resource_type` — must match one of that provider type's `spec.resources[].name`
- `spec.properties.category` / `subcategory` — resource classification
- `spec.properties.unit_cost` — optional cost-tracking figure (default `0.0`)
- `spec.dependencies[]` — capability-based dependencies (`category`/`subcategory`/`resource_type`,
  `optional`) — declares what *kind* of resource is needed, resolved by the workspace, not a
  hardcoded instance reference
- `spec.storage` — optional VM-style storage config: `install_path`, `disks[]` (`{name, size,
  label, mount}`), `volumes[]` (`{name, path}` — must sit under one of `disks[]`'s mount points),
  `parameters` (free-form)
- `spec.configuration` — passthrough, validated against the matching `ProviderConfig` resource
  entry's field patterns
- `spec.default_tags` / `spec.custom_tags` — cloud tags (both optional)
- `spec.custom` — free-form data, not consumed by any provisioner
- `spec.lifecycle` — optional IaC workflow hook phases

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: storage-account
spec:
  properties:
    provider_type: azure
    resource_type: storage_account
    category: storage
  configuration:
    account_tier: Standard
  default_tags:
    environment: example
    cost-center: "cc-1234"
---
apiVersion: strata.huybrechts.xyz/v2
kind: resource
meta:
  name: linux-vm
spec:
  properties:
    provider_type: azure
    resource_type: virtual_machine
    category: compute
  default_tags:
    environment: example
    cost-center: "cc-1234"
  custom_tags:
    owner: platform-team
```

## Notes

- A resource declares *capability* dependencies (e.g. "I need a `networking`/`virtual_network`"),
  not a specific instance name — the workspace resolves which concrete resource satisfies it,
  making resources reusable across workspaces and providers.
