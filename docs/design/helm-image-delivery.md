# Helm image delivery - Design

- Status: implemented
- Last updated: 2026-11-12

## Overview

A module service can declare an `image` (or an `artifact`). For a Compose module strata writes that image into the
compose file, so the declared image is what runs. For a Helm module it does not: the values strata writes for a
chart hold `env`, `persistence` and the service and module `configuration`, and no image. The chart decides which
image runs. A Helm module's declared service image is a statement about the module, not an instruction to the
chart.

Strata therefore keeps two facts apart:

- **Delivery.** Every image row says whether strata writes the image into what runs (`EffectiveImage.delivered`),
  and why not when it does not (`EffectiveImage.delivery`). A Helm module delivers an artifact only through an
  `${artifact:}` token in the chart's own values ([ADR-0058](../decisions/0058-artifact-token-for-pinned-image-fields.md)).
- **What a chart runs.** The build renders each Helm chart offline (`helm template`) and lists the images it runs in
  `sbom.json`. Those rendered images are then a build fact: the CVE and image gates read them from the SBOM, and
  `image_tag_policy` reads them back as rows with the `chart` origin at build, deploy and `policy check`.

A Helm module whose images strata knows neither way (it declares none, and its chart was not rendered) is named,
and `image_tag_policy.on_unknown_image` decides what that costs.

The decision is [ADR-0059](../decisions/0059-judge-rendered-helm-images-declared-are-declarations.md).

## Current Design

### Why strata does not interpret `configuration`

A Helm image only reaches the chart through `configuration`, written in the chart's own key shape. Charts disagree
on where the image lives: `image.repository` and `image.tag`, `image: "repo:tag"`,
`controllers.main.containers.main.image.tag` (bjw-s common-library charts), per-component `server.image.tag`, or no
tag at all, falling back to the chart's `appVersion`. Any rule that reads `configuration` for an image would be a
heuristic per chart, so strata does not read it. It renders the chart instead, and reads the images from the
manifests.

### Levels of certainty

| What the image is                                              | Does strata know what runs?                                                  |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Compose service `image`                                        | Yes: strata writes it                                                        |
| Artifact through a `store: artifact` variable or a token       | Yes, except a token in a Helm module outside the chart's values (see below)  |
| Helm module `services[].image`, or `artifact` with no token    | No: declared only                                                            |
| An image the rendered chart runs                               | Yes: read from the render (origin `chart`)                                   |
| A Helm module that declares no image and was not rendered      | No: reported as unknown                                                      |

### Delivered: one rule, in the resolver

`EffectiveImage` (`integrations/resolved_context.py`) carries:

- `delivered: bool = True`
- `delivery: str | None = None`, the reason when `delivered` is false

Strata delivers an image when it writes it into something that runs:

- the Compose integration writes a service `image` (`compose.py`);
- a `store: artifact` variable reaches an execution step that receives it;
- an `${artifact:}` token is resolved into a document the deployment uses, with one exception for Helm modules,
  below.

No other module integration writes an image: not `helm.py`, the GitOps integrations (`argocd`, `flux`), `script`
or a custom plugin.

`effective_images()` in `controllers/effective_images.py` sets the fields:

| Row                                                                                                     | `delivered` | `delivery`                                                  |
| ------------------------------------------------------------------------------------------------------- | ----------- | ----------------------------------------------------------- |
| `module-service` on a `compose` module                                                                  | true        | -                                                           |
| `module-service` on a `helm` module                                                                     | false       | `helm module: the chart decides`                            |
| `module-service` on any other module type                                                               | false       | `<type> module: strata writes no image`                     |
| `artifact` with at least one delivering use (below)                                                     | true        | -                                                           |
| `artifact` whose only uses are a `service:` on a `helm` module, or a token elsewhere in a `helm` module | false       | `helm module: no ${artifact:} token in the chart's values`  |
| `artifact` whose only uses are a `service:` on another non-Compose module                               | false       | `<type> module: strata writes no image`                     |

A delivering use of an artifact is one of:

- `variable:KEY`, a `store: artifact` variable that is used or received;
- `service:MODULE/SERVICE` on a `compose` module;
- `token:KIND/NAME`, an `${artifact:}` token in a reached document. In a `type: helm` module only a token the
  chart's own values receive counts.

**The Helm token rule.** `helm_delivered_artifacts(module)` in `controllers/artifact_tokens.py` returns the
artifacts an `${artifact:}` token names in the module's `spec.configuration` or in any service's `configuration`:
the parts `helm.py`'s `_render_values()` writes into the chart's own keys. A token in a service's `environment`
becomes an environment variable under `<entry>.env`, which never changes the image a pod runs. Such a token is a
**use** (it shows as `token:module/<name>` in USED BY, and the artifact is not `artifact_unused`), but not
**delivery**.

Two consumers share this one function, so they cannot disagree:

- `effective_images()`, and through it `deploy images`, the SBOM marker, the audit manifest, `image_tag_policy` and
  the image gate;
- `strata validate`'s `artifact_not_delivered` warning (`semantic_checks._check_helm_artifact_delivery()`). A
  token in another service's `configuration` satisfies it, since that configuration lands in the same chart
  values.

An artifact is delivered when any of its uses delivers it. So an artifact a Helm module uses without a chart-key
token, but that a `store: artifact` variable also delivers to Terraform, is delivered in `effective_images()`,
which judges the deployment, while `artifact_not_delivered` still warns on the module, which it judges alone. Both
are right about what they judge.

The resolver already resolves each module, so `spec.type` is at hand. `_enabled_modules()` is the one walk
(workspace, its namespaces, enabled references, version pins applied) that `effective_images()` and
`unknown_image_modules()` share, so they cannot disagree on which modules count.

### Modules whose images strata does not know

`UnknownImageModule` (`integrations/resolved_context.py`) names one: `module`, `namespace`, `chart`
(`source.chart_name`) and `chart_version` (after a `charts` pin, so the version that deploys).

`unknown_image_modules(context, deployment)` lists every enabled `helm` module whose services declare neither an
`image` nor an `artifact`. That covers no `services`, and services with only `env`, `persistence` or
`configuration`. A module referenced in two namespaces is listed once per namespace.

- It is a separate list, not rows in `effective_images()`: the SBOM, the audit manifest, the gates and the report
  all expect every row to have an image.
- Helm only. An `argocd`, `flux`, `script` or custom module may run no containers at all, so listing it would be
  noise. Revisit if a consumer needs GitOps modules reported.
- A Helm module that declares some images while its chart runs more is not listed: only a render shows that, and
  the render's images are judged directly (below).

### `strata deploy images`

- The console gets a DELIVERED column, shown only when some row is not delivered: `yes`, or `no (<delivery>)`.
- Below the table, "Known only to Helm" lists the unknown modules with namespace and chart.
- JSON carries `delivered` and `delivery` on each image, and an `unknown_image_modules` list.
- Without an `image_tag_policy`, each unknown module is an info finding, `image_unknown_to_strata`. With one, the
  policy decides.
- `deploy images` stays offline and build-free: it judges declared images only and never reads rendered ones. A
  rendered chart's module still shows under "Known only to Helm" there. A `--build-path` option is not provided;
  revisit if a consumer needs it.

### `image_tag_policy.on_unknown_image`

```yaml
spec:
  image_tag_policy:
    enforcement: deny
    on_unknown_image: warn     # skip | warn | block (default warn)
```

- It decides only about unknown modules, apart from `enforcement`, as `image_gate_policy.on_unknown_image` does.
  `block` is an error (`build run` and `deploy run` stop, `deploy images` and `policy check` exit 3), `warn` a
  warning, `skip` an info finding (out of the summary, still in `-v` and JSON).
- The finding, `image_unknown_to_strata`:

  ```
  image_tag_policy: module 'immich' (helm chart immich 0.13.1) declares no images, so the images the chart
  deploys are known only to Helm and are not judged. Declare them under spec.services[].image to have them
  judged, or set on_unknown_image: skip.
  ```

- `rings` applies as to any row. Unknown modules are reported when `origins` covers `module-service` or `chart`,
  or is omitted (`_UNKNOWN_MODULE_ORIGINS` in `controllers/image_tag_policy.py`). Their images are module images,
  and also the images the chart would render, so a chart-only policy must not pass them silently.
- `allow` does not exempt an unknown module: its names are image row names, not module names. To clear the finding,
  declare the chart's images, or let the build render the chart.
- A declared-only image is still judged, with the same rules and codes, so what an author wrote on purpose is not
  dropped. The message says so: `...; declared only, the chart decides which image runs) has the floating tag
  'latest'.`

`evaluate_image_tag_policy(images, policy, ring, *, unknown=(), today=None)` stays pure.
`check_image_tag_policy()` fetches `unknown_image_modules()` when the caller has not, so its callers do not have
to.

### The SBOM: the declared-only marker

- `ImageCollector` adds `strata:image-delivered: false` and `strata:image-delivery: <reason>` to a container
  component when no `graph.images` row for its purl is delivered. Every use counts, so an image a Helm module
  declares and a Compose module runs is delivered, with no marker. The properties are written only when the image
  is not delivered; a missing property means delivered, so an older SBOM reads correctly.
- `_merge_by_purl()` in `controllers/sbom_controller.py` keeps one component per purl, joins every
  `strata:used-by` value instead of keeping the first, and drops both marker properties once a rendered chart
  runs the image (`_rendered_is_delivered()`): the image is then what runs.
- The image gate: `images_from_sbom()` reads the marker into `ImageRef.delivered` (default true), and the gate's
  label for such an image ends with `(declared only)`, on every finding and on the "asking about" step.
- The audit manifest: `ManifestImageModel.delivered` is `false` only for an undelivered image and left out
  otherwise, so existing manifests and their hashes are unchanged.
- The CVE gate is unchanged. Trivy and Grype scan the SBOM, and the marker is there for whoever reads their
  results.

### The SBOM: rendering charts

The `chart-image` collector (`integrations/sbom_collectors/chart_image_collector.py`), registered after `helm`,
renders charts and lists the images they run.

- **On by default.** `spec.sbom.render_charts` (`WorkspaceSbomModel`) defaults to `true`; a workspace without an
  `sbom` section renders too. `render_charts: false` keeps the declared images only. It is a workspace setting,
  not a `build run` flag, because the gates it feeds are workspace settings.
- **Which charts.** Every module folder in the build path whose `meta.yaml` has a release name and a namespace:
  - a **local chart**: no `chartName`, and a `Chart.yaml` in the folder, the folder `deploy run` installs from;
  - a **registry chart**: `chartName` set. The collector renders the `.tgz` in `.strata/helm/` that `cached_pin()`
    finds for the `meta.yaml` coordinates, and only when its digest is the `chartDigest` the build pinned, as
    `deploy run` checks before installing. `write_sbom(..., helm_cache=)` passes the cache to this collector only;
    `build run` sets it to `helm_cache_dir(context.root)`.
- **The render.** `HelmIntegration.template()`: `helm template <releaseName> <chart> --namespace <namespace> -f
  <folder>/strata-values.yaml`, offline (no `--dependency-update`, no repository access; a registry archive already
  holds its subcharts), 120 s timeout. The values file is the one `deploy run` installs with, so the render shows
  what deploys, apart from values whose tokens resolve only at deploy. The output stays in memory.
- **Collecting.** The pure `rendered_images(text)` reads each document (`yaml.safe_load_all`) and takes every
  `image` under a `containers`, `initContainers` or `ephemeralContainers` list, anywhere in the document. That
  covers Deployment, StatefulSet, DaemonSet, Job, CronJob, Pod and CRDs that embed a pod spec.
- **The component.** Type `container`, purl from `image_to_purl()`, hashes from `image_digest_hash()`, tag
  stability as for any image, named after the container, with:
  - `strata:found-in: rendered-chart`;
  - `strata:used-by: chart:<namespace>/<reference>/<container>`;
  - `strata:image`: the reference as the chart writes it (`IMAGE_REF_PROPERTY`), so a tag next to a digest
    survives; the purl keeps only the digest. When two references share a purl, the first is kept.
- **When it cannot render**, one `info` finding per chart, `sbom_chart_not_rendered`, prefixed with the module
  folder and "chart images not listed:". The build never fails on it. The cases: `helm` is not installed;
  `helm template` fails or times out (with helm's first error line); a registry chart was not pinned by the build,
  is not in `.strata/helm/`, or is there with another digest.
- **An image that still holds a token** (`${...}`, resolved only at deploy) is left out, with an `info` finding,
  `sbom_chart_image_unresolved`. An image delivered by an `${artifact:}` token is already listed through its
  artifact row.

### The SBOM: declared but not rendered

`_declared_not_rendered()` in `sbom_controller.py`, right after the merge, adds an `info` finding,
`sbom_declared_image_not_rendered`, for each declared Helm service image whose module's chart was rendered without
it:

```
service 'server' of module 'authentik' declares ghcr.io/goauthentik/server:2026.5.2, but the rendered chart
does not run it. The declaration is kept, marked not delivered.
```

- `info`, not a warning: the declaration may be deliberate documentation.
- The merge has already dropped the marker from a declared image the chart runs, so every component still marked
  is a candidate. Matching is by purl: a declared `server:2026.5.2` and a rendered `server:2026.5.2@sha256:...`
  differ, which is correct, since the declaration does not say which digest runs.
- A chart counts as rendered when it yielded at least one image. A chart that was not rendered or failed gives no
  finding for its module: nothing is known.
- Rendered and declared images are compared by module: a rendered use `chart:NS/REFERENCE/CONTAINER` is resolved to
  the module through the namespace's reference; a declared use is `service:MODULE/SERVICE`. A module referenced in
  two namespaces counts as rendered when either is.
- The component's reason becomes `helm module: not in the rendered chart` only when every module declaring the
  image was rendered. Otherwise it keeps `helm module: the chart decides`.

### Rendered chart images as a build fact

The rendered images are read back from the build's `sbom.json` and judged by `image_tag_policy` like any other row.

- **Reading.** `read_sbom(build_path)` in `utils/sbom_file.py` reads `sbom.json` once: `None` when there is none, a
  `ValueError` when it cannot be used. The image gate and the audit manifest use the same loader.
- **Rows.** `rendered_chart_images(context, sbom)` in `controllers/effective_images.py` takes the components with
  `strata:found-in: rendered-chart` and a `strata:image`, and gives one row per `chart:` use:
  - `origin: chart`;
  - `name: <reference>/<container>` (for example `whoami/shipper`), what `allow` matches, so two charts with a
    container named `server` do not collide;
  - `module`: the module the namespace's reference points at, or the reference name when the solution no longer
    has it;
  - `via`: the use; `tag_source: chart`; `delivered: true`.
- **Merging.** `with_rendered_images(images, unknown, rendered)` is pure. Images are compared by purl, as the SBOM
  merge does. A rendered image that a declared row already has is not added; the declared row is judged once, as
  delivered, because the chart runs it. A Helm module with at least one rendered row is dropped from `unknown`, by
  `(namespace, module)`, so a module rendered in one namespace stays unknown in another.
- **Judging.** `check_image_tag_policy(..., build_path=)` reads the SBOM, adds the rendered rows and, whenever it
  judged some, adds an info finding, `image_tag_policy_rendered_images`: "N image(s) rendered charts run judged
  too, from `<build>/sbom.json` written `<time>`", the time being `sbom_written_at()` (the SBOM's
  `metadata.timestamp`). An unreadable SBOM is a warning, `image_tag_policy_sbom_unreadable`, and the declared
  images are still judged.
- **Rules for a chart row.** `origins` must cover `chart` (an omitted `origins` covers it). `require_tag`,
  `reject_tags`, `tag_pattern` and `require_digest` apply as to any image. `require_pin` does not: strata cannot pin
  an image inside a chart; a `charts` pin, or a registry chart's digest, fixes it. The fix text says to set the tag
  in the chart's values through the module's `configuration`, or to pin a chart version that ships the wanted tag.

  ```
  image_tag_policy: chart 'whoami/shipper' (ghcr.io/acme/log-shipper; used by chart:apps/whoami/shipper) has the
  floating tag 'latest'. Set the tag in the chart's values through the module's configuration, or pin a chart
  version that ships the tag you want.  [image_floating_tag]
  ```

### Where the policy is judged

| Caller                | Build path                                            | Rendered rows                     |
| --------------------- | ----------------------------------------------------- | --------------------------------- |
| `build run`           | its own, right after `write_sbom()`                   | yes                               |
| `build run --dry-run` | none (nothing written)                                | no: declared images only          |
| `deploy run`          | the build it deploys                                  | yes, from that build's `sbom.json` |
| `policy check`        | `--build-path`, when it exists                        | yes, from the last build          |
| `deploy images`       | none                                                  | no: declared images only          |

- `build run` judges `image_tag_policy` at the end, after the SBOM and before the CVE gate, in one check over
  declared and rendered images. A `deny` fails the build with the output on disk, as a CVE `deny` does, and the
  result goes into `policy_results.json` with the other build policies. `on_unknown_image: block` fails at the
  same point.
- To fail fast, run `strata deploy images` or `strata policy check` before `build run`: both judge the declared
  images offline in seconds.
- `deploy run` judges before any step, with its own `--pin`, and is what actually stops a deploy, so a pipeline
  that ignores the build's exit code is still caught. A Helm module's pins are applied at build, so a deploy whose
  version differs from the build's is refused before this (`build_version_mismatch`, helm-chart-deployment.md,
  "Version pins at deploy"): the rendered images judged are the ones the deploy installs.

### Out of scope

- A literal Helm service `image` is never written into the chart. To set it, write it in the chart's own keys
  under `configuration`, or use an artifact with an `${artifact:}` token. A per-service `image_values` mapping is
  not provided; revisit if a consumer needs a literal image pinned through `kind: version` into a chart.
- GitOps and script modules that declare no image are not reported as unknown.
- `deploy images --build-path`.

## Related Decisions

- [ADR-0059](../decisions/0059-judge-rendered-helm-images-declared-are-declarations.md) - judge the images a chart
  renders; a Helm module's declared image is a declaration unless a token delivers it
- [ADR-0058](../decisions/0058-artifact-token-for-pinned-image-fields.md) - the `${artifact:NAME.FIELD}` token, the
  way an artifact reaches a chart's own values
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `ModuleServiceModel.artifact`; its Helm claim
  holds for the service's resolved `image_ref`, not for the chart
- [ADR-0019](../decisions/0019-version-pinning.md) - `images` and `charts` pins
- [image-tag-policy.md](image-tag-policy.md) - the policy that exposed the gap
- [sbom-generation.md](sbom-generation.md) - the image and chart collectors

## History

- strata never wrote a Helm service image into the chart values, and v1 did not either: v1's
  `_render_module_artifacts` wrote the same `env`, `persistence` and `configuration`, and v1 used the service image
  only for Compose, `images` pins and the manifest. The real consumer with image pins (`haven`) pins its Helm apps
  under `charts:`, never `images:`. Every check that read the declared image as what runs was false assurance for
  a Helm module, and a chart-only module was invisible.
- Four options were weighed: say it in the docs (A), mark what strata does not deliver and name unknown modules
  (B), write the declared image into a chart path (C), render the chart (D). B came first because it is cheap and
  true for every module type. C was met for artifacts by the `${artifact:}` token rather than an `image_values`
  mapping. D was built for the SBOM and then fed to the policy.
- Declared-only Helm images are still judged, with "declared only" in the message, rather than skipped: the
  author wrote them on purpose and can vouch for them.
- `on_unknown_image` defaults to `warn`, as in `image_gate_policy`. A `block` default would have failed every
  existing chart module that declares no images.
- The SBOM marker is written only when an image is not delivered, so its absence means delivered and older SBOMs
  read correctly; the audit manifest's `delivered` is likewise left out unless `false`, keeping existing hashes.
- Rendering was possible offline once strata wrote `strata-values.yaml` next to a local chart instead of editing the
  chart's `values.yaml`: the render then uses exactly what `deploy run` installs. Registry charts render from the
  cached archive only when its digest matches `chartDigest`, the same check `deploy run` makes.
- The `chart:` use names the container, and a rendered component carries `strata:image`: the merge keeps one name
  per purl, which lost the second container of a shared image, and a digest-pinned purl drops the tag as written.
- `image_tag_policy` moved from the start of `build run` to after the SBOM, so it sees rendered images. A bad tag
  is now reported after cloning and rendering, not before; `deploy images` and `policy check` are the fast
  offline check.
- `require_pin` does not apply to a chart row: strata cannot pin an image inside a chart, so the finding would only
  be noise.
- `render_charts` was first designed as opt-in, then a notice release before flipping the default, to avoid an
  unchanged pipeline failing on upgrade (the 2.3.2 lesson). Since `render_charts` had not shipped yet, the default
  became `true` from its first release and the notice was dropped. Rejected: a `build run --render-charts` flag (the
  gates are workspace settings) and rendering by default while feeding the policies only on opt-in (two switches
  for one fact; the SBOM would list images the gates silently skip).
- The Helm delivery rule for tokens was once two rules: `artifact_not_delivered` counted only chart-key tokens,
  while `effective_images()` counted any token, so adding an environment variable flipped the answer without
  changing what runs. `helm_delivered_artifacts()` is now the one rule both call.
- The dogfood build found that a relative `--build-path` rendered no chart, because `helm template` got relative
  paths while running in the chart's folder; `HelmIntegration.template()` now resolves them.
