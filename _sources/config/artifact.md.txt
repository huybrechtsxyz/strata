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
- `spec.image_tag` — what to deploy, in one of three shapes (see [Tag and digest](#tag-and-digest));
  optional, omit it when a [`version`](version.md) pin always supplies it. Overridden by `kind: version`'s
  `spec.pins.artifacts[name]` when set; with neither, the tag is blank
- `spec.registry` — free-text registry (host, and a feed or project path if it has one), documentation
  only — **not** a remote reference (strata never fetches an image; the runtime pulls it at deploy time).
  It is not added to the image reference. When the two name different registries, `strata validate`
  gives information (`artifact_registry_mismatch`, never a warning, so `--strict` passes):
  - where `image_name` is pulled as written (a Compose service), it must hold the full reference, registry
    included; an `image_name` with no host is pulled from Docker Hub;
  - where a consumer adds its own registry, a host-less path is right: a Terraform root that imports the
    image into a per-deployment registry, or a chart with its own `image.registry`
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

### Tag and digest

`spec.image_tag`, and a pin under `pins.artifacts`, take one of three shapes:

| Value                | Image reference              |
| -------------------- | ---------------------------- |
| `1.4.2`              | `name:1.4.2`                 |
| `sha256:<hex>`       | `name@sha256:<hex>`          |
| `1.4.2@sha256:<hex>` | `name:1.4.2@sha256:<hex>`    |

- A tag is letters, digits, `_`, `.` and `-`, at most 128 characters, not starting with `.` or `-`. A digest is
  an algorithm, `:` and at least 32 lowercase hex characters.
- Anything else fails: an artifact's `image_tag` when the document loads, a pin in `strata validate`
  (`artifact_tag_invalid`), and `strata version set NAME artifacts TARGET VALUE` before it writes the file.
- Pin both together. One string moves the tag and the digest at once, so they cannot drift apart.
- `strata deploy images` shows the digest in a `DIGEST` column (`data.images[].image_digest`); `image_tag` there is
  the tag part only. `image_tag_policy` judges the tag part, and `require_digest` fails an image with no digest
  ([workspace](workspace.md)).

### The `${artifact:NAME.FIELD}` token

Read an artifact directly, with no variable to declare:

| Token                             | Value                                                                                          |
| --------------------------------- | ---------------------------------------------------------------------------------------------- |
| `${artifact:NAME.image_name}`     | `spec.image_name`, never pinned                                                                |
| `${artifact:NAME.image_tag}`      | the whole value after the deployment's [`version`](version.md) pin, else `spec.image_tag`      |
| `${artifact:NAME.image_ref}`      | the image reference built from both, as in the table above, or just `image_name` with no value |
| `${artifact:NAME.image_tag_name}` | the tag part of `1.4.2@sha256:<hex>` (`1.4.2`)                                                 |
| `${artifact:NAME.image_digest}`   | the digest part (`sha256:<hex>`)                                                               |

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
  Use the chart's own keys, as above (the module's or a service's `configuration`); a token in a service's
  `environment` only sets an environment variable, so `strata validate` and `deploy images` both call the artifact
  not delivered. For a Compose module the service's `artifact` is enough.
- It resolves at deploy, in every field a `${var:}` token resolves in: a Terraform payload or backend, Helm values,
  a Compose file, a script's arguments. `--pin NAME` on `deploy run` picks the version, as for every other pin.
- `${artifact:NAME.image_tag}` on an artifact with no `image_tag` and no pin for the deployment is an error
  (`unavailable_artifact_token`), never an empty tag. So is `image_tag_name` or `image_digest` when the value
  has no such part: a chart that takes `image.digest` needs the pin to carry one. An unknown artifact or field
  is `unknown_artifact_token`.
  `strata validate` judges both per deployment, and `deploy run` stops on them before the step's tool runs.
- An environment store's own `value` cannot use it: a store value is resolved before a deployment is chosen.
- Write `$${artifact:...}` for a literal.

### A `store: artifact` variable

To hand the value to a Terraform root as a variable (`strata.variables` and `TF_VAR_<key>`), or to
override it per environment, declare a variable. `field` is required: `image_name`, `image_tag` (with
any [`version`](version.md) pin applied; `""` when there is no tag), `image_ref`, `image_tag_name` or
`image_digest` (each `""` when the value has no such part):

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

An artifact that no deployment uses in any of these ways is reported by `strata validate`
(`artifact_unused`):

- a **warning** (`--strict` fails on it) when no document names it at all: no `store: artifact` variable,
  no module service `artifact`, no `${artifact:}` token and no `pins.artifacts` entry. That is a leftover or a
  mistyped name, and `strata version update` does not offer a pin for it;
- **information** when a document names it but no deployment reaches that document yet: an image staged before
  its first deployment, for example a `store: artifact` variable in an environment no deployment lists yet.
  `strata version update` offers its pin as usual.

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
