# Topology (`kind: topology`)

A named grouping of resources and namespaces — "what conceptually belongs together" (e.g. "the
AKS cluster + its blob store + its key vault"), for documentation, diagramming, and workload
placement. Deliberately carries no provider/provisioner binding — which tool builds/deploys these
resources is a separate concern (see [workspace.md](workspace.md)'s `execution`/provisioners).

## Schema

- `spec.type` — the topology type (must match a [topologyconfig.md](topologyconfig.md) document's `meta.name`)
- `spec.components[]` — each `{resource, modules}`: a resource name reference, optionally with
  application code (`modules[]`, same shape as [namespace.md](namespace.md)'s module references)
  attached directly to that resource (e.g. an Azure Function App's code onto its Function App resource)
- `spec.namespaces[]` — each `{namespace}`: a namespace reference deployed on this topology
- `spec.volumes[]` — topology-level storage volumes

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: main-topology
spec:
  type: kubernetes
  components:
    - resource: web-storage
    - resource: web-vm
```

## Notes

- When multiple modules are attached to one component and more than one is enabled, exactly one
  must be marked as the `main` slot.
- A single provisioner can span several topologies, and a single topology can be built by several
  different provisioners acting on different subsets — grouping and tooling are independent axes
  (ADR-0011).
