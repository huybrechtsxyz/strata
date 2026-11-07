# Helm module images - Work

- Status: draft
- Last updated: 2026-11-02

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
| Artifact (`store: artifact`, or a service `artifact`)                                         | Yes for Terraform variables and Compose. A Helm service `artifact` is declared only, as below. | Yes              |
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

## Related Decisions

- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) - `ModuleServiceModel.artifact` and its Compose
  and Helm rendering; its Helm claim holds only for `image_ref` resolved into the service, not for the chart
- [ADR-0019](../decisions/0019-version-pinning.md) - `images` and `charts` pins
- [image-tag-policy.md](../design/image-tag-policy.md) - the policy that exposed this
- [effective-images.md](effective-images.md) - the resolver and its rows
- [sbom-generation.md](../design/sbom-generation.md) - the image and Helm chart collectors

## Remaining Work / Open Questions

### Implementation plan

- [ ] 1. Add `delivered` (and the reason) to `EffectiveImage`; true for Compose services and for artifacts that
  reach a Terraform variable, false for a Helm module's service images
- [ ] 2. `deploy images`: a DELIVERED column and `data.images[].delivered`; list enabled chart modules that
  declare no image
- [ ] 3. `image_tag_policy`: `on_unknown_image`, the `image_unknown_to_strata` finding, and a decision on judging
  declared-only Helm images (see below)
- [ ] 4. Docs: the policy section of the workspace reference, `module.md` (what a Helm service `image` means), and
  the stale rows listed below
- [ ] 5. Optional, on demand: option C, then option D

### Open questions

- **Judge declared-only Helm images?** Judging them can give false assurance; skipping them drops what the author
  wrote on purpose. Suggested: judge them and word the finding "declared as X; the chart may run another".
- **Does the SBOM and the CVE and image gates need the same marker?** They scan Helm-declared images as if they
  ran. A `strata:image-delivered=false` property on the SBOM component would let a gate report "scanned a declared
  image". Taken up in [sbom-generation.md](sbom-generation.md) (step 1), together with rendering local charts.
- **`on_unknown_image` default.** `warn` (as `image_gate_policy`) is the suggestion. `block` makes every chart
  module declare its images or a pin, which is strict.
- **Chart-only modules.** Is there a real consumer with a chart-only module whose image tag matters? Without one,
  B is enough.
- **A Helm service `artifact`.** It resolves into the service `image`, so it is declared only for Helm too. Should
  it be an error without `image_values` once C exists?

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

- 2026-11-02: created from a review of [image-tag-policy.md](../design/image-tag-policy.md). Verified in the v2 and
  v1 source and in a rendered `config/` build that a Helm service image is never written into the chart values.
