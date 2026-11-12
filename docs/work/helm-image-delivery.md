# Helm module images - Work

- Status: option B and plan item 8 built (2026-11-11), not released; plan item 9 designed, 9a to 9f built (2026-11-12)
- Last updated: 2026-11-12

## Overview

A module service can declare an `image` (or an `artifact`). For a Compose module strata writes that image into
the compose file, so the declared image is what runs. For a Helm module it does not: the rendered chart values
contain `env`, `persistence` and any `configuration` of the service, and no image. The chart decides which image
runs. So a Helm module's declared service image is a statement about the module, not an instruction to the chart.

Everything that reads `ModuleServiceModel.image` treats it as what runs: `strata deploy images`, the SBOM, the
CVE policy, the image gate, `kind: version` `images` pins and `image_tag_policy`. For a Helm module a clean
result from any of them can be false assurance, and a chart that declares no services (or has images beyond the
ones declared) is invisible. This doc settles what strata should say about images it does not control, and
whether it should start controlling them.

It started from [image-tag-policy.md](../design/image-tag-policy.md), where this was the largest known gap.

## Current state

Verified against the source on 2026-11-02.

### What strata does with a service image

| Consumer                                      | Compose module                                                      | Helm module                                                                                                                                           |
| --------------------------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rendered output                               | Written as `image:` in the compose file (`integrations/compose.py`) | Not written. `integrations/helm.py` builds `<module>-<service>: {env, persistence, <service configuration>}` and merges the module's `configuration`. |
| `kind: version` `pins.images`                 | Changes the image that runs                                         | Changes the declared value only (SBOM, `deploy images`, audit). The chart is unaffected.                                                              |
| `deploy images`, SBOM, CVE policy, image gate | Judge what runs                                                     | Judge the declared image, which may not be what runs.                                                                                                 |
| `image_tag_policy`                            | Judges what runs                                                    | Judges the declared image, which may not be what runs.                                                                                                |

Evidence:

- Rendering `config/` (`strata build run prd-deployment` on a copy, 2026-11-02) writes
  `build/prd-deployment/hearth/authentik/values.yaml` with `authentik-db`, `authentik-redis`, `authentik-server`
  and `authentik-worker` entries holding `env` and `persistence`, and no `image` key anywhere. The module is
  `type: helm` and declares `docker.io/library/postgres:16-alpine`, `redis:7-alpine` and
  `ghcr.io/goauthentik/server:2026.5.2` as service images.
- `integrations/helm.py` contains no reference to `image`. The Compose integration writes it
  (`entry["image"] = service.image`).
- A module with no `services` and no `configuration` writes no values file at all, and a chart's own images then
  come only from the chart.

### What v1 did

Checked in the v1 source (xyz-strata 1.11.2, `E:\SourcesXYZ\strata`):

- `builders/helm_builder.py::_render_module_artifacts` writes the same three things as v2 (`env`, `persistence`,
  service `configuration`) and no image. v2's version was ported from it. v1 never gave a Helm chart its service
  image through the values.
- v1 used the service image for the Compose file (`builders/compose_builder.py`), for `images` version pins, and
  for the manifest's `image_versions` (`builders/platform_builder.py`: module name to the first service image).
- In `haven` (the real consumer with `kind: version` image pins): `images:` pins are for the Hearth Compose
  modules (`db`, `redis`, `server`, `worker`, `portainer`, `vaultwarden`, ...). The Forge Helm apps (`cert-manager`,
  `immich`, `jellyfin`, `nextcloud`, ...) are pinned under `charts:`, never under `images:`.
- v1 let a Helm module set an image through `configuration` (the chart's own values, for example `image.tag`) and
  validated those keys against the chart's `values.yaml` (`check_helm_values`). v2 keeps that check
  (`check_configuration_keys`).

So a Helm image has only ever reached the chart through `configuration`, written by the author in the chart's own
key shape, which strata does not interpret.

### Why strata cannot just interpret `configuration`

Charts disagree on where the image lives: `image.repository` and `image.tag`, `image: "repo:tag"`,
`controllers.main.containers.main.image.tag` (bjw-s common-library charts such as immich), per-component
`server.image.tag`, and so on. A chart may also set no tag and fall back to its `appVersion`. Any rule that reads
`configuration` for an image is a heuristic per chart.

## Four levels of certainty

| What the image is                                                                             | Does strata know what runs?                                                                    | Judged today     |
| --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------- |
| Compose service `image`                                                                       | Yes                                                                                            | Yes              |
| Artifact (`store: artifact`, or a service `artifact`)                                         | Yes for Terraform variables, Compose and an `${artifact:}` token in Helm values; a Helm service `artifact` alone is declared only. | Yes              |
| Helm module `services[].image` or `artifact`                                                  | No: declared only                                                                              | Yes, as if known |
| An image only the chart knows (no `services`, or extra images, or a `configuration` override) | No                                                                                             | No, silently     |

## Options

| Option                            | What it does                                                                                                                                                                                                                                                                                    | Cost and risk                                                                                                                     |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| A. Say it                         | Document that a Helm module's declared image is metadata, and mark such rows in `deploy images`.                                                                                                                                                                                                | Small. Honest. Fixes nothing.                                                                                                     |
| B. Know what strata does not know | Add a `delivered` fact to the effective image rows (true when strata writes the image into what runs). List each enabled chart module that declares no image as "images known only to Helm". The tag policy gets `on_unknown_image: skip \| warn \| block`, as `image_gate_policy` already has. | Small to medium. Makes the gap visible and lets a team choose to fail on it.                                                      |
| C. Deliver the image to the chart | A service says where in the chart's values its image goes, and strata writes it there, so the declared image, the `images` pin and an `artifact` become authoritative.                                                                                                                          | Medium. Needs a per-service mapping, and a check that the path exists in the chart's `values.yaml`.                               |
| D. Render the chart               | Run `helm template` at build and read the real images from the manifests.                                                                                                                                                                                                                       | Large. Needs Helm and the chart fetched, so `build run` is no longer offline. Exact for everything, including chart-only modules. |

### Sketch of option C

```yaml
services:
  - name: server
    image: ghcr.io/goauthentik/server:2026.5.2
    image_values: authentik.server.image     # where the chart reads it; strata writes repository and tag here
```

- Strata splits the reference (`parse_image_ref`) and writes `{repository, tag}` (or `{repository, digest}`) at the
  path, after pins and artifact resolution, so a pin changes what runs.
- The path is checked against the chart's default `values.yaml` with the existing `check_configuration_keys`, so a
  wrong path fails at build instead of being ignored by Helm.
- Writing the same path from `configuration` is an error, not a silent override.
- A chart that takes one `repository:tag` string needs a second form (`image_values: {path: ..., form: ref}`).
  Whether to support it is a question for a real consumer.

**Met for artifacts by the `${artifact:NAME.FIELD}` token** (ADR-0058, 2026-11-10). A module writes the pinned image
in the chart's own keys, `configuration: {image: {repository: "${artifact:x.image_name}", tag:
"${artifact:x.image_tag}"}}`, with no `image_values` field, and `check_configuration_keys` still checks the path
against the chart's `values.yaml`. A one-string chart takes `${artifact:x.image_ref}`. `strata validate` warns
`artifact_not_delivered` for a Helm service that names an `artifact` with no such token
([artifact-gap-analysis.md](artifact-gap-analysis.md), design 2). A literal `services[].image` on a Helm module still
has no way in except writing it by hand into `configuration`.

### Sketch of option B output

```
NAME    ORIGIN          IMAGE                       TAG        SOURCE    DELIVERED
db      module-service  docker.io/library/postgres  16-alpine  pin prd   no (helm module: the chart decides)

  warning  image_tag_policy: module 'immich' (helm chart immich 0.13.1) declares no images, so the images
           the chart deploys are known only to Helm and are not judged.  [image_unknown_to_strata]
```

`policy.on_unknown_image` decides whether that line is skipped, a warning, or an error.

## Proposal

1. Do B first. It is cheap, it is true for every module type, and it stops the tag policy, the SBOM and the gates
   claiming more than they know.
2. Do C only when a real consumer wants a Helm service image pinned through `kind: version`. Today no consumer
   does (haven pins Helm apps by chart version). Until then, a Helm image is set through `configuration`.
3. Leave D until a consumer needs chart-only modules judged.

## Design: option B (2026-11-11)

Every place that lists or judges images says which ones strata delivers. It also names the Helm modules whose
images strata does not know at all. Nothing changes in what is rendered or deployed. The chart version is still
the only thing that fixes a Helm module's images; strata now says so instead of judging a declaration as if it
ran.

### 1. Delivered: one rule, in the resolver

Strata delivers an image when it writes the image into something that runs:

- the Compose integration writes a service `image` (`compose.py`, `entry["image"] = service.image`);
- a `store: artifact` variable reaches an execution step;
- an `${artifact:}` token is resolved into a document the deployment uses.

No other module integration writes an image: not `helm.py`, the GitOps integrations (`argocd`, `flux`), `script`
or a custom plugin.

`EffectiveImage` (`integrations/resolved_context.py`) gets two fields with defaults, so existing constructors do
not change:

- `delivered: bool = True`
- `delivery: str | None = None`, the reason when `delivered` is false

`effective_images()` sets them:

| Row | `delivered` | `delivery` |
| --- | --- | --- |
| `module-service` on a `compose` module | true | - |
| `module-service` on a `helm` module | false | `helm module: the chart decides` |
| `module-service` on any other module type | false | `<type> module: strata writes no image` |
| `artifact` with at least one delivering use: `variable:KEY`, `token:KIND/NAME`, or `service:` on a `compose` module | true | - |
| `artifact` whose only uses are `service:` on non-Compose modules | false | `helm module: no ${artifact:} token` (or the type, as above) |

The resolver already resolves each module (`resolve_module()`), so `spec.type` is at hand; no extra lookup.

**Known limit.** A `token:` use names the document, not the field. So an `${artifact:}` token in a Helm
service's `environment` counts as delivered here, although it only sets an environment variable.
`artifact_not_delivered` still warns about that at `strata validate`, so the case is not silent.

### 2. Modules whose images strata does not know

There is a new frozen dataclass next to `EffectiveImage`:

```python
@dataclass(frozen=True)
class UnknownImageModule:
    module: str          # module document name
    namespace: str       # the namespace whose modules[] enables it
    chart: str | None    # source.chart_name
    chart_version: str | None  # after a `charts` pin, so it is the version that deploys
```

There is also a new function, `unknown_image_modules(context, deployment) -> list[UnknownImageModule]`, in
`controllers/effective_images.py`. It uses the same walk as `effective_images()`: the deployment's workspace,
its namespaces, enabled references and version pins applied. The walk becomes one private generator that both
functions use, so they cannot disagree on which modules count.

- **What counts:** an enabled `helm` module whose `services` declare neither an `image` nor an `artifact`. That
  covers no `services`, or services that carry only `env`, `persistence` or `configuration`.
- **Kept out of `effective_images()`.** The SBOM, the audit manifest, the gates and the report all expect every
  row to have an image. A row with no image would break each of them.
- **Helm only.** An `argocd`, `script` or custom module may run no containers at all, such as a script that sets
  DNS. Reporting them would be noise. (See the open questions.)
- **Not reported:** a Helm module that declares some images but whose chart runs more. Strata cannot tell
  without rendering the chart, which is option D.

### 3. `strata deploy images`

The console gets a DELIVERED column, shown only when some row is not delivered, as DIGEST is. Below the table is a
list of the unknown modules:

```
  NAME     ORIGIN          IMAGE                        TAG        SOURCE    DELIVERED                                USED BY
  api      artifact        acr.example.io/acme/api      1.4.0      pin prd   yes                                      token:module/api
  server   module-service  docker.io/vaultwarden/server 1.32.0     declared  yes                                      service:vaultwarden/server
  db       module-service  docker.io/library/postgres   16-alpine  pin prd   no (helm module: the chart decides)      service:authentik/db
  server   module-service  ghcr.io/goauthentik/server   2026.5.2   declared  no (helm module: the chart decides)      service:authentik/server

  Known only to Helm (declare no images, so none of theirs is listed):
    immich  namespace forge  chart immich 0.13.1
```

JSON gets the new fields and a new list, so a pipeline can tell the two kinds of image apart:

```json
{"images": [{"name": "db", "module": "authentik", "image_ref": "docker.io/library/postgres:16-alpine",
             "delivered": false, "delivery": "helm module: the chart decides", "...": "..."}],
 "unknown_image_modules": [{"module": "immich", "namespace": "forge", "chart": "immich",
                            "chart_version": "0.13.1"}]}
```

- **Without an `image_tag_policy`:** one info finding per unknown module, `image_unknown_to_strata`. The run
  gets no warning, because nothing asked for one, as a missing tag is only a warning there.
- **With an `image_tag_policy`:** the policy decides (section 4).

### 4. `image_tag_policy.on_unknown_image`

```yaml
spec:
  image_tag_policy:
    enforcement: deny
    on_unknown_image: warn     # skip | warn | block (default warn)
```

- `on_unknown_image` decides only about the unknown modules, separately from `enforcement`. `image_gate_policy`
  has the same field and works the same way.
  - `block` is an error: `build run` and `deploy run` stop, and `deploy images` and `policy check` exit 3.
  - `warn` is a warning.
  - `skip` is an info finding, which is how `image_gate_policy` handles `skip` too: it stays out of the summary
    but remains in `-v` and JSON output.
- The finding:

  ```
  image_tag_policy: module 'immich' (helm chart immich 0.13.1) declares no images, so the images the chart
  deploys are known only to Helm and are not judged. Declare them under spec.services[].image to have them
  judged, or set on_unknown_image: skip.  [image_unknown_to_strata]
  ```

- The policy's existing filters apply to unknown modules too:
  - **`rings`:** a deployment outside the policy's rings gets nothing, as today.
  - **`origins`:** unknown modules are reported only when `origins` covers `module-service`, because they are
    module images.
- **`allow` does not exempt an unknown module.** Its names are image names (an artifact or a service), not module
  names. To clear the finding, declare the chart's images. They are then judged as declared, which is what the
  author can vouch for.
- **A declared-only image is still judged**, with the same codes and rules, so what an author wrote on purpose
  is not dropped. The message says the image is declared only:

  ```
  image_tag_policy: module-service 'server' (ghcr.io/goauthentik/server; used by service:authentik/server;
  declared only, the chart decides which image runs) has the floating tag 'latest'.  [image_floating_tag]
  ```

- Code: `evaluate_image_tag_policy(images, policy, ring, *, unknown=(), today=None)` stays pure.
  `check_image_tag_policy()` gets `unknown_image_modules()` and passes them on, so its four callers do not
  change: `deploy images`, `build_controller`, `deploy_controller` and `policy_controller`. `deploy images`
  passes the list it already printed, as it does `images`.

### 5. What does not change

- **Rendering:** nothing new is written into a chart. That is option C.
- **`images` pins on a Helm service** still move only the declaration. The DELIVERED column now shows that.
- **SBOM, CVE and image gates:** unchanged. Plan item 6 (the `strata:image-delivered=false` SBOM property) can
  then read `EffectiveImage.delivered` instead of working it out again.
- **Audit manifest:** its image entries keep their schema. Adding `delivered` there is part of plan item 6, so
  the audit and the SBOM gain the marker together.
- **`deploy report`:** dumps `EffectiveImage` with `asdict()`, so `delivered` and `delivery` show up there with no
  code change.

### 6. Compatibility

- The new JSON fields and the `unknown_image_modules` list are additive.
- A workspace with an `image_tag_policy` and a Helm module that declares no images gets a **new warning** on
  `build run` and `deploy run`. It does not fail unless `on_unknown_image: block` is set. In `config/`, the
  `authentik` Helm module declares its images, so it gets no new finding.
- `.github/CHANGELOG.md`, Added: the DELIVERED fact, the unknown-module list and `on_unknown_image`.

### 7. Tests

- **`effective_images()`:**
  - `delivered` for Compose, Helm and `script` service rows;
  - an artifact reached by a variable;
  - an artifact reached by a token in a Helm module;
  - an artifact used only by a Helm service with no token, which is false.
- **`unknown_image_modules()`:**
  - a chart-only Helm module is listed, with its pinned chart version;
  - one that declares an image is not, nor is one that declares an artifact;
  - a disabled reference and a Compose module are not listed;
  - a module in two namespaces is listed once per namespace.
- **`evaluate_image_tag_policy()`:**
  - `skip`, `warn` and `block` map to info, warning and error;
  - `rings` and `origins` exclude unknown modules;
  - the declared-only wording;
  - `enforcement: warn` with `on_unknown_image: block` still errors.
- **`deploy images`:**
  - the DELIVERED column appears only when needed;
  - the unknown-module section;
  - the JSON fields;
  - exit 3 on `block`.
- **The model:** `on_unknown_image` defaults to `warn` and rejects another value.

## Design: the SBOM side (plan item 8, 2026-11-11)

This is the deferred SBOM work, designed on 2026-11-07 in the work doc `sbom-generation.md`, which was later
removed. It is restated here against today's code. Two parts:

- a **marker**: the SBOM, the image gate and the audit manifest say which images strata does not deliver;
- **rendered charts**: the SBOM lists the images a local chart actually deploys.

### Current state (2026-11-11)

- `sbom.json` lists a Helm module's declared service images as if they ran. It lists none of the images its chart
  deploys on its own: the app, sidecars, init containers and subcharts.
- `cve_policy` scans `sbom.json`, and `image_gate_policy` reads its images from it (`images_from_sbom()`). So they
  scan declared images and miss rendered ones.
- `graph.images`, which `write_sbom()` receives, already holds `EffectiveImage.delivered` (option B).
- The values-file problem the 2026-11-07 doc found is gone. Strata writes `strata-values.yaml` next to a local
  chart, and the chart's own `values.yaml` is left alone. So rendering a local chart's folder with
  `-f strata-values.yaml` renders what `deploy run` installs, apart from values whose tokens resolve only at
  deploy.
- `build run` already runs `helm`: it pulls registry charts and runs `dependency list`.

### Part 1: the marker

- **SBOM.** `ImageCollector` adds `strata:image-delivered: false` and `strata:image-delivery: <reason>` to a
  container component when no `graph.images` row with its purl is delivered. Every use is considered, so an image
  that a Helm module declares but a Compose module also runs is delivered, and has no marker.
  - The property is written only when the image is not delivered. A missing property means delivered, so an
    older SBOM still reads correctly. `strata:pinned: false` works the same way.
- **Merge.** `_merge_by_purl()` drops both properties when another component with the same purl comes from a
  rendered chart (part 2). The image is then what runs.
  - It also joins `strata:used-by` values instead of keeping the first. Today the first collector's list wins,
    and a rendered chart's use would be lost.
- **Image gate.**
  - `images_from_sbom()` reads the marker into `ImageRef.delivered`, which defaults to true.
  - The gate's label for such an image ends with `(declared only)`. It shows on every finding and on the
    "asking about" step, so "the vendor knows nothing bad about X" is not read as being about what runs.
- **Audit manifest.** `ManifestImageModel` gets `delivered: bool | None`. It is set to `false` only when the image
  is not delivered, and left out otherwise (`exclude_none`), so existing manifests and their hashes are unchanged.
- **CVE gate:** unchanged. Trivy and Grype scan the SBOM, and the marker is there for whoever reads their
  results, Dependency-Track included.

### Part 2: render local charts

A new SBOM collector, `chart-image`, lives in `integrations/sbom_collectors/chart_image_collector.py` and is
registered in `_KNOWN` after `helm`.

- **Opt-in:** `spec.sbom.render_charts: true` on the workspace, default `false`.
  - Rendered images reach `cve_policy` and `image_gate_policy`. A `deny` gate can then fail a build that passed
    the day before, because of a sidecar nobody had scanned. That is the point, but turning it on by default would
    break pipelines on upgrade, the 2.3.2 lesson.
  - It is a workspace setting, not a `build run` flag, because the gates it feeds are workspace settings too.
  - It can become the default in a later minor release.
- **Which charts:** every folder in the build path that holds a `Chart.yaml` and a `meta.yaml` without
  `chartName`, which is a local chart. `deploy run` installs from that same folder. The collector finds them by
  file, as the other file collectors do.
  - Registry charts were not rendered by item 8. Their unpacked copy is in `.strata/helm/`, outside the build
    path, and the collector read only the build path. Plan item 9b renders them.
- **Render:** `helm template <releaseName> <folder> --namespace <namespace> -f <folder>/strata-values.yaml`.
  - The release name and namespace come from `meta.yaml`.
  - It runs offline, with no `--dependency-update` and no repository access, through a new
    `HelmIntegration.template()`, with a 120 s timeout.
  - The rendered text stays in memory and is never written. Strata's values file holds `${secret:}` tokens, not
    secrets, so the render has nothing secret to leak in any case.
- **Collect:** the pure function `rendered_images(text)` reads each document (`yaml.safe_load_all`) and walks it.
  Every `image` under a `containers`, `initContainers` or `ephemeralContainers` list counts. Walking covers
  Deployment, StatefulSet, DaemonSet, Job, CronJob and Pod, and also CRDs that embed a pod spec.
- **Component:**
  - type `container`, with the purl from `image_to_purl()`, `hashes` from `image_digest_hash()`, and tag
    stability (`strata:tag-stability: floating` with a warning) as for any image;
  - `strata:used-by: chart:<namespace>/<reference>/<container>` (the container since plan item 9a);
  - `strata:image`, the reference as the chart writes it (plan item 9a);
  - `strata:found-in: rendered-chart`;
  - its name is the container name.
- **When it cannot render:** one `info` finding per chart, `sbom_chart_not_rendered`. The chart's images are not
  listed, and the build never fails because of it. The cases:
  - `helm` is not installed;
  - `helm template` fails, for example on a dependency that is not vendored under `charts/`. The finding gives
    helm's first error line.
  - The build still passes `--offline` to the chart dependencies; rendering never needs the network.
- **An image that still holds a token** (`${...}`, a value resolved only at deploy) is left out, with an `info`
  finding (`sbom_chart_image_unresolved`). An image delivered by an `${artifact:}` token is already in the SBOM
  through its artifact row.

### Example

A local chart `whoami` deploys `traefik/whoami:v1.10` with a `busybox:1.36` init container, and its module
declares no services:

```json
{"type": "container", "name": "whoami", "version": "v1.10", "purl": "pkg:docker/traefik/whoami@v1.10",
 "properties": [{"name": "strata:found-in", "value": "rendered-chart"},
                {"name": "strata:used-by", "value": "chart:apps/whoami/whoami"},
                {"name": "strata:image", "value": "traefik/whoami:v1.10"}]}
```

authentik's declared `ghcr.io/goauthentik/server:2026.5.2`, without rendering, which is the default and also
the case for a registry chart:

```json
{"type": "container", "name": "server", "purl": "pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io",
 "properties": [{"name": "strata:image-delivered", "value": "false"},
                {"name": "strata:image-delivery", "value": "helm module: the chart decides"},
                {"name": "strata:used-by", "value": "service:authentik/server"}]}
```

The image gate then logs: `image gate: asking aikido about ghcr.io/goauthentik/server:2026.5.2 (declared only)`.

### Not in this item

- `deploy images`, `image_tag_policy` and `unknown_image_modules()` keep reading the solution only. They do not
  read rendered images, which exist only after a build. So a rendered local chart still shows as "known only to
  Helm" in `deploy images`. Plan item 9 takes this up for the policy.
- A declared Helm image that the rendered chart does not contain keeps its marker. The declaration may be
  deliberate documentation. Plan item 9 adds an `info` finding for it.

### Tests

- **Marker:**
  - an image only a Helm module declares is marked;
  - the same image also on a Compose module is not;
  - Compose and artifact images are never marked;
  - the merge drops the marker for a rendered image and joins `used-by`.
- **Gate:**
  - `images_from_sbom()` reads `delivered`;
  - the label ends with `(declared only)`.
- **Audit:** `delivered: false` is written only for an undelivered image.
- **`rendered_images()`:**
  - a Deployment, a CronJob, an init container, a CRD with a pod spec;
  - multi-document output, an empty document;
  - an image with a token.
- **Collector**, with `HelmIntegration` stubbed:
  - it is off by default;
  - it finds local charts only;
  - the helm-missing and template-failure findings;
  - the components and their properties.
- **End to end:** one test against real `helm`, skipped when Helm is not installed.

## Design: rendered chart images as a build fact (plan item 9, 2026-11-12)

Item 8 made the rendered chart an input to the SBOM only. Four follow-ups are one gap: nothing else in strata knows
what a chart runs.

1. `image_tag_policy` and `on_unknown_image` do not see rendered images. The dogfood build rendered `whoami`'s
   three images and still warned that its images are known only to Helm.
2. Registry charts are not rendered, so 1 would help local charts only.
3. A declared Helm image that the rendered chart does not run keeps its marker and says nothing.
4. `render_charts` is off by default.

This item does 1 to 3. Number 4 waits for a release with 1 to 3 in it (see the open questions).

### Current state (2026-11-12)

- `build run` judges `image_tag_policy` first, before it cleans or writes anything
  (`build_controller.py`, "Image tag policy"). `cve_policy`, `checkov_policy` and `provisioner_lock_policy` run
  last, after the render and the SBOM. A `deny` there fails the build and leaves the output on disk, and the
  results go to `policy_results.json`.
- `deploy run` reads `policy_results.json` only for the audit manifest. It never refuses a build because of it. It
  runs its own checks: `image_tag_policy` again, with the deploy's `--pin`, and `image_gate_policy` over the build's
  `sbom.json` (`images_from_sbom()`).
- `policy check` judges `image_tag_policy` offline, from the solution, and records into `--build-path`.
  `deploy images` has no build path.
- The chart-image collector writes rendered components with `strata:found-in: rendered-chart` and `strata:used-by:
  chart:<namespace>/<reference>`. A digest-pinned image's purl keeps only the digest, so the reference as written
  in the chart cannot be read back from the purl.
- `_merge_by_purl()` keeps one component, with one name, per purl, and joins the uses. Two containers that run
  the same image, in one chart or in two, become one component: the use says which charts, but not which
  containers.
- A registry chart's `meta.yaml` holds `chartName`, `chartVersion` and `chartDigest`. Its archive is in
  `.strata/helm/`, which `cached_pin()` finds. `deploy run` installs that archive once its digest matches
  `chartDigest`.

### 1. `image_tag_policy` moves to the end of the build

- `build run` judges the policy last, next to the other build policies, in one check over the declared and the
  rendered images. One `image_tag_policy` entry goes into `policy_results.json`, as today.
- A `deny` fails the build after the render, as a CVE `deny` does. The output stays on disk. With `--clean`, the
  previous build is already gone by then. This is a visible change: a bad tag is now reported after the sources are
  cloned and the charts are rendered, not before.
- **Failing fast is still possible:** `strata deploy images` and `strata policy check` judge the declared images
  offline in seconds, with no build. A pipeline that wants the early answer runs one of them before `build run`.
  The how-to says so.
- **`--dry-run`:** the end of the build is skipped in a dry run, but the policy is not. A dry run judges the
  declared images only, as today, because nothing is rendered.
- Without `render_charts`, the check is the same as today's, only later.
- **How it is built** (plan item 9e): in `build_run()`, `effective_images()` still runs early, because the
  resolved graph carries the images. `check_image_tag_policy(..., build_path=)` runs right after `write_sbom()`,
  before the CVE gate; it no longer returns early, so the other gates still run and every result is recorded in
  the one `policy_results.json`. A dry run calls it without a build path where the SBOM would have been written.
  With `--clean`, the previous build is gone before a `deny` is known.

### 2. Rendered images as policy rows

- **The SBOM keeps what the rows need** (plan item 9a):
  - a rendered component gets `strata:image: <reference as the chart renders it>`, so a tag next to a digest
    survives (`traefik/whoami:v1.10.2@sha256:...`). When two references share a purl, the first one is kept;
    they then differ only in a tag next to the same digest;
  - the use names the container: `chart:<namespace>/<reference>/<container>`. It survives the merge, which keeps
    a single component name. Item 8 is not released, so the changed value breaks nothing.
- **Reading them back:** a new `rendered_chart_images(build_path) -> list[EffectiveImage] | None` in
  `controllers/effective_images.py` reads `sbom.json` and returns one row per `chart:` use of a rendered
  component. It returns `None` when there is no SBOM.
  - `origin: chart`, a new origin;
  - `name: <reference>/<container>`, for example `whoami/shipper`, so that two charts with a container named
    `server` do not collide in `allow`;
  - `module` is the module reference, `via` is the use, `chart:<namespace>/<reference>/<container>`;
  - `tag_source: chart`, and `delivered: true`.
- **`check_image_tag_policy(..., build_path=None)`.** When it is given and the SBOM exists:
  - the rendered rows are added;
  - a rendered row whose image a declared row already has is not added. The declared row is judged once, as
    delivered, because the chart runs it;
  - a Helm module with at least one rendered row is no longer unknown, so `on_unknown_image` does not report it. A
    chart that renders no container stays unknown; that is not a real case.
- **Policy fields for a chart row:**
  - `origins` gains `chart`. A workspace that lists `origins` today does not list `chart`, so it does not get the
    rendered images until it adds it. One with no `origins` judges every origin, chart rows included.
  - `allow` matches `<reference>/<container>`.
  - `require_tag`, `reject_tags`, `tag_pattern` and `require_digest` apply as to any image.
  - `require_pin` does not apply. Strata cannot pin a chart's image; a `charts` pin, or the chart's digest for a
    registry chart, fixes it. The finding would only be noise.
- **The fix text** for a chart row: "Set the tag in the chart's values through the module's `configuration`, or
  pin a chart version that ships the tag you want." For `require_digest`: "Set the image as `TAG@sha256:<hex>` in
  the chart's values through the module's configuration."
- **How it is built** (plan item 9d):
  - `rendered_chart_images(context, build_path)` in `effective_images.py` reads `sbom.json` itself, as
    `images_from_sbom()` does for the image gate. It takes only components with `strata:found-in: rendered-chart`
    and a `strata:image`, and gives one row per `chart:` use. `module` is resolved through the namespace document
    in the solution; when the solution no longer has that reference, it falls back to the reference name.
  - `with_rendered_images(images, unknown, rendered)` is pure. Images are compared by purl, as the SBOM merge
    does. Unknown modules are dropped by `(namespace, module)`, so a module rendered in one namespace stays unknown
    in another.
  - `check_image_tag_policy(..., build_path=)`. An SBOM that cannot be read is a warning,
    `image_tag_policy_sbom_unreadable`, and the solution is still judged: the build wrote that file, so a broken
    one is worth hearing about, but it should not hide the declared images' findings.
  - No caller passes `build_path` yet; 9e and 9f do. Until then nothing changes for a user.
- **Example:**

  ```
  image_tag_policy: chart 'whoami/shipper' (ghcr.io/acme/log-shipper; used by chart:apps/whoami/shipper) has the
  floating tag 'latest'. Set the tag in the chart's values through the module's configuration, or pin a chart version
  that ships the tag you want.  [image_floating_tag]
  ```

### 3. Who reads the rendered rows

| Caller | Build path | Rendered rows |
| --- | --- | --- |
| `build run` | its own, after `write_sbom()` | yes |
| `build run --dry-run` | none (nothing written) | no |
| `deploy run` | the build it deploys | yes, from that build's `sbom.json` |
| `policy check` | `--build-path` when it exists | yes, from the last build |
| `deploy images` | none | no: declared only, unchanged |

- `deploy run` is what actually stops a deploy, so a pipeline that ignores the build's exit code is still caught.
- `policy check` judges the last build's rendered images. Its output says where they came from: "rendered images
  from the build of <time>", the `sbom.json` timestamp.
- `deploy images` stays offline and build-free. Giving it `--build-path` is possible later, on demand.
- **How it is built** (plan item 9f): `deploy_run()` and `policy check`'s `_image_tag_check()` pass their build
  path to `check_image_tag_policy()`. Whenever rendered rows are judged, it adds an info finding,
  `image_tag_policy_rendered_images`: "N image(s) rendered charts run judged too, from <build>/sbom.json written
  <time>", the time being the SBOM's `metadata.timestamp` (`sbom_written_at()`). It shows at build and deploy too,
  not only in `policy check`: one line, and at deploy it is just as useful to know which build was read. An info
  finding is not a violation, so the recorded result is unchanged.

### 4. Registry charts

- The chart-image collector also renders a registry chart: `helm template <release> <archive> --namespace <ns> -f
  <folder>/strata-values.yaml`, where the archive is the `.tgz` in `.strata/helm/` that `cached_pin()` finds for the
  `meta.yaml` coordinates.
- **Digest first:** it renders only an archive whose digest is `chartDigest`, as `deploy run` checks before
  installing. Any other case is an `info` finding, `sbom_chart_not_rendered`, and the chart is skipped:
  - the build did not pin it, for example `--offline` with an empty cache;
  - the archive is missing or has another digest.
- **The cache path:** `write_sbom()` gains `helm_cache: Path | None`, which `build run` sets to
  `helm_cache_dir(context.root)`, and passes it to the chart-image collector only, as its `helm_cache` attribute.
  The `collect()` signature the other collectors implement does not change. Without it, registry charts are
  skipped silently, as before.
- **Finding the folders:** every module folder whose `meta.yaml` has a release name and a namespace. One with
  `chartName` is a registry chart; one without it needs a `Chart.yaml`, as before.
- **The render:** `HelmIntegration.template()` takes the archive as the chart and runs in the archive's folder.
  The values file is the module folder's `strata-values.yaml`, as for a local chart.
- Offline, as for local charts: the archive already holds its subcharts.
- **The findings** (`sbom_chart_not_rendered`, `info`), prefixed with the module folder and "chart images not
  listed:":
  - `chart 'immich' was not pinned by the build`;
  - `chart 'immich' 0.13.1 is not in .strata/helm/`;
  - `chart 'immich' 0.13.1 in .strata/helm/ is sha256:..., but the build pinned sha256:...`.

### 5. Declared but not rendered

- When a Helm module's chart was rendered and a declared service image of that module is not among the rendered
  images, `write_sbom()` adds an `info` finding, `sbom_declared_image_not_rendered`:

  ```
  service 'server' of module 'authentik' declares ghcr.io/goauthentik/server:2026.5.2, but the rendered chart
  does not run it. The declaration is kept, marked not delivered.
  ```

- `info`, not a warning: the declaration may be deliberate documentation, and the chart may pick the image by a
  value strata does not set. The component keeps its marker, with the reason `helm module: not in the rendered
  chart`.
- Matched on the purl, so a declared `server:2026.5.2` and a rendered `server:2026.5.2@sha256:...` differ. That
  is correct: the declaration does not say which digest runs.
- **How it is built** (plan item 9c): `_declared_not_rendered()` in `sbom_controller.py`, right after
  `_merge_by_purl()`. The merge has already dropped the marker from a declared image the chart runs, so every
  component still marked is a candidate.
  - **Which modules were rendered:** a rendered image's use is `chart:NS/REFERENCE/CONTAINER`, and the
    namespace's reference names the module (`graph.namespaces`). A declared image's use is
    `service:MODULE/SERVICE`, by module, so the two are compared by module.
  - **A chart counts as rendered when it yielded at least one image.** A chart that failed to render, or was not
    rendered, yields none, so its module's declarations get no finding: nothing is known about them.
  - **The reason changes only when every module declaring the image was rendered.** An image two Helm modules
    declare, one rendered and one not, gets the finding for the rendered one and keeps "the chart decides".
  - The finding's `location` is the module. The declared reference comes from `graph.images`.
  - A module referenced in two namespaces counts as rendered when either is: the declared use does not carry
    the namespace. Both render the same chart in practice.

### 6. Compatibility

- **Changed:** a `deny` from `image_tag_policy` now fails `build run` at the end, not at the start. `on_unknown_image:
  block` moves with it.
- **New findings, only with `render_charts`:** rendered images are judged by `image_tag_policy`, at build and at
  deploy, unless `origins` leaves `chart` out. A `deny` policy can then fail a build that passed before.
- **Fewer findings:** a rendered Helm module loses its `image_unknown_to_strata` warning.
- **Additive:** the `chart` origin, the `strata:image` property, the two finding codes, registry charts in the SBOM.
- Docs: the "Where it runs" table in [image-tag-policy.md](../design/image-tag-policy.md) and in
  [workspace.md](../config/workspace.md), which both promise "before anything is cleaned or written", and the
  comment in `build_controller.py`.

### Not in this item

- `deploy images --build-path`.
- A deploy whose `--pin` differs from the build's for a Helm module: the deploy judges the build's rendered
  images, which carry the build's pin. See the open questions.
- `render_charts` by default.

### Tests

- **`rendered_chart_images()`:** reads `strata:image`, keeps tag and digest, names rows `<reference>/<container>`;
  `None` without an SBOM; ignores components that were not rendered.
- **`check_image_tag_policy()` with a build path:**
  - rendered rows are judged, and `allow` matches `<reference>/<container>`;
  - a rendered image a declared row also has is judged once, as delivered;
  - a rendered module is not unknown;
  - `origins` without `chart` skips them;
  - `require_pin` does not apply to a chart row.
- **`build run`:**
  - a `deny` on a rendered image fails the build with the SBOM written and the result recorded;
  - a `deny` on a declared image still fails it;
  - `--dry-run` judges declared images;
  - the policy result is written once.
- **`deploy run`:** a `deny` on a rendered image in the build's SBOM stops the deploy before any step.
- **`policy check`:** reads rendered images from `--build-path`.
- **Registry chart:** renders from a cached archive whose digest matches; skips with `info` on a missing archive
  and on another digest. One test against real `helm` with a packaged chart.
- **Declared but not rendered:** the `info` finding and the reason; no finding when the chart was not rendered.

## Related Decisions

- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `ModuleServiceModel.artifact` and its Compose
  and Helm rendering; its Helm claim holds only for `image_ref` resolved into the service, not for the chart
- [ADR-0019](../decisions/0019-version-pinning.md) - `images` and `charts` pins
- [image-tag-policy.md](../design/image-tag-policy.md) - the policy that exposed this
- [effective-images.md](effective-images.md) - the resolver and its rows
- [sbom-generation.md](../design/sbom-generation.md) - the image and Helm chart collectors

## Remaining Work / Open Questions

### Implementation plan

- [x] 1. `EffectiveImage.delivered` and `.delivery`, set by `effective_images()` (design section 1); the module
  walk shared with step 2
- [x] 2. `UnknownImageModule` and `unknown_image_modules()` (section 2)
- [x] 3. `ImageTagPolicyModel.on_unknown_image`; `evaluate_image_tag_policy(..., unknown=...)` with the
  `image_unknown_to_strata` finding and the declared-only wording; `check_image_tag_policy()` fetches the modules
  (section 4)
- [x] 4. `deploy images`: the DELIVERED column, the "Known only to Helm" list, the JSON fields, the info finding
  without a policy (section 3)
- [x] 5. Docs: the policy section of the workspace reference, `module.md` (what a Helm service `image` means),
  `deploy images` help, `image-tag-policy.md`'s out-of-scope note, `.github/CHANGELOG.md`. The schema is
  generated from the model, so `on_unknown_image` needs no file. The `version-lifecycle.md` row below was already
  correct; the ADR rows stay as they are (ADRs are immutable)
- [x] 6. Checks: ruff, mypy, lint-imports, `strata validate config`, the full suite
- [ ] 7. Optional, on demand: option C, then option D
- [x] 8. The SBOM side: the declared-only marker and rendering local charts (design "The SBOM side" above). Its
  part 1 is this doc's open question on a shared marker; its part 2 is option D for local charts only (offline).
  - [x] 8a. Marker: `ImageCollector` properties, `_merge_by_purl()` (drop the marker for a rendered image, join
    `used-by`), `ImageRef.delivered` and the `(declared only)` gate label, `ManifestImageModel.delivered`
  - [x] 8b. `rendered_images()` (pure) and `HelmIntegration.template()`
  - [x] 8c. `WorkspaceSpecModel.sbom.render_charts`; the `chart-image` collector and its findings
  - [x] 8d. Docs (the SBOM design and how-to, `workspace.md`; `security-gates.md` names no label), CHANGELOG;
    checks; the full suite; a real render of a local chart (a test against real `helm`)
- [ ] 9. Rendered chart images as a build fact (design "Rendered chart images as a build fact" above). Each step
  ends green on ruff, mypy, lint-imports and its own tests.
  - [x] 9a. The SBOM keeps the reference and the container: `strata:image` on a rendered component, and the
    use `chart:<namespace>/<reference>/<container>` (`chart_image_collector.py`, `IMAGE_REF_PROPERTY` in
    `sbom_utils.py`). Nothing reads them yet. Tests: the reference keeps the tag the purl drops; two containers of
    one image keep both uses after the merge. Built 2026-11-12.
  - [x] 9b. Registry charts: `write_sbom(..., helm_cache=)` from `build run`; the chart-image collector finds the
    archive with `cached_pin()`, checks `chartDigest`, renders the `.tgz`; the skip cases as `info`. Unit tests
    with a stubbed `HelmIntegration`, one with real `helm` and a packaged chart (section 4). Built 2026-11-12.
  - [x] 9c. Declared but not rendered: the `sbom_declared_image_not_rendered` finding in `write_sbom()` after the
    merge, and the reason `helm module: not in the rendered chart` (section 5). Built 2026-11-12.
  - [x] 9d. Policy rows: the `chart` origin in `ImageTagPolicyModel.origins`; `rendered_chart_images()`;
    `check_image_tag_policy(..., build_path=)` adds the rows, judges a shared image once as delivered, drops
    rendered modules from `unknown`; `_fix()` and `_problems()` for a chart row (no `require_pin`). Pure tests on
    `evaluate_image_tag_policy()` plus a test over a written `sbom.json` (section 2). Built 2026-11-12. The user
    docs for `origins: chart` and the `allow` name wait for 9g, when a caller passes the build.
  - [x] 9e. Move the build check: `build run` judges the policy after the SBOM, next to the CVE gate, with its build
    path; a dry run judges declared images at the same point; the comment rewritten. Build tests for both `deny`
    cases, the dry run and the single result (section 1). Built 2026-11-12.
  - [x] 9f. `deploy run` and `policy check` pass their build path; `policy check` says where the rendered images
    came from. Tests for both (section 3). Built 2026-11-12, with the "Where it runs" tables in
    `image-tag-policy.md` and `workspace.md` (and the History bullet), and `denied-build-audit.md`'s current state:
    its option C now holds.
  - [ ] 9g. Docs: the `chart` origin and
    the `allow` name in `workspace.md`, the SBOM design and how-to (registry charts, the new finding), the CI
    how-to's "fail fast with `deploy images`" line, `.github/CHANGELOG.md` (Changed: the build check moves; Added:
    the rest). Then `strata validate config`, the full suite, and the dogfood build again: `whoami` loses its
    unknown warning and a `latest` sidecar fails a `deny` policy.
- [ ] 10. Later, after a release with item 9: `render_charts` on by default (open question below).

### Open questions

- ~~**Judge declared-only Helm images?**~~ Answered 2026-11-11: judged, and the finding says "declared only, the
  chart decides which image runs" (design section 4).
- **GitOps and script modules.** Their service images are marked not delivered. But a module of that type that
  declares no images is not reported as unknown, because it may run no containers. Should `argocd` and `flux`
  count, since they deploy Kubernetes manifests? Wait for a consumer.
- ~~**Does the SBOM and the CVE and image gates need the same marker?**~~ Answered 2026-11-11: yes,
  `strata:image-delivered: false`, read by the image gate and the audit manifest (design "The SBOM side").
- ~~**Registry charts in the SBOM.**~~ Answered 2026-11-12: rendered from the cached archive once its digest
  matches `chartDigest` (plan item 9, section 4).
- ~~**Declared but not rendered.**~~ Answered 2026-11-12: an `info` finding, `sbom_declared_image_not_rendered`
  (plan item 9, section 5).
- **`render_charts` by default** in a later minor release? Not before a release with item 9, so that the policy,
  the SBOM and the gates all see rendered images when it changes.
- **A deploy `--pin` that differs from the build's.** For a Helm module the pin is baked into
  `strata-values.yaml` at build, so the deploy judges the build's rendered images, not what its own pin says.
  Should the deploy warn when a Helm module's declared tag differs between its pin and the build? Or should a
  Helm module's pin be accepted at build only?
- ~~**`on_unknown_image` default.**~~ Answered 2026-11-11: `warn`, as `image_gate_policy`. `block` is opt-in.
- **Chart-only modules.** Is there a real consumer with a chart-only module whose image tag matters? Without one,
  B is enough.
- ~~**A Helm service `artifact`.** It resolves into the service `image`, so it is declared only for Helm too. Should
  it be an error without `image_values` once C exists?~~ Answered 2026-11-10: delivered with an `${artifact:}` token
  in `configuration`; without one, a warning (`artifact_not_delivered`), not an error, since the artifact is still a
  useful declaration.

### Stale statements to correct

- [version-lifecycle.md](version-lifecycle.md): the table row for `images` says the call site is
  `compose.py`/`helm.py` service rendering. It is `compose.py` only.
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) and
  [version-pin-overlay.md](../design/version-pin-overlay.md) say the Compose/Helm image-pinning path needed no
  change. True for the overlay, but for Helm the overlaid value never reaches the chart. ADRs are immutable, so
  the correction lives here.

### Risks

- Option C makes a service `image` mean "also write it into the chart". A module that sets the same image through
  `configuration` today would then conflict; hence the error rather than a silent override.
- A strict `on_unknown_image: block` default would fail every existing chart module that declares no images.
  Hence `warn`.

## Changelog

- 2026-11-12: plan item 9 designed and planned: `image_tag_policy` moves to the end of the build and judges
  rendered images (the `chart` origin), at build, deploy and `policy check`; registry charts are rendered from the
  cache; a declared image the chart does not run gets an `info` finding. Two open questions answered, one added.
  `render_charts` by default is item 10. 9a built the same day: designing it showed the merge loses the container
  of a second use, so the `chart:` use now names the container too. 9b built the same day: registry charts are
  rendered from the cached archive once its digest matches. 9c built the same day: the
  `sbom_declared_image_not_rendered` finding. 9d built the same day: the `chart` origin and the rendered rows;
  no caller passes a build yet. 9e built the same day: `build run` judges the policy after the SBOM; the
  dogfood build found that `helm template` got relative paths while running in the chart's folder, so a
  relative `--build-path` rendered no chart (fixed in `HelmIntegration.template()`). 9f built the same day.

- 2026-11-11: plan item 8 designed from the removed work doc `sbom-generation.md` (2026-11-07). The values-file
  finding there is resolved (`strata-values.yaml`). Rendering local charts is opt-in (`spec.sbom.render_charts`);
  registry charts are left for later. Built the same day (8a-8d).
- 2026-11-11: option B designed: `EffectiveImage.delivered`, `unknown_image_modules()`, the `deploy images` column
  and list, `image_tag_policy.on_unknown_image`. Two open questions answered; the plan rewritten; the old plan
  items 5 and 6 are now 7 and 8. Built the same day (plan steps 1-6).
- 2026-11-10: option C is met for artifacts by the `${artifact:NAME.FIELD}` token, with the `artifact_not_delivered`
  warning; the open question on a Helm service `artifact` answered. A literal Helm service `image` is unchanged.
- 2026-11-07: The SBOM's Helm gap ([sbom-generation.md](sbom-generation.md)) is deferred to be done together with
  this doc's work; added as plan item 6.
- 2026-11-02: created from a review of [image-tag-policy.md](../design/image-tag-policy.md). Verified in the v2 and
  v1 source and in a rendered `config/` build that a Helm service image is never written into the chart values.
