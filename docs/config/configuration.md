# Configuration (`kind: configuration`)

Platform-wide policy: which provider types and topology types this solution allows. A
deliberately small slice of v1's `configuration_model.py` — security policy, path conventions,
logging, cost/drift tracking are not ported (only added when a v2 feature actually needs them).

## Schema

- `spec.providers[]` — names of `ProviderConfig` documents (the provider type registry)
- `spec.topologies[]` — names of `TopologyConfig` documents (the topology type registry)
- `spec.additional_topologies` — allow topology types not listed in `spec.topologies` (default `false`)
- `spec.properties` / `spec.configuration` / `spec.custom` — free-form passthrough

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: example-config
spec:
  providers:
    - azure
  additional_topologies: false
  topologies:
    - kubernetes
```

## Notes

- `providers`/`topologies` are plain document names (ADR-0015), resolved by discovery — not
  embedded registry entries. Each provider/topology type gets its own `ProviderConfig`/
  `TopologyConfig` document (see [providerconfig.md](providerconfig.md)/[topologyconfig.md](topologyconfig.md)),
  so adding a new type is a new file with its own PR, not an edit to a shared list.
- `spec.remotes` is **not** here — remotes live on the solution manifest (`strata.yaml`, see
  [solution.md](solution.md)), since Configuration itself can live in a remote and remotes must
  resolve first.
