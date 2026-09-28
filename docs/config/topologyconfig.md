# TopologyConfig (`kind: topologyconfig`)

The registry entry for one topology *type* (kubernetes, dockerswarm, azure-native, ...): its
expected component roles. `meta.name` is the type key itself — matched against a real
[`topology`](topology.md) document's `spec.type`.

Promoted to its own kind (rather than a bare list on [`configuration`](configuration.md)) for the
same reason as [`providerconfig`](providerconfig.md) — each topology type gets its own file.

## Schema

- `spec.description` — description of the topology type
- `spec.components[]` — expected component roles, each:
  - `role` — matched against a [`workspace`](workspace.md) resource's `role` field
  - `uses_module` — whether a resource with this role must have a module attached
  - `required` — whether at least one resource with this role must be present
  - `min_count` / `max_count` — instance count bounds (`max_count: 0` means unlimited)
- `spec.additional_components` — allow component roles not listed above (default `false`)

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: topologyconfig
meta:
  name: kubernetes
spec:
  description: "Kubernetes topology type registry"
  additional_components: true
  components:
    - role: control-plane
      description: "Cluster control plane"
      required: true
      min_count: 1
      max_count: 1
    - role: worker
      description: "Worker node pool"
      required: false
      min_count: 0
      max_count: 0
```
