# Artifact (`kind: artifact`)

A pinnable, named reference to something external strata doesn't fetch or deploy itself — a
container image today, possibly other identifiers later. Referenced by identity from anywhere in
the solution: an `${artifact:NAME.FIELD}` token in any field strata resolves at deploy, a
`store: artifact` variable (to hand a Terraform root a variable), or a module service's `artifact`
field (see [`module`](module.md)).

## Schema

- `spec.image_name` — the full path the runtime pulls, registry host included (e.g.
  `ghcr.io/example/scheduler-api`; no host means Docker Hub) — identity, never overridden by a
  [`version`](version.md) pin
- `spec.image_tag` — the tag/digest to deploy; optional, omit it when a [`version`](version.md) pin
  always supplies it. Overridden by `kind: version`'s `spec.pins.artifacts[name]` when set; with
  neither, the tag is blank
- `spec.registry` — free-text registry host, documentation only — **not** a remote reference
  (strata never fetches an image; the runtime pulls it at deploy time). It is not added to the image
  reference: `image_name` holds the full path the runtime pulls, registry host included. `strata validate`
  warns when the two name different registries (`artifact_registry_mismatch`), counting an `image_name`
  with no host as Docker Hub
- `spec.integration` — names an existing [`integration`](integration.md) that holds the registry's
  credentials. `strata deploy images` reports it per image (`INTEGRATION`, `data.images[].integration`),
  so a pipeline that copies the image knows which credentials to use. Strata resolves nothing from it
  and never logs in: supply the credentials to whatever pulls or copies the image (your pipeline, the
  runtime)
- `spec.properties` / `spec.configuration` / `spec.custom` — free-form/passthrough data, never
  merged and never read by strata. Reach a literal field from another document's string with
  `${value:artifact.<name>.spec.configuration.<key>}`. `spec.image_name` and `spec.image_tag` cannot be
  read that way (`value_reference_pinnable_field`): use the `${artifact:NAME.FIELD}` token below

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: scheduler-api
spec:
  image_name: cdn-feeds.acme.com/example/scheduler-api
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

- For a Helm module this is how an artifact becomes the image that runs: strata writes no image into a chart's
  values, so a service's `artifact` alone is only declared (`strata validate` warns, `artifact_not_delivered`).
  Use the chart's own keys, as above; for a Compose module the service's `artifact` is enough.
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

The pinned tag is also what a build writes to `strata.variables`.

A declared variable only counts as using the artifact when the deployment can receive it: a `${var:KEY}`
token in a document it reaches, or an execution step that gets every variable (Terraform, which reads
`strata.variables` and `TF_VAR_<key>`; a script without `script.values`; an `output.template` that reads it).
A variable nothing receives does not make `deploy images` or `image_tag_policy` list the artifact.

Every way of reaching an artifact counts as using it: `strata deploy images` lists the artifact with each use in
`via` (`variable:KEY`, `service:MODULE/SERVICE`, or `token:KIND/NAME` for the document holding an
`${artifact:}` token), and so the SBOM, the image gates and `image_tag_policy` judge it.

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
