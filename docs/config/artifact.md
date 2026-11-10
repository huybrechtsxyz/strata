# Artifact (`kind: artifact`)

A pinnable, named reference to something external strata doesn't fetch or deploy itself — a
container image today, possibly other identifiers later. Referenced by identity from anywhere in
the solution: an `${artifact:NAME.FIELD}` token in any field strata resolves at deploy, a
`store: artifact` variable (to hand a Terraform root a variable), or a module service's `artifact`
field (see [`module`](module.md)).

## Schema

- `spec.image_name` — the container image path (e.g. `example/scheduler-api`) —
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
  name: scheduler-api
spec:
  image_name: example/scheduler-api
  image_tag: "1.4.2"
  registry: cdn-feeds.acme.com
```

### The `${artifact:NAME.FIELD}` token

Read an artifact directly, with no variable to declare:

| Token                         | Value                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------- |
| `${artifact:NAME.image_name}` | `spec.image_name`, never pinned                                                                         |
| `${artifact:NAME.image_tag}`  | the tag after the deployment's [`version`](version.md) pin, else `spec.image_tag`                       |
| `${artifact:NAME.image_ref}`  | `image_name:image_tag`, `image_name@sha256:...` for a digest, or just `image_name` when there is no tag |

```yaml
# A Helm module: the chart's own keys, the pinned values
spec:
  configuration:
    image:
      repository: "${artifact:scheduler-api.image_name}"
      tag: "${artifact:scheduler-api.image_tag}"
```

- It resolves at deploy, in every field a `${var:}` token resolves in: a Terraform payload or backend, Helm values,
  a Compose file, a script's arguments. `--pin NAME` on `deploy run` picks the version, as for every other pin.
- `${artifact:NAME.image_tag}` on an artifact with no `image_tag` and no pin for the deployment is an error
  (`unavailable_artifact_token`), never an empty tag. An unknown artifact or field is `unknown_artifact_token`.
  `strata validate` judges both per deployment, and `deploy run` stops on them before the step's tool runs.
- An environment store's own `value` cannot use it: a store value is resolved before a deployment is chosen.
- Write `$${artifact:...}` for a literal.

### A `store: artifact` variable

To hand the value to a Terraform root as a variable (`strata.variables` and `TF_VAR_<key>`), or to
override it per environment, declare a variable. `field` is required: `image_name`, `image_tag` (with
any [`version`](version.md) pin applied; `""` when there is no tag) or `image_ref`:

```yaml
variables:
  - key: SCHEDULER_API_IMAGE_TAG
    store: artifact
    value: scheduler-api
    field: image_tag
```

The pinned tag is also what a build writes to `strata.variables`. `${value:artifact.<name>.spec.image_tag}` is
rejected, as it would read the unpinned tag; use the token.

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
