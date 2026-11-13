# Judge the images a Helm chart renders; a Helm module's declared image is a declaration

- Status: accepted
- Date: 2026-11-12
- Related: [docs/design/helm-image-delivery.md](../design/helm-image-delivery.md) (the design),
  [ADR-0026](0026-pinnable-artifact-references.md) (a module service's `artifact`),
  [ADR-0019](0019-version-pinning.md) (`images` and `charts` pins),
  [ADR-0058](0058-artifact-token-for-pinned-image-fields.md) (the `${artifact:}` token that delivers an artifact to a
  chart's values)

## Context and Problem Statement

A module service can declare an `image` or an `artifact`. For a Compose module strata writes that image into the
compose file, so it is what runs. For a Helm module it does not: the values strata writes for a chart hold `env`,
`persistence` and `configuration`, and no image. The chart decides which image runs. v1 behaved the same way.

Yet every image check read the declared service image as what runs: `strata deploy images`, the SBOM, the CVE and
image gates, `kind: version` `images` pins and `image_tag_policy`. For a Helm module a clean result could be false
assurance. An `images` pin on a Helm service changed nothing that deploys. A chart that declares no services, or
runs sidecars, init containers or subcharts beyond what it declares, was invisible to all of them.

Strata cannot read the image out of `configuration` either: charts put it under `image.repository`/`image.tag`,
`image: "repo:tag"`, `controllers.main.containers.main.image`, per-component keys, or nowhere (the chart's
`appVersion`). Any such rule is a heuristic per chart.

## Considered Options

- **A. Say it.** Document that a Helm module's declared image is metadata and mark it in `deploy images`. Honest,
  but fixes nothing.
- **B. Know what strata does not know.** A `delivered` fact on every image row, a list of Helm modules that declare
  no image, and `image_tag_policy.on_unknown_image: skip | warn | block`. Makes the gap visible and lets a team fail
  on it, but still cannot see what a chart runs.
- **C. Deliver the image to the chart.** A service names the path in the chart's values (`image_values`) and
  strata writes the image there, so the declaration, the pin and the artifact become what runs. Needs a per-service
  mapping, two forms for one-string and split charts, and a conflict rule against `configuration`.
- **D. Render the chart.** Run `helm template` at build and read the real images from the manifests. Exact for every
  chart, chart-only modules included, but needs Helm and the chart on the build agent.

## Decision Outcome

Chosen: **B and D together, with C met only through the `${artifact:}` token**, because only a render tells strata
what a chart runs, and a declaration it does not deliver must say so rather than pass for the truth.

- A Helm module's declared service `image`, and its `artifact` without a token, is a **declaration**: listed and
  judged, but marked not delivered everywhere (`deploy images`, the SBOM, the audit manifest, the image gate and
  `image_tag_policy`).
- An artifact is **delivered** to a Helm chart only by an `${artifact:}` token in the chart's own values, the
  module's or a service's `configuration`. A token in a service's `environment` is a use, not delivery. One
  function decides this for both `effective_images()` and `strata validate`'s `artifact_not_delivered`.
- The build **renders every Helm chart** (local and pinned registry charts, offline, on by default with
  `sbom.render_charts`) and lists the images it runs in `sbom.json`. Those rendered images are what strata judges
  for a chart: the CVE and image gates read them from the SBOM, and `image_tag_policy` reads them back from the
  build's `sbom.json` as rows with the `chart` origin at `build run`, `deploy run` and `policy check`.
- A Helm module that declares no image and whose chart was not rendered is **unknown**, and `on_unknown_image`
  decides what that costs (default `warn`).
- No `image_values` mapping. A literal Helm service image reaches a chart only through `configuration`, in the
  chart's own keys.

### Consequences

- Good: the gates and the tag policy judge what a chart actually runs, sidecars and init containers included,
  without any per-chart rule.
- Good: no check claims more than strata knows; a declared-only image says so in every finding.
- Good: no new module schema; the token already exists and works for Terraform, scripts and Compose too.
- Bad: the build needs `helm` and the chart archives to render; a chart that cannot be rendered is only an `info`
  finding, and its module stays unknown.
- Bad: `image_tag_policy` moves to the end of `build run`, so a bad tag is reported after the render, not before.
  `deploy images` and `policy check` remain the fast offline check.
- Bad: rendered images can fail a `deny` gate that passed before, on images nobody had scanned.
- Neutral: an `images` pin on a Helm service still moves only the declaration; a chart is pinned by its version.
