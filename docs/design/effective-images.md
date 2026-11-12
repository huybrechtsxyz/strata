# Effective images of a deployment - Design

- Status: current
- Last updated: 2026-11-12

## Overview

`effective_images()` (`controllers/effective_images.py`) answers one question for a deployment: which container
images will it run, with the tag after any `kind: version` pin, where that tag came from, what uses each image and
whether strata writes it into what runs. It reads the solution only: no build, no store, no network, no secret.

`strata deploy images DEPLOYMENT` prints that answer, so a pipeline can, for example, copy each pinned image from a
build feed into the target's registry before `deploy run`. `build run` and the SBOM, the audit manifest,
`image_tag_policy`, `deploy report` and the `artifact_unused` check all read the same rows, so none of them can
disagree about what a deployment runs.

## Current Design

### Rows

`effective_images(context, deployment, *, pinned_by_flag=False) -> list[EffectiveImage]`. `deployment` is the
resolved model, already carrying a `--pin` in `spec.version`; `pinned_by_flag` says whether it did. `EffectiveImage`
lives in `integrations/resolved_context.py` so the SBOM collectors, which sit below the controllers, can read it.

| Field                                       | Meaning                                                                                              |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `origin`                                    | `artifact` (a `kind: artifact`), `module-service` (a module service's literal `image`), or `chart` (a rendered chart's image, see below) |
| `name`, `artifact`, `module`                | the artifact or service name, and the document it came from                                          |
| `via`                                       | every use: `variable:KEY`, `service:MODULE/SERVICE`, `token:KIND/NAME`, `chart:NS/REFERENCE/CONTAINER` |
| `image_name`, `image_tag`, `image_digest`   | the name, the tag part and the digest part of `TAG`, `sha256:...` or `TAG@sha256:...`; either part `None` when absent |
| `image_ref`                                 | `compose_image_ref()` of name and tag: `name:tag`, `name@sha256:...`, `name:tag@sha256:...`, or the bare name |
| `tag_source`                                | `pin`, `declared`, `none`, or `chart` for a rendered chart row                                       |
| `pin_version`, `pin_flag`                   | the version document whose pin chose the tag, and whether it came from `--pin`                       |
| `registry`, `configuration`, `integration`  | an artifact's own fields; `integration` by name only, never resolved, so no credential reaches a row |
| `delivered`, `delivery`                     | whether strata writes the image into what runs, and why not when it does not                         |

Rows are artifacts first, then module services, each in the order first used.

### What counts as a use

Only what the deployment reaches is listed; an artifact nothing uses is not deployed and has no row.

- **An enabled module service's `artifact`** or **literal `image`**, walking the enabled module references of the
  workspace's namespaces, with `images` and `charts` pins applied.
- **An `${artifact:NAME.FIELD}` token** in any document the deployment reaches
  (`reachable_documents.documents_reached_by_deployment(..., enabled_only=True)`: the workspace's documents plus the
  deployment, its tenant and its environments; a module reached only through a disabled reference is left out). The
  use is `token:KIND/NAME`, once per document. An unknown artifact is skipped; `strata validate` reports it.
- **A `store: artifact` variable the deployment can receive**: a `${var:KEY}` token in a reached document reads it,
  or an `execution` step receives every variable without a token (`_received_variable_keys()`):
  - a tool that takes the strata payload (`ENV_VAR_PREFIX`: Terraform), through `strata.variables` and
    `TF_VAR_<key>`. The root module is not read, so a variable it does not declare still counts;
  - a script without `script.values` (`STRATA_VALUES`); with it, only the keys it lists;
  - an `output.template`: the `variables` keys it reads, or every variable when it reads them dynamically or cannot
    be read;
  - a tool strata does not know: every variable.

  Only `execution` steps count: `deploy run` runs nothing else. A declared variable nothing receives does not list
  its artifact. Where strata cannot tell, it counts: over-reporting an image is the safe side.

### Delivery

`delivered` separates "the deployment declares this image" from "strata writes this image into what runs":

- A Compose service's image (literal or from its `artifact`) is delivered: `compose.py` writes it into the stack.
- A `store: artifact` variable a step receives, and an `${artifact:}` token in a document a provisioner consumes,
  deliver their artifact.
- A Helm module's service image is a declaration only: the chart decides what runs (`helm module: the chart
  decides`). In a Helm module an `${artifact:}` token delivers only when it is in the chart's own keys, the
  module's `spec.configuration` or a service's `configuration` (`artifact_tokens.helm_delivered_artifacts()`, the
  same rule as the `artifact_not_delivered` warning). A token elsewhere, such as a service's `environment`, is a use
  but not delivery.
- Any other module type writes no image.

An artifact is delivered when any of its uses delivers it.

`unknown_image_modules()` names the enabled Helm modules that declare no image and no artifact at all, so every
image their chart runs is known only to Helm. When a build's `sbom.json` holds the images its rendered charts run,
`rendered_chart_images()` turns them into `chart` rows and `with_rendered_images()` merges them in: a declared row the
chart is seen to run becomes delivered, the other rendered rows are added after the declared ones, and a Helm module
with a rendered row is no longer unknown. `image_tag_policy` does this merge when it has a build; `deploy images`
reads the solution only. How a Helm module's images reach the chart is in
[helm-image-delivery.md](helm-image-delivery.md).

### Tags, provenance and logging

The pin lookup for artifacts is `artifact_tokens.effective_artifact_tag()`, which `value_controller.resolve_artifact_tag()`
wraps: it returns `ArtifactTag(value, source, version)`, or `None` for an artifact that does not exist. `store:
artifact`, the `${artifact:}` token and `effective_images()` all go through it, so pin precedence lives in one place.
A blank tag is `source="none"`: a `store: artifact` variable resolves it to `""`, and the token reports it as
`unavailable_artifact_token`.

`effective_images()` re-derives pins that `build run` and `deploy run` already apply and log (ADR-0019 decision 7),
so it logs none: it calls `resolve_artifact_tag(..., log=False)` and `apply_version_pins(..., log=False)`. One build
or deploy logs each pin once, however many readers ask for the rows, and `strata validate` logs none.

### Readers

- `build run` computes the rows once and carries them on `ResolvedWorkspaceGraph.images`. The SBOM image collector
  adds the `artifact` rows to the module service images, with `strata:used-by`, `strata:pinned-by` and
  `strata:image-delivered`. An image is `strata:tag-stability=floating` when it has no tag or a known mutable alias
  (`latest`, `main`, ...) and no digest; a tag a version pin chose is not floating just because it is a build number.
  The SBOM's module images and chart versions are the pinned ones.
- The audit manifest's `artifacts.images` and `deploy report`'s `images[]`.
- `image_tag_policy`, at `build run`, `deploy run`, `strata policy check` and `deploy images`
  ([image-tag-policy.md](image-tag-policy.md)).
- `artifact_usage.check_unused_artifacts()`: `artifact_unused` for an artifact no deployment's rows list.

### `strata deploy images`

`strata deploy images DEPLOYMENT [--pin VERSION] [--path PATH] [-o json]`

- Never resolves a store value: no store I/O, no secrets. It calls `require_valid()`, because a dangling environment
  or module reference would silently drop rows.
- Console: a table of name, origin, image, tag, source (`pin prd`, `--pin prd`, `declared`, `none`) and used by;
  DIGEST, DELIVERED and INTEGRATION columns appear only when a row needs one. Helm modules with no declared image are
  listed under "Known only to Helm".
- JSON: `{deployment, version, pin, images: [...], unknown_image_modules: [...]}`, each row as `asdict()`.
- Without `image_tag_policy`, a row with neither tag nor digest is a warning (`image_without_tag`) and an unknown
  Helm module an info finding (`image_unknown_to_strata`); nothing fails. With the policy, the policy judges every
  row, and a `deny` breach exits 3.
- Exit codes: 0 listed; 2 bad arguments, an unknown deployment or `--pin`, or not inside a solution; 3 an invalid
  solution or a denied image; 1 system failure.

The command lives under `deploy`, where the commands about one deployment live, and is named `images`, not
`artifacts`: the consumer needs images, and "artifacts" already means the build output and the audit manifest's bill
of materials.

### Out of scope

- A version document shared across workspaces (`spec.workspace` as a list) needs its own ADR first.
- `spec.mirror` on an artifact, and a capability check on `spec.integration`: not supported until strata consumes
  either field.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - version pins, their precedence, and logging each application
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `kind: artifact` and `store: artifact`
- [ADR-0022](../decisions/0022-strata-build-run.md) - what `build run` renders; build keeps tokens literal, deploy
  resolves
- [ADR-0058](../decisions/0058-artifact-token-for-pinned-image-fields.md) - the `${artifact:NAME.FIELD}` token

## History

- Three places used to answer "which images does this deployment use", each incompletely: the SBOM read unpinned
  images and skipped artifacts, so the image gate gated on the wrong tags; the audit manifest applied pins but skipped
  artifacts; the render path had everything but threw the pin source away. One resolver replaced all three.
- Placement: a new `strata artifacts` group (one subcommand, and the word already taken), `values list --type
  artifacts` (no `--pin`, sees only `store: artifact`, resolves other stores live), the `version` group (it manages
  version documents) and a file in the build output (needs a full build with source clones) were all rejected for
  `deploy images`, which needs no build and no store.
- Literal module service images are listed with artifacts, filtered on `origin`, because the SBOM and the audit
  manifest need both.
- A blank artifact tag used to resolve to `None`, the same as an unknown artifact, so `deploy run` reported a
  tag-less artifact as "not a known artifact". The tag lookup returns `None` only for a missing artifact and a blank
  tag as `source="none"`.
- A digest tag produced the invalid `name:sha256:...`; `compose_image_ref()` joins a digest with `@`, and `TAG@digest`
  as `name:tag@digest`.
- A declared `store: artifact` variable nothing reads used to list its artifact, so `image_tag_policy` could fail a
  deployment on an image it does not run. It counts only where a step or a token receives it, with the same rules as
  secrets (`step_secret_keys()`).
- `${artifact:}` tokens are counted as uses; without that, an image delivered only through the token escaped the
  policy, the SBOM and the gates.
- An `${artifact:}` token in a Helm service's `environment` once marked the artifact delivered, while
  `artifact_not_delivered` warned on the same module: adding an environment variable changed the answer without
  changing what runs. Both now use `helm_delivered_artifacts()`.
- Guard rails (fail on no tag, reject floating tags) were kept out of the resolver and built as the opt-in workspace
  policy `image_tag_policy`; without it a tag-less image is only a warning in `deploy images`.
- Re-deriving rows logged each pin once per reader; the resolver passes `log=False`, so only the build and deploy that
  apply a pin log it.
