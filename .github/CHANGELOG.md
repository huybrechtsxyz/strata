# strata - Changelog

Concise, user-facing summary of each release. Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/); the current shape of each feature lives in [docs/design/](../docs/design/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Changed

- Helm and Compose steps honour a stage's `timeouts.plan` and `timeouts.apply` (and `STRATA_TIMEOUT_<STEP>_PLAN` /
  `_APPLY`); before, only `check` and `destroy` reached them. For Helm the timeout is per release: an `apply` timeout
  of N seconds sets Helm's `--timeout` to N/2, leaving the other half for `--atomic` to roll back. Without an override
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

[Unreleased]: https://github.com/huybrechtsxyz/strata/compare/v2.3.3...HEAD
[2.3.3]: https://github.com/huybrechtsxyz/strata/compare/v2.3.2...v2.3.3
[2.3.2]: https://github.com/huybrechtsxyz/strata/compare/v2.3.1...v2.3.2
[2.3.1]: https://github.com/huybrechtsxyz/strata/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/huybrechtsxyz/strata/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/huybrechtsxyz/strata/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/huybrechtsxyz/strata/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/huybrechtsxyz/strata/releases/tag/v2.0.0
