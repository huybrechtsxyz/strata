# SBOM generation: images inside Helm charts - Work

- Status: deferred - to be done together with the other Helm image gaps in
  [helm-image-delivery.md](helm-image-delivery.md)
- Last updated: 2026-11-07

## Overview

The SBOM ([sbom-generation.md](../design/sbom-generation.md)) lists a Helm chart as one component
(`pkg:helm/authentik@2026.5.2`) and lists as container images only what a module declares under `services`. The
images the chart actually deploys (the app, sidecars, init containers, subcharts such as `postgresql`) are not in
`sbom.json` unless someone also declared them by hand. Everything that reads the SBOM inherits the gap:

- `cve_policy` scans `sbom.json`, so it never scans those images.
- `image_gate_policy` takes its image list from `sbom.json` (`images_from_sbom()`), so it never asks about them.
- `strata:used-by` and `strata:pinned-by` cannot describe what is not listed.

And the images that are listed for a Helm module are not necessarily what runs: strata never writes a service's
`image` into a chart's values ([helm-image-delivery.md](helm-image-delivery.md)), so a declared Helm service image
is a statement about the module, not what the chart deploys.

This doc covers what the SBOM records for Helm modules. What strata should deliver into a chart, and what
`image_tag_policy` and `deploy images` say about undelivered images, stay in
[helm-image-delivery.md](helm-image-delivery.md); the two share one marker (below). When this work is finished it
graduates into sbom-generation.md, which keeps describing the current state until then.

## Current state

Verified against the source on 2026-11-07.

| Module source                                          | What is in the build path                                                                                                                                                                                             | In the SBOM today                                                                                                     |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Chart from a remote or a repository (`chart_name` set) | Only `values.yaml` and `meta.yaml` in `build/<namespace>/<reference>/`. `sync_module_source()` does not copy a chart-based source; the deployer pulls the chart at `deploy run` (`HelmIntegration._resolve_chart()`). | The chart (`HelmCollector`, declared form); the module's declared service images, as if they ran                      |
| Local chart (`source_path`)                            | The chart directory itself, copied by `sync_module_source()`, plus `meta.yaml`, plus strata's `values.yaml` (see the finding below)                                                                                   | The chart and its `Chart.yaml` dependencies (`HelmCollector`, file form); the declared service images, as if they ran |

`deploy run` installs a local chart from that same directory (`chart = str(item.source_path)` when `meta.yaml` has
no `chartName`), with the token-resolved values as an extra `-f` overlay. So rendering that directory at build time
shows what the deploy will install, apart from values whose `${var:}`/`${secret:}` tokens are only resolved at
deploy.

### Finding: strata's values.yaml replaces a local chart's own

`HelmIntegration.prepare_namespace()` writes `_render_values(module)` to `<module dir>/values.yaml` whenever the
module has `services` or `configuration`. For a local chart that path is the chart's own default `values.yaml`,
copied in a moment earlier, so the chart's defaults (an `image.repository`, a `replicaCount`) are replaced by
strata's partial document, both for rendering here and for the real `helm upgrade`. `_render_values()`'s docstring
says it only skips writing an empty document, "matches v1". Whether that is intended (v1 did it too) or a defect
that drops chart defaults needs its own decision; it is not part of this work, but it means "render the local chart"
renders what deploy installs, defaults lost included. Logged as an open question.

## Design

Three steps, each useful alone. Step 1 is cheap and honest; step 2 adds the real images for charts strata already
has; step 3 (remote charts) is opt-in and separate.

### Step 1 - mark declared-only images

An image that only a Helm module's `services` declare gets `strata:image-delivered: false`: strata did not write it
into what runs. This is the marker [helm-image-delivery.md](helm-image-delivery.md) proposes for the SBOM ("Does the
SBOM and the CVE and image gates need the same marker?"), so a gate or a reader can tell "scanned a declared image"
from "scanned what runs".

- `ImageCollector` knows the module of every service (`module.spec.type == "helm"` and no Compose module uses the
  same image). An image a Compose module or an artifact also uses is delivered there, so it is not marked.
- When step 2 finds the same image in the rendered chart, the image is what runs: the marker is dropped (rule in
  `_merge_by_purl()`, since the two collectors produce the same purl).
- Nothing else changes: the image stays in the SBOM, still scanned, still asked about by the image gate.

### Step 2 - render local charts and list their images

A new collector, `chart-image` (`integrations/sbom_collectors/chart_image_collector.py`, registered in `_KNOWN`):

- **Which charts.** Every module folder in the build path that holds a `meta.yaml` without `chartName` and a
  `Chart.yaml`: a local chart, exactly the case `deploy run` installs from the folder. The collector finds them by
  file, as the other file collectors do, so the `SbomCollector` interface is unchanged.
- **Render.** `helm template <releaseName> <folder> --namespace <namespace>` (both from `meta.yaml`), offline: no
  `--dependency-update`, no repository access. `run_command` from `utils.transport`, with a timeout.
- **Collect.** Every `image:` under a `containers`, `initContainers` or `ephemeralContainers` list, anywhere in each
  rendered document (`yaml.safe_load_all`). Walking the structure instead of matching kinds covers Deployment,
  StatefulSet, DaemonSet, Job, CronJob, Pod and CRDs that embed a pod spec (Argo Rollouts, operators).
- **Component.** `container`, purl from `image_to_purl()`, `hashes` from `image_digest_hash()`, tag stability as for
  any image, `strata:used-by: module:<reference>`, and `strata:found-in: chart`. Merged with the other collectors by
  purl, so an image both declared and rendered is one component.
- **Cannot render.** One `info` finding per chart, and the chart's images are simply not listed (the build never
  fails on this):
  - `helm` is not on `PATH`: `chart images not listed: helm is not installed`;
  - `helm template` fails, typically a dependency in `Chart.yaml` that is not vendored under `charts/`
    (`helm dependency build` needs network access): the first line of helm's error;
  - an image still holding a `${...}` token (a value resolved only at deploy): that image is left out, and the
    finding names it.

Why rendering and not reading `values.yaml`: charts disagree on where an image lives (`image.repository` +
`image.tag`, one `image:` string, `controllers.main.containers.main.image`, per-component keys, a tag defaulting to
`appVersion`); [helm-image-delivery.md](helm-image-delivery.md) already rejected interpreting `configuration` for
that reason, and the same holds for `values.yaml`. Rendering is exact for the chart strata has.

### Step 3 - remote charts (opt-in, later)

Superseded on 2026-11-07 by [ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md) and
[helm-registry-chart-checks.md](helm-registry-chart-checks.md): `build run` now pulls every registry chart into
`.strata/helm/<source>/<chart>/<version>/` and pins it, with no opt-in. The SBOM already records the pinned chart's
exact version, digest, `appVersion` and subcharts. What remains of this step is the images: step 2's renderer can
run on the unpacked chart in the cache. A chart the build could not pin (no `helm`, credentials that need `--resolve`,
`--offline`) has no cached copy, so its images reach the SBOM only when declared, and step 1 marks those as
declared-only.

## Rollout and risk

Step 2 changes what gates see. A workspace with `cve_policy` or `image_gate_policy` at `enforcement: deny` starts
scanning images it never scanned, and a vulnerable sidecar can fail a build that passed the day before. That is the
point, but it is the 2.3.2 lesson again: a default that can break pipelines on upgrade. Options:

| Rollout                                                                                             | Effect on upgrade                                                |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| A. On by default, in a minor release with a CHANGELOG warning                                       | Builds with a `deny` gate can start failing                      |
| B. Opt-in first (`build run --chart-images`, or a workspace setting), on by default one minor later | Nothing breaks; teams try it with `strata policy findings` first |

Proposed: B. Step 1 is safe on by default: it only adds a property.

## Related Decisions

- [sbom-generation.md](../design/sbom-generation.md) - the collectors and purl rules this extends
- [helm-image-delivery.md](helm-image-delivery.md) - what strata delivers into a chart; the shared
  `strata:image-delivered` marker
- [security-gates.md](../design/security-gates.md) - the image gate, which reads its images from `sbom.json`
- [ADR-0022](../decisions/0022-strata-build-run.md) - `build run` stays offline apart from fetching sources

## Remaining Work / Open Questions

### Implementation plan

- [ ] **Phase 1 - declared-only marker.** `ImageCollector` sets `strata:image-delivered: false` on images only
  Helm modules declare. Tests: Helm-only image marked; the same image also in a Compose module or an artifact not
  marked; Compose images never marked.
- [ ] **Phase 2 - renderer.** A pure function from rendered manifest text to `(image, document kind/name)` pairs,
  walking `containers`/`initContainers`/`ephemeralContainers`. Tests on fixed manifests: Deployment, CronJob,
  a CRD with a pod spec, multi-document output, an image with a token.
- [ ] **Phase 3 - collector.** `chart-image` collector: find local chart folders, run `helm template`, build
  components; the `info` findings; drop the Phase 1 marker on merge when the chart renders the image. Tests with a
  stubbed `run_command`; one end-to-end test behind `shutil.which("helm")` (skipped without Helm).
- [ ] **Phase 4 - rollout switch** (per the open question), docs (sbom-generation.md, the how-to,
  helm-image-delivery.md's SBOM question), CHANGELOG, and dogfood on `config/` with a local chart.

### Open questions

1. **Rollout:** opt-in first (B, proposed) or on by default (A)? And if opt-in, a `build run` flag or a workspace
   setting?
2. **The values.yaml finding:** is replacing a local chart's own `values.yaml` intended? If it is a defect, fixing it
   (write strata's values next to the chart and pass both with `-f`) changes what deploy installs, so it needs its
   own change and its own release note.
3. **Declared images a chart does not render:** when step 2 renders a chart and a declared Helm service image is not
   among its images, should the SBOM drop the declared one, keep it marked, or add a `strata:declared-not-rendered`
   finding? Proposed: keep it marked and add an `info` finding, since the declaration may be deliberate
   documentation.
4. **`helm` version:** require a minimum (`helm template` output is stable across Helm 3), or accept any `helm` 3?

## Changelog

- 2026-11-07: Deferred. The SBOM's Helm gap shares its cause (strata does not know what a chart runs) and its
  marker with [helm-image-delivery.md](helm-image-delivery.md), so both are tackled in one piece of Helm work
  rather than the SBOM part on its own.
- 2026-11-07: Created. Verified that a remote chart is not in the build path, that a local chart is installed from
  the build path folder, and that strata's `values.yaml` replaces a local chart's own. Proposes a declared-only
  marker shared with helm-image-delivery.md, rendering local charts with `helm template`, and remote charts as a
  later opt-in.
