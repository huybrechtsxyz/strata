# strata - Changelog

Concise, user-facing summary of each release. Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/); the current shape of each feature lives in [docs/design/](../docs/design/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

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
  `${artifact:NAME.FIELD}` token for it appears in the module's or the service's `configuration`. Strata writes no
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

### Changed

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
