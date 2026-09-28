# Artifact (`kind: artifact`)

A pinnable, named reference to something external strata doesn't fetch or deploy itself — a
container image today, possibly other identifiers later. Referenced by identity from anywhere in
the solution: a `store: artifact` variable (an external provisioner's own input) or a module
service's image (once that binding is wired up).

## Schema

- `spec.image_name` — the container image path (e.g. `int-docker-test/src/omp.dispatcher.api`) —
  identity, never overridden by a [`version`](version.md) pin
- `spec.image_tag` — the tag/digest to deploy; may be blank until a real image exists. Overridden
  by `kind: version`'s `spec.pins.artifacts[name]` when set
- `spec.registry` — free-text registry host, documentation only — **not** a remote reference
  (strata never fetches an image; the runtime pulls it at deploy time)
- `spec.integration` — optional [`integration`](integration.md) (capability: `sources`) providing
  registry credentials
- `spec.properties` / `spec.configuration` / `spec.custom` — free-form/passthrough data, never merged

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dispatcher-api
spec:
  image_name: int-docker-test/src/omp.dispatcher.api
  image_tag: "1.4.2"
  registry: cdn-feeds.omp.com
```

Referenced from an environment's `variables`:

```yaml
variables:
  - key: DISPATCHER_API_IMAGE_TAG
    store: artifact
    value: dispatcher-api
```

## Notes

- Deliberately not built on [`SourceModel`](solution.md) despite the surface similarity to its
  chart mode — a remote's `fetch` describes *who materializes bytes on disk*, and strata never
  fetches a container image (the registry pulls it at deploy time, not strata at build time).
- No chart mode: a chart pin already targets a real [`module`](module.md) document's own
  `spec.source.chart_version` — fully served by the existing mechanism, not a gap this kind covers.
