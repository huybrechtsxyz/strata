# strata - Changelog

Concise, user-facing summary of each release. Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/); the current shape of each feature lives in [docs/design/](../docs/design/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **Parameter sets: one deployment base for many spokes.** A new kind, `parameterset`, holds one instance's plain
  values. A deployment names sets per role in `meta.params` (`params: { hub: hub-hub01, spoke: spoke-acme-001 }`),
  and any document of its `extends` chain reads them with `${param:<role>.<key>}`, in any field of its spec,
  reference fields included (`tenant`, `environments`). Values are filled in when the solution loads, and each
  deployment is then validated as if written out in full (ADR-0061).
  - Sets named along the chain merge per role, base first: a base's sets are defaults, the leaf's override them.
  - `meta.name_rule` (`spoke-${param:spoke.name}`) checks each deployment's name against its values
    (`name_rule_mismatch`).
  - A problem is reported on the field you wrote, with the values that made it and the deployment it was resolved
    for (`param_missing`, `param_not_scalar`, `unknown_reference`). A value nothing reads is information
    (`param_unused`), so `validate --strict` still passes.
  - Deployments only for now; environments and tenants follow.
  - The exported deployment schema (`.strata/schemas/deployment.json`, from `strata sln init`/`update`) accepts a
    `${param:}` token in every spec field but `extends` and `partial`, so the YAML editor no longer underlines
    `tenant: ${param:spoke.tenant}`. Run `strata sln update` to refresh the schemas.
  - `docs/how-to/parameterised-deployment-bases.md` walks through a full example.

- **`extends`, `partial` and parameter sets on environments and tenants.** An environment or tenant can build on a
  `partial: true` base with `spec.extends`, name parameter sets in `meta.params`, read them with `${param:}`, and
  require a name with `meta.name_rule`, as a deployment can. Both are merged when the solution loads, so every reader
  (value assembly, `strata values`, tenant defaults, `${value:}`, attributeconfig) sees the merged document.
  - Environments merge `properties` and `custom` per leaf key; a `variables`, `secrets` or `features` entry with the
    same `key` replaces the base's whole entry. Tenants replace `geographies` (never join them), append
    `environments` after the base's, and merge `properties`, `custom` and `configuration` per leaf key.
  - A base is never used: listing a partial environment is `environment_not_listable`, naming a partial tenant is
    `tenant_not_selectable`, and reading either with `${value:}` is `value_reference_to_base`.
  - A tenant's `display_name` is now required only when the tenant is neither `partial` nor built with `extends`.
  - The exported environment and tenant schemas accept `${param:}` tokens; run `strata sln update` to refresh them.
  - `docs/how-to/environment-and-tenant-bases.md` walks through a full example.

- **attributeconfig checks parameter sets.** `kind: parameterset, block: values, view: document` checks the shape of
  each set; `block: params, view: effective` checks the values a deployment, environment or tenant receives once its
  sets are merged, with one top-level key per role (`required: [spoke, hub]`). A selector path now walks into a map,
  so `meta.labels.role: spoke` selects sets by label.

- **`strata render KIND NAME`: where each value comes from.** Shows a deployment, environment or tenant as strata
  uses it (its `extends` chain merged, its parameter sets filled in, a deployment's tenant defaults folded in), one
  line per field, with the document that wrote each value and, for a value filled in from a parameter set, the text
  as written and the role, key and parameter set it read. `-o json` gives the same as data. Secret values are always
  masked. Runs nothing and needs no valid solution, like `deploy list`.

- **The strata agent skill covers parameter sets**: the `${param:}` token, bases for environments and tenants,
  `meta.name_rule`, the attributeconfig views and the pitfalls. Run `strata sln update` to refresh
  `.claude/skills/strata/SKILL.md`.

- **A full container image, `ghcr.io/huybrechtsxyz/strata:<version>-full`** (and `latest-full`): strata with
  OpenTofu, Helm 4, the Docker CLI with Compose, Trivy and Checkov, for a pipeline that runs Helm or Compose steps or the CVE and
  Checkov policies. The image you pull today is the slim variant: `strata:<version>` and `latest` are unchanged, and
  are also tagged `-slim` and `latest-slim`. Neither variant has Terraform, whose licence is not settled for a public
  image; extend either one with your own (`docs/design/cli-image.md`).
- **OpenTofu: `tool: opentofu`** runs a root module with `tofu` (OpenTofu 1.6 or later), with everything a
  `tool: terraform` step has: the `backend`, the `strata` payload and `TF_VAR_` values, saved plans, destroy,
  outputs, the lock policy, the SBOM and the Checkov policy. Bind a `type: opentofu` integration to pin its version.
  - The lock policy and the SBOM read a short provider source as `registry.opentofu.org` when `tofu init` locked it
    so, and the lock policy says `tofu init`.
  - `strata validate` errors when a Terraform or OpenTofu provisioner binds an integration of the other type
    (`provisioner_integration_type_mismatch`), or when a `terraform` integration's `spec.command` runs `tofu` or
    the reverse (`integration_command_other_tool`). Running `tofu` through `type: terraform` worked by accident,
    with Terraform's version check and registry; switch it to `tool: opentofu` and `type: opentofu`.
  - `docs/config/workspace.md` has the migration steps from Terraform.
- **`strata deploy steps DEPLOYMENT`** lists the execution steps `deploy run` would run, in its order, for a
  pipeline to plan its jobs. It runs nothing and needs no build or credentials.
  - Each step has its `scope`, `order`, `level` (steps on one level do not depend on each other), `depends_on`,
    the tool it needs, `capabilities` (`saved_plan`: `deploy apply` can take it; `destroy`; `container`), its
    declared and effective targets (topologies expanded) and its stage `timeouts`.
  - `data.scopes[]` gives each scope the scopes it waits for; scopes that depend on each other are marked
    `orderable: false`, with a `scope_cycle` warning.
  - `--stage` and `--scope` select as on `deploy run`. An empty selection exits 0; values that matched no step
    are in `data.unmatched`.
  - `--destroy` lists teardown order. `--check-tools` checks each listed step's tool on this machine (on `PATH`,
    required version) and exits 3 when one is not ready.

- **The SBOM, the image gate and the audit manifest say which images strata does not deliver.** A Helm module's
  declared service image is still listed and scanned, but the chart decides which image runs.
  - `sbom.json` gives it `strata:image-delivered: false` and `strata:image-delivery: helm module: the chart
    decides`.
  - `image_gate_policy` names it `... (declared only)`.
  - The deploy audit manifest records it with `delivered: false`.
  - `strata:used-by` now lists every use of an image found by more than one collector, not only the first
    collector's.
- **`spec.sbom.render_charts`** (workspace, default `true`, see "Changed"). `build run` renders every Helm chart with
  `helm template`, offline and with strata's values, and lists every image it deploys in `sbom.json`, sidecars and
  init containers included (`strata:found-in: rendered-chart`). Each records the container in its use
  (`strata:used-by: chart:<namespace>/<reference>/<container>`) and the reference as the chart writes it
  (`strata:image`), which keeps a tag the purl drops for a digest-pinned image.
  - A registry chart is rendered from the archive the build pinned in `.strata/helm/`, only while its digest
    still matches.
  - A declared Helm image the module's rendered chart does not run gets an `info` line
    (`sbom_declared_image_not_rendered`) and the reason `helm module: not in the rendered chart`.
  - A chart that cannot be rendered gets an `info` line (`sbom_chart_not_rendered`): no `helm`, a dependency
    not vendored under `charts/`, or a registry chart that is not pinned or whose cached archive changed.
  - Those images reach `cve_policy`, `image_gate_policy` and `image_tag_policy`, so a `deny` gate can fail on an
    image it never saw before.
- **`image_tag_policy` judges the images rendered charts run**, read from the build's
  `sbom.json` by `build run`, `deploy run` and `policy check`. `strata deploy images` reads no build and is
  unchanged.
  - A new origin, `chart`. A workspace that lists `origins` gets these images only once it adds `chart`.
  - Each is named `REFERENCE/CONTAINER` (`whoami/shipper`), the name an `allow` entry uses.
  - `require_pin` does not apply to them, and the fix points at the chart's values.
  - A declared Helm image the chart also runs is judged once, as delivered. A Helm module whose chart rendered an
    image is no longer reported as `image_unknown_to_strata`.
  - An info line (`image_tag_policy_rendered_images`) names the `sbom.json` and when it was written, so
    `policy check` and `deploy run` say which build they judged. An `sbom.json` that cannot be read is a warning
    (`image_tag_policy_sbom_unreadable`), and the declared images are still judged.
- **`strata deploy images` says which images strata delivers.** Strata delivers an image when it writes it into
  what runs:
  - a Compose service;
  - an artifact a step receives through a variable, or one an `${artifact:}` token delivers. In a Helm module only
    a token in the chart's own keys (the module's or a service's `configuration`) delivers it; a token in a
    service's `environment` sets an environment variable, so it is a use, not delivery
    (`no (helm module: no ${artifact:} token in the chart's values)`), as `artifact_not_delivered` judges it.

  A Helm module's service image is only declared, because the chart decides which image runs.
  - The console gets a `DELIVERED` column whenever an image is not delivered, for example
    `no (helm module: the chart decides)`.
  - JSON gets `data.images[].delivered` and `.delivery`.
  - A Helm module that declares no service `image` or `artifact` is listed under "Known only to Helm"
    (`data.unknown_image_modules[]`), with its chart and version.
- **`image_tag_policy.on_unknown_image`** (`skip` | `warn` | `block`, default `warn`), for a Helm module whose
  images strata does not know (`image_unknown_to_strata`). It is independent of `enforcement`. `allow` does not
  exempt a module; declare its images to have them judged.
  - A workspace with an `image_tag_policy` and such a module now gets a warning on `build run` and `deploy run`.
  - The module is reported when `origins` covers `module-service` or `chart` (or is omitted): a chart-only policy
    hears about a chart whose images it could not judge.
  - A declared Helm image is still judged, and its finding says "declared only, the chart decides which image
    runs".
- `${artifact:NAME.FIELD}`, a token for an artifact's `image_name`, `image_tag` or `image_ref`, with the
  deployment's version pin applied. It works in every field a `${var:}` token resolves in at deploy (a Terraform
  payload or backend, Helm values, a Compose file, a script's arguments), so an artifact no longer needs a
  `store: artifact` variable declared only to pass one field along. `${artifact:NAME.image_tag}` on an artifact with
  no tag and no pin is an error (`unavailable_artifact_token`), never an empty tag; an unknown artifact or field is
  `unknown_artifact_token`. `strata validate` judges both per deployment. A `store: artifact` variable still gives
  Terraform the value as a variable. An artifact used through the token is an image the deployment runs:
  `strata deploy images` lists it with `via: token:KIND/NAME`, and the SBOM, the image gates and `image_tag_policy`
  judge it.
- `strata validate` warns `artifact_not_delivered` for a Helm module service that names an `artifact` while no
  `${artifact:NAME.FIELD}` token for it appears in the module's or a service's `configuration`. Strata writes no
  image into a chart's values, so such an artifact (and its version pin) changes `deploy images` and the SBOM but not
  what runs. Deliver it in the chart's own keys, for example
  `configuration: {image: {repository: "${artifact:NAME.image_name}", tag: "${artifact:NAME.image_tag}"}}`.
- `strata validate` warns `artifact_registry_mismatch` when an artifact's `registry` names another registry than
  its `image_name` is pulled from (an `image_name` with no host is Docker Hub). `registry` is documentation: strata
  never adds it to the image reference, so the runtime pulls `image_name` as written. Put the host in `image_name`.
- `strata deploy images` reports an artifact's `integration`, by name: an `INTEGRATION` column when any image
  has one, and `data.images[].integration` in JSON, so a pipeline that copies the images knows which
  credentials each one needs. Nothing is resolved from it, so no credential appears.
- `strata validate` warns `artifact_unused` for an artifact no deployment uses: no `store: artifact` variable a
  step receives, no module service `artifact` and no `${artifact:}` token in a document a deployment reaches.
  `strata version update` no longer offers a pin for such an artifact, and says which it left out.
- An artifact's `image_tag` and an `artifacts` pin can be a tag, a digest or both: `1.4.2@sha256:<hex>` gives
  `name:1.4.2@sha256:<hex>`. Two more fields read the parts, `image_tag_name` and `image_digest`, as a
  `${artifact:}` token or a `store: artifact` variable's `field`. `strata deploy images` gains a `DIGEST` column and
  `data.images[].image_digest`. `image_tag_policy` gains `require_digest` (`image_without_digest`).

### Changed

- **`--stage` and `--scope` are repeatable** on `deploy setup`, `check`, `plan`, `apply`, `run` and `destroy`.
  A step is kept when it matches any value of an option, and both options when both are given
  (`--scope infra --scope network`). One value of each works as before.
- **`build run` renders Helm charts by default** (`sbom.render_charts` defaults to `true`). `sbom.json` lists
  the images every Helm chart runs, and `cve_policy`, `image_gate_policy` and `image_tag_policy` check them, so a
  pipeline with a Helm module and a `deny` gate can fail on a sidecar or init container nobody scanned before.
  Run `strata build run` and `strata policy check` before upgrading the pipeline, and fix or `allow` what they
  report. To keep the declared images only, set `sbom: {render_charts: false}` on the workspace. Without `helm`
  on the build agent each chart is an `info` line and is skipped.
- **`build run` judges `image_tag_policy` at the end**, after `sbom.json` is written, next to the other build
  policies, so it can judge the images rendered charts run. A `deny` (and `on_unknown_image: block`) now fails the
  build after the sources are cloned and the charts rendered, with the output on disk. With `--clean`, the previous
  build is gone by then. A denied build now records its result in `policy_results.json`. To fail fast, run
  `strata deploy images` or `strata policy check` before `build run`. `--dry-run` still judges the declared images.
- An artifact's `image_tag` must be a tag, a digest or `tag@digest`; another shape fails when the document loads.
  An `artifacts` pin of another shape is an error in `strata validate` (`artifact_tag_invalid`), and
  `strata version set` refuses to write one. Before this, `1.4.2@sha256:...` gave `name@1.4.2@sha256:...`, which
  no runtime pulls.
- An `images` pin must be a whole image reference with a tag, a digest or both (`postgres:16-alpine`); another
  value is an error in `strata validate` (`image_pin_invalid`), and `strata version set` refuses to write one. A pin
  replaces the service's whole image, so `db: 16-alpine` ran the image `16-alpine` from Docker Hub, and
  `db: postgres` floated on `latest`. Two warnings join it: `image_pin_renamed` when the pin names another image
  than the service runs (`postgres` and `docker.io/library/postgres` are the same image), and `pin_not_applicable`
  when the service declares no `image`, so the pin changed nothing. `--available` is not checked.
- In `strata deploy images -o json`, `data.images[].image_tag` is now only the tag part. A digest is in
  `image_digest`, so a digest-only image has `image_tag: null` where it had the digest. The same split
  applies to a module service's literal `name:tag@sha256:...`. An image with a digest is never floating in the SBOM,
  and `image_tag_policy`'s `reject_tags` and `tag_pattern` judge the tag part of `tag@digest`.

- An artifact reached only through a `store: artifact` variable is listed by `deploy images`, and judged by the
  SBOM, the image gates and `image_tag_policy`, only when the deployment can receive that variable: a `${var:KEY}`
  token in a document it reaches, or an execution step that gets every variable (Terraform, a script without
  `script.values`, an `output.template` that reads it, or a tool strata does not know). A variable declared for
  a Helm- or Compose-only workspace that nothing reads no longer makes the policy fail on an image that never runs.
- `strata validate` rejects `${value:artifact.NAME.spec.image_name}`, as it already rejected `spec.image_tag`
  (`value_reference_pinnable_field`). Such an image was never pinned and never listed by `deploy images`, the SBOM
  or `image_tag_policy`. Replace it with `${artifact:NAME.image_name}`, which the message names. The artifact's
  free-form fields (`spec.configuration.*`, `spec.properties.*`, `spec.registry`, ...) stay readable through
  `${value:}`.

### Fixed

- **`deploy run` refuses a version the Helm build was not made with** (`build_version_mismatch`). A Helm module's
  pins are applied by `build run`, so `deploy run --pin prd-1.5` on a build made with `prd-1.4` installed the
  charts at 1.4 while the run and its audit record named 1.5. The deploy now stops before any step, naming both
  versions and the rebuild command. Build and deploy with the same `--pin`. A destroy, a build without Helm modules
  and a build made before this release are not checked. `resolved.yaml` gains a `version` key.
- **The container image** (`ghcr.io/huybrechtsxyz/strata`) now has `git` and `ssh`, which strata calls itself:
  a `build run` with a remote no longer fails in it, and `ssh_key` auth works. git trusts a checkout mounted with
  `docker run -v`, so the audit record and the image gate get the commit SHA instead of silently none. The user has a
  home directory. The image installs the dependency versions in `uv.lock`, the ones CI tested, with a pinned `uv`.
  Provisioner tools stay out of it; see the new full variant under "Added".
- **An `sbom.json` that cannot be read no longer crashes a run.**
  - The image gate (`deploy run`, `policy findings`) follows `on_missing_data` when the file is locked or has no
    read permission, instead of stopping on a raw `OSError`.
  - The deploy audit record warns `audit_sbom_unreadable` instead of crashing after the deploy has run. A corrupt
    file is still referenced and hashed, without a component count; one that cannot be read is left out.
  - An SBOM with no components (CycloneDX then leaves out the `components` key) is an empty SBOM, not a broken
    one: the image gate says there are no images instead of reporting missing data.
- **A version pin is logged once, where it is applied** (`version pin applied`). `build run` logged each image and
  chart pin a second time while writing the SBOM, and `deploy run` again while writing the audit record; both only
  re-read what the build applied. `deploy run` logs an `${artifact:}` pin only for an artifact a token of that
  deployment names, and `strata validate` logs none.
- Schema descriptions (`strata schema`) and docstrings cite each doc where it is now. 139 citations of 13 docs
  pointed to a path the doc had moved from; a test now fails on any such citation.
- `strata promote status` marks an `images` pin `← behind` by its tag. An image pin is a whole reference
  (`acme/api:1.2.0`), which never parsed as a version, so no image pin was ever marked behind. Siblings that pin
  different images are not compared, and an `artifacts` pin of `tag@digest` compares its tag.
- A registry chart pull on Windows no longer fails with a bare `PermissionError` when another process (a virus
  scanner reading the new archive) briefly holds a file in its staging directory. Removing that directory is best
  effort, so the pull's own result stands, such as `helm_chart_digest_changed`. A failure to store the entry is
  reported as `helm_chart_pull_failed`. A later pull removes a `.pull-*` directory left in `.strata/helm/` once it
  is more than an hour old.

## [2.6.0] - 2026-11-09

Deployment documentation: `strata deploy output`, `strata deploy report` and `spec.documentation`, built on a
per-step output store ([docs/design/deployment-documentation.md](../docs/design/deployment-documentation.md)).

### Changed

- The output store keeps one key per step, `strata/step-outputs/<deployment>/<step>`, instead of one key for the
  whole deployment (ADR-0057). On `azure-appconfig`, which allows 10 KB per key, the limit now applies to one step
  instead of all steps together. Existing records keep working without a redeploy: a step without a key of its own
  is read from the old `strata/deployment-outputs/<deployment>` key, and while that key exists each deploy updates
  it too, so an older strata version reading it never gets an outdated value. A later release removes both.
- `deploy run` warns when a step's recorded outputs pass 8,000 bytes on a backend with a size limit
  (`output_store_record`), and when they pass the limit it names the step, its size and the limit instead of
  passing on the backend's own error. The step's deploy still succeeds, as before. No value is recorded then, and
  the step's earlier values are no longer read either: its key holds an "omitted" marker and it is removed from the
  old key, so a later step reading one of its outputs fails with that reason instead of getting a value from an
  earlier deploy. `deploy output` shows it as "too large for the output store".
- `strata values get --format raw|env|export` writes the run's header, findings and footer to stderr, so stdout
  holds only the values and `$(strata values get app KEY --format raw)` captures just the value. `--format table`
  and `--output json` are unchanged.
- The audit manifest records `commit_sha`, the commit that was deployed (`STRATA_COMMIT_SHA`, `GITHUB_SHA`,
  `BUILD_SOURCEVERSION`, else `git rev-parse HEAD`). The field existed but was never filled. `strata audit status`
  and `strata audit changes` include it in their JSON output.

### Added

- `strata deploy output DEPLOYMENT [STEP | STEP.KEY]`, the `terraform output` equivalent. It reads the outputs each
  step recorded in the workspace's `output_store`, so it needs no Terraform state or provider credentials, only
  access to the store. A sensitive output shows as `<sensitive>`: strata never stores its value, so `--format raw`
  on it fails (exit 2) and shows its description instead. `--format raw` prints one bare value on stdout, with the
  run's report on stderr, for `$(...)` in a script. With `-o json`, each output carries its description. Exit 2 for
  an unknown step or key, 3 when the workspace has no `output_store`. A report record from a different deploy than
  the values (`report: mismatch`) is not applied to them.
- `deploy run` also records a report record per step, `strata/deployment-report/<deployment>/<step>`: the names of
  its sensitive outputs and each output's `description`, read from the root module's `output` blocks (`*.tf` and
  `*.tf.json`). Never a value.
  Above the backend's size limit, descriptions are shortened, then left out (`report_store_record` warning). A failed
  write is a warning. Destroy marks it destroyed. `strata build run` sizes it from the rendered `.tf` files and warns
  early (`report_record_size`); when the output store cannot be resolved where the build runs, it skips the estimate
  with a warning (`report_estimate_skipped`) and the build goes on.
- `strata deploy report DEPLOYMENT [--step NAME]...`, the handover document: each step's outputs, the secrets each
  step used with where to find them, and the deployment's images. No secret value is ever read or shown: every secret
  and every sensitive output is `********`. A secret is listed by location: the vault URL and name for
  `azure-keyvault`, address, project, environment and name for `infisical`, the environment variable for
  `environment` and `github`. A `constant` secret is shown as held in source, with a warning
  (`secret_held_in_source`). Only secrets a step used are listed: `${secret:}` tokens in its input, the Terraform
  variables its root module declares (strata passes every value as `TF_VAR_<key>`, Terraform reads only declared
  ones), `script.values`, and the secrets an `output.template` reads. A script without `script.values`, or a
  template that reads `secrets` dynamically, counts as using every secret. `-o json` carries the same data.
- `strata deploy report --format markdown` renders the handover document as Markdown, `--template FILE` replaces
  it with a Jinja2 template (sandboxed; it receives the `--output json` data, in which nothing holds a secret value),
  and `--output-path FILE` writes it to a file. On stdout, the document is the only output; the run's report goes to
  stderr. The document opens with the last successful deploy: who ran it, when, the commit and the audit record.
  `deploy run` keeps that summary in the output store (`strata/deployment-audit/<deployment>`), so the report
  never needs the git audit sink.
- `spec.documentation` on a deployment: after each successful `deploy run`, the handover document is written as
  `_handover.md` next to the audit manifest and pushed with it by every enabled git audit sink. `strata validate`
  rejects it without an `output_store`, without an enabled git audit sink, or with a missing or invalid template. A
  render failure, any error a template raises included, is a warning, never a failed deploy. Under `deploy run
  --pin`, the document lists the pinned version's images.
- `deploy run` now records the secrets each step used in the report record, for Helm and Compose steps too.
  `build run` includes them in its size estimate, for every step type.
- `strata validate` warns about a secret that sets `version` (`secret_version_ignored`): no store reads it yet, so
  the latest version is always used.
- `strata validate` rejects an `azure-keyvault` secret whose `value` is a URL (`keyvault_secret_value_is_url`). The
  value is the secret's name in the vault the integration points at. A resource that needs the secret's address,
  such as App Gateway's `key_vault_secret_id`, takes it as a variable.

### Fixed

- A Compose step deployed twice from one build (`deploy run --dry-run` then `deploy run`, or a retry) deployed its
  stack with every secret empty: the first run rewrote the built `docker-compose.yml` with its tokens resolved, so
  the second found no `${secret:}` token to deliver, and Docker substituted an empty string with only a warning.
  Every `${var:}` and `${output:}` value was also the first run's. `deploy run` now writes the resolved document to
  `docker-compose.deploy.yml` next to it and deploys that, leaving the built file as `build run` wrote it. Like
  before, the file holds no secret value: a secret is `${KEY}`, its value in the `docker stack` process environment.

## [2.5.0] - 2026-11-09

### Added

- strata runs on Python 3.12 and 3.14 as well as 3.13 (`requires-python = ">=3.12"`). No behaviour changes: the
  full test suite passes on all three, and CI now tests every pull request on each of them. Type checking and linting
  target 3.12, the oldest, so code that needs a newer Python fails the checks instead of a 3.12 install.

## [2.4.0] - 2026-11-08

### Added

- The deploy commands print the version and path of each tool whose version they read (`helm 4.3.1
  (/usr/bin/helm)`), and `deploy run`, `apply` and `destroy` record them in the deployment manifest's
  `artifacts.providers` (`tool_version`, `tool_path`, one entry per provisioner). `strata integration check` shows the
  path, and `check`/`list --output json` carry `tool_path` and a new `warnings` list.
- `build run` places a local Helm chart's dependencies in the built chart's `charts/`: the version in a committed
  `Chart.lock`, else the declared version (a range resolved at each build), pulled through the `.strata/helm/` cache;
  a `file://` dependency is copied from beside the source chart. Before, a chart that did not commit its dependencies
  failed at `helm upgrade`. Strata never writes a `Chart.lock`. A dependency whose repository URL matches a declared
  helm or oci remote uses that remote's credentials. `meta.yaml` and the SBOM record each dependency's exact version and
  digest. A dependency the machine cannot pull (no `helm`, credentials that need `--resolve`, `--offline`) is a
  `helm_chart_dependency_not_pinned` warning, and the deploy refuses that chart before any step. New build errors:
  `helm_chart_dependency_lock_out_of_sync`, `helm_chart_dependency_missing`, `helm_chart_dependency_wrong_version`,
  `helm_chart_dependency_repository_name`, `helm_chart_dependency_outside_source`.
- `strata validate` checks the release name and Kubernetes namespace of every enabled Helm module reference, defaults
  included, and fails on what Helm or Kubernetes would reject: `helm_release_name_invalid` (lowercase letters,
  digits, `-` and `.`, at most 53 characters), `helm_kubernetes_namespace_invalid` (lowercase letters, digits and
  `-`, at most 63 characters) and `helm_release_conflict` (two references in one workspace installing the same release
  into the same Kubernetes namespace). The defaults are the module reference name and the strata namespace name, which
  may contain `_`: such a configuration now fails `validate`, `build run` and `deploy` instead of the `helm upgrade`.
  Set `spec.release_name` / `spec.kubernetes_namespace` on the module, or rename the reference or namespace.
- `spec.kube_context` on a deployment names the context in the caller's kubeconfig that its Helm steps deploy to.
  Every `helm` call (deploy, dry-run, uninstall, destroy preview) then uses that context whichever is active, and the
  deploy stops before its first step when the kubeconfig has no such context (`helm_kube_context_missing`) or the
  environment sets `HELM_KUBEAPISERVER` or another `HELM_KUBECONTEXT` (`helm_kube_context_conflict`). Without the field
  nothing changes at deploy; `strata validate` warns (`helm_kube_context_unset`) for a deployment with a Helm step.
- `spec.docker_context` does the same for Compose steps: every `docker` call (`stack deploy`, the `stack config`
  dry-run, `stack rm`, `stack services`) runs with `DOCKER_CONTEXT` set to it, and the deploy stops before its first
  step when Docker has no such context (`compose_docker_context_missing`) or the environment sets `DOCKER_HOST`, which
  would override it, or another `DOCKER_CONTEXT` (`compose_docker_context_conflict`). `strata validate` warns
  (`compose_docker_context_unset`) for a deployment with a Compose step and no `docker_context`. A `${...}` token in
  either field is a schema error.
- `checkov_policy.config_file` names a Checkov config file (e.g. `.checkov.yaml`) relative to the root of the
  repository each scanned provisioner or Helm module comes from. `build run` copies it into the build path
  (`checkov-config/`) and passes it as `--config-file`; `strata policy check` and `policy findings` use that copy. A
  repository without the file is reported through `on_missing_data`. Flags strata sets itself (`skip_checks`,
  `include_checks`, `framework`, ...) win over the same keys in the file.

- `build run` pins every registry Helm chart: it pulls the chart into `.strata/helm/` (in an isolated Helm
  configuration, with the remote integration's credentials) and records the exact version, the archive's SHA-256 and
  its subcharts in the module's `meta.yaml`. An exact `chart_version` already cached needs no network and no `helm`.
  Registry charts now get the configuration key check at build and `helm lint` in the check phase, and the SBOM
  records their exact version, `SHA-256` hash, `appVersion` and subcharts. When the build cannot pull (no `helm`, a
  credential that needs `--resolve`, or the new `build run --offline`), the chart is left unpinned with a
  `helm_chart_not_pinned` warning and the build still succeeds (ADR-0054).

### Changed

- **Breaking:** strata passes Helm 4's flags by default: `helm upgrade --wait=watcher --rollback-on-failure` and
  `--dry-run=client` instead of `--wait --atomic` and `--dry-run`, so Helm 4 no longer prints deprecation warnings.
  Helm 3 is deprecated and needs a declaration: a `helm` integration whose `spec.version` allows no Helm 4 keeps the
  old flags (and gets a `helm_3_deprecated` warning). A deploy command whose steps include a Helm step, and any
  command that checks a declared `helm` integration, now reads `helm version` first; Helm 3 without the declaration
  stops there with `helm_3_not_declared`, before any step (a warning under `deploy destroy`, which runs only
  `helm uninstall`). To stay on Helm 3, add:

  ```yaml
  apiVersion: strata.huybrechts.xyz/v2
  kind: integration
  meta:
    name: helm
  spec:
    type: helm
    capabilities: [container]
    version: "<4"
  ```

  With Helm 4 the wait uses kstatus (`watcher`), so the deploy identity needs the `watch` verb on the chart's
  resources ([ADR-0055](../docs/decisions/0055-helm-4-by-default-helm-3-only-when-declared.md)).
- The Helm configuration key check no longer warns about a key named after a chart dependency (or its `alias`) or
  `global`: Helm passes those to subcharts.
- `deploy run`, `plan` and `apply` install a registry Helm chart from the archive the build pinned, not from the
  registry: before the first step they take it from `.strata/helm/` or pull exactly that version again, and stop on an
  unpinned chart (`helm_chart_not_pinned`) or a different archive (`helm_chart_digest_changed`). A build made before
  this release has no pin, so rebuild before deploying. A CI build job needs `helm` (strata's image does not ship it)
  and the registry credentials for its registry charts to be pinned.
- Helm and Compose steps honour a stage's `timeouts.plan` and `timeouts.apply` (and `STRATA_TIMEOUT_<STEP>_PLAN` /
  `_APPLY`); before, only `check` and `destroy` reached them. For Helm the timeout is per release: an `apply` timeout
  of N seconds sets Helm's `--timeout` to N/2, leaving the other half for the rollback. Without an override
  nothing changes (600 s and `--timeout 5m`).

### Fixed

- Helm: a local chart keeps its own default values. `build run` used to write the module's values over the chart's
  `values.yaml`, so every default the module did not repeat was lost, and a template reading one failed with `nil
  pointer evaluating interface {}`. The module's values are now `strata-values.yaml` beside `meta.yaml`, given to Helm
  with `-f` on top of the chart's defaults, as for a registry chart. A module that relied on the chart's defaults being
  dropped now gets them.
- Helm: a registry chart module with no `services` and no `configuration` can deploy. No values file was written, and
  `helm upgrade` failed on the missing `-f` file. `strata-values.yaml` is now always written (`{}` when empty).
- Run `strata build run` again before deploying: a build from an earlier version has no `strata-values.yaml`, and
  `deploy` reports that for each Helm module instead of deploying without values.

### Security

- Helm: a module's secret values reach `helm upgrade` on stdin (`-f -`) instead of as `--set-string path=value`
  arguments, where any user of the machine could read them in the process list. A secret inside a list now arrives
  as a list item; `--set-string` read the index as a map key.
- Helm: `deploy plan` and `deploy run --dry-run` no longer stream the rendered manifest, whose Secret `data` is base64
  and was not redacted. Each release is reported as one `Kind/name` line per resource, hooks marked, and is shown
  without `--follow` too.

## [2.3.3] - 2026-11-06

### Added

- `provisioner_lock_policy` on a workspace: `build run` fails (`deny`) or warns (`warn`) when a Terraform or
  OpenTofu provisioner has no `.terraform.lock.hcl`, or one that does not lock every provider it declares. It has
  `rings` and an `allow` list with a reason and an optional expiry, runs in `strata policy check`, and is recorded in
  `policy_results.json`. Opt-in: without it nothing fails.
- Without the policy, `build run` prints an `info` line (`sbom_provider_unlocked`) for each Terraform root whose
  providers end up in the SBOM without a version, or not at all (used without `required_providers` and no lock
  file). `info` never fails a command, not even `validate --strict`.

### Changed

- Image purls in `sbom.json` follow the purl specification: the registry is the `repository_url` qualifier instead of
  part of the name (`pkg:docker/goauthentik/server@2026.5.2?repository_url=ghcr.io`, was
  `pkg:docker/ghcr.io/goauthentik/server@2026.5.2`), and every spelling of a Docker Hub image gives one purl
  (`postgres`, `library/postgres` and `docker.io/library/postgres` are all `pkg:docker/postgres`). Every image purl
  changes once, so Dependency-Track and similar tools see new components. The image gate reads both shapes.
  An image written with `docker.io/...` now reaches the image gate as its short Docker Hub path (`postgres`, not
  `docker.io/library/postgres`): rename such a key in an `image_findings` integration's
  `configuration.images.names`, and check `name_strategy: full`.
- `sbom.json` names what it describes: `metadata.component` is the deployment (with its workspace, `kind:
  version` and the commit being built, `strata:commit`), `metadata.tools` is strata and its version, and the
  deployment depends on every component. The commit comes from the same variables as the release gate
  (`STRATA_COMMIT_SHA`, then the CI's own), else `git rev-parse HEAD`.
- A component's `bom-ref` is its purl instead of a random value, so two builds of one deployment can be diffed.
  A component found by two collectors (an image declared by the module and named in the rendered compose file) is
  listed once.
- An image and a chart in `sbom.json` say where they are used, in a `strata:used-by` property:
  `service:MODULE/SERVICE` and `variable:KEY` for an image, `module:NAME` for a chart. An image used by several
  services lists all of them; before, only the first service name was kept.
- An image or chart whose version a `kind: version` pin chose has `strata:pinned-by: <version document>` in
  `sbom.json`.
- An image referenced by digest (`...@sha256:...`) also lists the digest as a CycloneDX hash
  (`"hashes": [{"alg": "SHA-256", "content": "..."}]`), so tools that match on hashes find it. Only the digest
  already in the solution is used; a tag is not looked up in the registry.
- A Helm chart taken from a `type: helm` remote is recorded with the remote's URL as `repository_url`, and is not
  listed a second time from its materialised `Chart.yaml`.
- A Terraform provider in `sbom.json` has the version locked in its root module's `.terraform.lock.hcl`
  (`pkg:terraform/hashicorp/azurerm@4.12.0`) instead of its `required_providers` constraint (`@~>4.0`), which
  scanners cannot match. Without a lock file the provider has no version. The constraint is kept in the
  `strata:version-constraint` property. A provider used without a `required_providers` entry is now listed from the
  lock file, and a provider from a registry other than `registry.terraform.io` carries it as `repository_url`.
  The lock file is the nearest one in the folder or a parent, so a child module's providers take the root's
  locked version. `.terraform/` folders (what `terraform init` downloaded during a `deploy run` in the same build
  path) are no longer read as source.
- A Terraform registry module in `sbom.json` has a version only when its `version` pins exactly one (`5.0.0` or
  `= 5.0.0`); a range such as `~> 5.0` was written into the purl and is now the `strata:version-constraint`
  property. Two modules from one source at different exact versions are now both listed (before, only the first
  one was).

### Fixed

- An image referenced by digest only (`postgres@sha256:...`) is no longer marked `strata:tag-stability: floating`
  in `sbom.json`, nor reported as a floating-tag warning by `build run`: a digest is the most precise reference.

## [2.3.2] - 2026-11-05

### Removed

- The `path_remote_outside_solution` warning from 2.3.1. It fired for every `path` outside the solution, so
  `strata validate --strict` failed for the sibling layout (`path: ../iac`) that 2.3.1 was made for. Writing `path`
  already states that the checkout was placed by someone else, so a path outside the solution is no longer reported.
  A `path` that does not exist still fails `build run`.

## [2.3.1] - 2026-11-05

### Changed

- The `path` of a `git` remote is now resolved from the solution root (the folder holding `strata.yaml`) and may use
  `..` or be absolute. Before, it had to stay inside the solution. A pipeline that checks repositories out beside
  each other, which is what an Azure DevOps job with several `checkout:` steps does, can now use `path: ../iac` and
  keep one value for CI and a workstation. `strata validate` warns (`path_remote_outside_solution`) when the path
  leaves the solution, so a changed `path` that makes a build read another folder is visible in review.

## [2.3.0] - 2026-11-05

### Added

- A `script` provisioner can set `plan_args` and `destroy_args`. `plan_args` run the script when a preview is
  requested (`deploy run --dry-run`, `deploy plan`); a real `deploy run` never runs them, so the script is not executed
  twice. `destroy_args` make the step destroyable: `deploy destroy --force` runs the script with them, and
  `deploy destroy --dry-run` prints `would run: ...` without executing it. Without `destroy_args` a destroy still
  refuses the step. strata cannot check that a script is free of side effects when run with `plan_args`.
- Every script run sets `STRATA_PHASE` (`plan`, `apply` or `destroy`), so one script can tell which phase it is in.
- A `script` provisioner can set `values`, a list of keys, to limit what the script receives in `STRATA_VALUES`: only
  those keys plus `deployment:name` and `deployment:tenant`. Unset, the script still receives every resolved value. A
  listed key that does not resolve fails the step, and `strata validate` reports a key no environment declares
  (`undeclared_script_value`). Arguments are not limited by it.
- A `script` step can produce outputs: it writes a JSON object to the file named by the environment variable
  `STRATA_OUTPUT_FILE`, and later steps use the keys as `${output:<step>.<key>}`. The `_sensitive` key lists the keys
  that are secret; those are never recorded in the `output_store`. A file that is not valid is ignored with a warning;
  the step does not fail.
- A `git` remote in `strata.yaml` can name a `path` instead of a `reference`: the folder where a pipeline already
  checked the repository out, inside the solution. strata reads it there and never clones it, so the ref is stated
  only in the pipeline and the pipeline no longer has to link its checkout to `.strata/remotes/<name>/<reference>`.
  `path` implies `fetch: external`. The deploy's audit manifest records the checkout's commit, and warns when it
  cannot (not a git checkout, or `git` failed). A `path` remote cannot
  take an `integration`, be pinned by a `version` document, or be pushed to by a GitOps provisioner or audit sink
  (`strata validate` reports the last as `path_remote_not_pushable`).
- `strata deploy destroy --smoke-test`: the zero-contact mode of `deploy run --smoke-test` for a teardown. It reports
  "would destroy step X" per step, last first, without calling any tool or reading the output store. Values must still
  resolve. It reports a step that cannot be destroyed, a filter that selects no step and an `${output:}` reference
  that nothing could serve. `deploy destroy` now needs exactly one of `--force`, `--dry-run` or `--smoke-test`.

### Changed

- A `script` step's `${output:}` references (its arguments, and `<step>.<key>` entries of `values`) are now checked
  before the step runs, like a Terraform step's, and a preview says why one is missing: a preview applies no step, so
  an upstream output exists only if an earlier real run recorded it in the workspace's `output_store`.
- When a step's outputs cannot be collected after a successful deploy, the run now warns with the reason. Before, a
  Terraform or GitOps step whose output command failed said nothing, and a later `${output:}` failed with no
  explanation.
- A failed `init`, `validate`, `plan`, `deploy` or `destroy` now says how the command ended (`exit code N`, or `timed
  out and was killed`) and shows the last 10 lines of its stdout and stderr, with resolved secrets redacted. Before, only
  stderr was shown, so a script that printed its error to stdout gave an empty message, and a timeout looked like any
  other failure.
- `deploy apply` now refuses a `script` or ArgoCD/Flux step before running anything, and says to use `deploy run`.
  These tools write no saved plan, so it used to fail with "run 'deploy plan' first", advice that could not work.

### Fixed

- A sensitive output (a Terraform output marked `sensitive`, or a key a script lists in `_sensitive`) is now masked in
  the streamed output and error messages of the steps that run after it, like a secret is.
- `deploy run --dry-run` and `deploy plan` now show what a `script` step would run (`would run: ...`, secrets
  redacted). The preview was computed and then dropped.
- `build run` now fails when a `script` provisioner's `file` is not in its materialised source. It used to pass, and
  `deploy run` failed later with a raw `[Errno 2]`.

## [2.2.0] - 2026-11-04

### Added

- `strata deploy destroy DEPLOYMENT`: tears down what `deploy run` created (Terraform, Helm, Compose and GitOps
  steps), last step first. It needs exactly one of `--force` (destroy, no prompt) or `--dry-run` (preview, changes
  nothing). Image tag policy and security gates are skipped, so a `deny` never blocks a teardown. A `script` step
  cannot be destroyed and stops a `--force` run before anything is removed. A step that uses `${output:}` needs the
  workspace's `output_store`. See [docs/design/deploy-destroy.md](../docs/design/deploy-destroy.md) and
  [docs/how-to/ci-deploy-pipeline.md](../docs/how-to/ci-deploy-pipeline.md).
- A destroy preview for every tool that can show one: `terraform plan -destroy` (never saves a plan file), the
  resources of a Helm release (never Secret data), a Compose stack's running services, and whether a GitOps file is
  in the config repo.
- `strata audit status` and `strata audit changes` show an `action` (`deploy` or `destroy`) in the table and the JSON.
  A real destroy is audited like a deploy and sends `deployment.destroyed` to audit sinks, so a sink that filters
  `events` must list it.
- Integrations gain `SUPPORTS_DESTROY`, `plan_destroy()` and `destroy_namespace()`, all with defaults.

### Changed

- `docker stack rm` now runs with `--detach=false`, so a Compose destroy returns once the stack's tasks have stopped.
- A rendered `output.template` now ends exactly as its template does. It used to lose one final newline, so a file
  pushed to a GitOps repo had no newline at end of file.

### Fixed

- `strata build run` crashed with a raw `FileNotFoundError` for any ArgoCD or Flux provisioner (one with no `source`),
  because the step's build directory was never created before its GitOps sidecar was written.

## [2.1.0] - 2026-11-03

### Added

- `${deployment:name}` and `${deployment:tenant}` value tokens: the name and tenant of the deployment being run, so a
  workspace's Terraform backend can build every deployment's state key from one rule
  (`key: ${deployment:name}.tfstate`) instead of a per-deployment variable. `${deployment:tenant}` on a deployment
  without a tenant is an error, reported by `strata validate` and by `deploy run`.

## [2.0.0] - 2026-11-02

v2 is a ground-up rebuild of strata, not an upgrade of v1: documents use `apiVersion: strata.huybrechts.xyz/v2` and the CLI surface is new. Every feature was built from what v1 and real consumer pipelines actually do. See [docs/decisions/README.md](../docs/decisions/README.md) for the ADR index.

### Document model

- Twenty document kinds, each with an exported JSON Schema (`.strata/schemas/`) that editors and AI agents can read: `solution`, `configuration`, `provider`, `providerconfig`, `attributeconfig`, `topologyconfig`, `resource`, `network`, `firewall`, `dns`, `module`, `namespace`, `workspace`, `version`, `integration`, `tenant`, `deployment`, `environment`, `artifact` and `asset`.
- Documents reference each other by `(kind, name)`, never by file path. `strata.yaml` marks the solution root and documents are discovered from there.
- A deployment composes a workspace, an environment and a tenant, supports `extends` chains, and can rebind provider and asset slots per deployment.
- `attributeconfig` enforces a JSON Schema fragment against matching documents, or against a deployment's merged values.
- `${var:}`, `${secret:}` and `${feature:}` tokens resolve against the environment.

### Commands

- `strata validate`: schema checks plus cross-document checks (unresolved variables, missing modules, dangling pins, topology references).
- `strata build run`: renders a deployment's provisioners into Terraform, Compose or Helm output on disk. It executes nothing.
- `strata deploy run`: executes plan, apply, or an upgrade against the build output with fully resolved values; `deploy setup | check | plan | apply` run the phases on their own. `--dry-run` runs a real plan preview, `--smoke-test` checks wiring without contacting anything, and `--follow` streams the tool's output with secrets redacted (also as NDJSON).
- `strata deploy list` and `strata deploy images`: list deployments and the images each one runs.
- `strata version new | set | update`: pins for artifacts, remotes, images, charts and assets; `update` lists the candidates.
- `strata promote status | apply | view`: ring, order and wave rollout across versions.
- `strata policy list | check | findings` and `strata integration list | check`.
- `strata values`, `strata graph`, `strata path`, `strata audit` and `strata sln init | update`.
- Structured JSON output and stable exit codes on every command, so pipelines and agents can drive it.

### Guard rails

- Security gates: `release_gate_policy` and `image_gate_policy` on the workspace, checked by `strata deploy run` before any step, backed by any `release_gate` / `image_findings` integration (Aikido Security first). See [docs/how-to/security-gates.md](../docs/how-to/security-gates.md).
- `cve_policy` judges the build's `sbom.json` and `checkov_policy` judges the rendered sources, both through `strata policy check`. A failed Checkov check without a severity counts at any `severity_gate`.
- `image_tag_policy`: opt-in rules on the tag of every image a deployment runs (no tag, floating tag such as `latest`, tag pattern, required pin, per-ring rules, an allow list with expiry). It is judged offline by `build run`, `deploy run`, `deploy images` and `policy check`.
- Integration readiness: every command first checks each enabled integration (tool on PATH, `spec.version`); a `required: true` integration that is not ready stops the run. `git`, `trivy`, `grype` and `checkov` are declarable integration types.

### Integrations

- Terraform, Compose and Helm provisioners; private Helm and OCI chart registries through a `remote` integration.
- Artifacts (a pinnable container image reference) and assets (templated input files, optionally from git) flow into the rendered output.
- A VS Code extension, published on the Marketplace listing `huybrechts-xyz.xyz-strata`, with views for the document graph, integrations and policies.

### Known limits

- Helm modules do not receive the declared service image in the chart values, so `image_tag_policy` judges the declared image of a Helm module but cannot see an image that only the chart knows.
- Deployment stage `gates` are not available yet.

[Unreleased]: https://github.com/huybrechtsxyz/strata/compare/v2.6.0...HEAD
[2.6.0]: https://github.com/huybrechtsxyz/strata/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/huybrechtsxyz/strata/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/huybrechtsxyz/strata/compare/v2.3.3...v2.4.0
[2.3.3]: https://github.com/huybrechtsxyz/strata/compare/v2.3.2...v2.3.3
[2.3.2]: https://github.com/huybrechtsxyz/strata/compare/v2.3.1...v2.3.2
[2.3.1]: https://github.com/huybrechtsxyz/strata/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/huybrechtsxyz/strata/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/huybrechtsxyz/strata/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/huybrechtsxyz/strata/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/huybrechtsxyz/strata/releases/tag/v2.0.0
