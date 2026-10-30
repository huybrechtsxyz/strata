# Artifact (`kind: artifact`)

A pinnable, named reference to something external strata doesn't fetch or deploy itself — a
container image today, possibly other identifiers later. Referenced by identity from anywhere in
the solution: a `store: artifact` variable (an external provisioner's own input) or a module
service's `artifact` field (see [`module`](module.md)).

## Schema

- `spec.image_name` — the container image path (e.g. `int-docker-test/src/acme.dispatcher.api`) —
  identity, never overridden by a [`version`](version.md) pin
- `spec.image_tag` — the tag/digest to deploy; optional, omit it when a [`version`](version.md) pin
  always supplies it. Overridden by `kind: version`'s `spec.pins.artifacts[name]` when set; with
  neither, the tag is blank
- `spec.registry` — free-text registry host, documentation only — **not** a remote reference
  (strata never fetches an image; the runtime pulls it at deploy time)
- `spec.integration` — reserved. Must name an existing [`integration`](integration.md), but
  nothing reads it: no registry credentials are resolved from it. Supply registry credentials to
  whatever pulls or copies the image (your pipeline, the runtime)
- `spec.properties` / `spec.configuration` / `spec.custom` — free-form/passthrough data, never
  merged and never read by strata. Reach a literal field from another document's string with
  `${value:artifact.<name>.spec.configuration.<key>}`

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dispatcher-api
spec:
  image_name: int-docker-test/src/acme.dispatcher.api
  image_tag: "1.4.2"
  registry: cdn-feeds.acme.com
```

Referenced from an environment's `variables`. `field` is required: `image_name`, `image_tag` (with
any [`version`](version.md) pin applied; `""` when there is no tag) or `image_ref` (`image_name:image_tag`,
`image_name@sha256:...` for a digest, or just `image_name` when the tag is blank):

```yaml
variables:
  - key: DISPATCHER_API_IMAGE_TAG
    store: artifact
    value: dispatcher-api
    field: image_tag
```

The pinned tag is also what a build writes to `strata.variables`. Read it with a `${var:KEY}` token
in `spec.properties` and it resolves at deploy. `${value:artifact.<name>.spec.image_tag}` is
rejected, as it would read the unpinned tag.

To see which images a deployment runs, with the tag after the pin and where it came from, run
`strata deploy images DEPLOYMENT [--pin NAME] -o json`. It reads the solution only (no build, no
store, no secret), so a pipeline can use it to copy the images to a registry before `deploy run`. The
same list feeds the SBOM and the audit manifest.

## Notes

- Deliberately not built on [`SourceModel`](solution.md) despite the surface similarity to its
  chart mode — a remote's `fetch` describes *who materializes bytes on disk*, and strata never
  fetches a container image (the registry pulls it at deploy time, not strata at build time).
- No chart mode: a chart pin already targets a real [`module`](module.md) document's own
  `spec.source.chart_version` — fully served by the existing mechanism, not a gap this kind covers.
