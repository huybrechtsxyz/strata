# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.12.0] — 2026-06-23

### Added

- **Secret generation utilities (`strata/utils/secret_generator.py`)**
  - `generate_secret(fmt, length)` — cryptographically secure generation for formats: `urlsafe`, `hex`, `alphanumeric`, `password`, `numeric`, `base64`, `uuid4`, `uuid7`
  - `mask_secret(value, show, char)` — safe masking for log/output display; moved from `commands/` into shared utils so controllers can import without violating layer rules
  - `generate_secret_command.py` and `mask_secret_command.py` converted to re-export shims

- **Auto-generated secrets (ADR 0013)**
  - `SecretGenerateSpec` on `SecretStoreModel` — declare a generator spec alongside the secret reference
  - `ValueController._resolve_secret` — generate-on-missing: if a secret is absent and `generate:` is declared, strata generates a value, writes it to the backing store, and returns it
  - Race-safe: if `set_secret` fails but a concurrent write is detected via re-read, the existing value is used without error

- **Seed-on-missing for variables and feature flags**
  - `VariableStoreModel.default` — if a variable key is absent from an integration-backed store, strata writes the declared default and returns it
  - `FeatureStoreModel.default` — same pattern for feature flags; default parsed as `"true"`/`"false"` string to boolean
  - Race-safe re-read fallback on write failure for both types

### Changed

- `ValueController._resolve_variable`, `_resolve_secret`, `_resolve_feature` — return signature extended from `(value, error)` to `(value, error, note)` to carry seed/generate annotations without polluting error lists
- `ResolvedValues.for_stage` — filters `secret_notes` alongside secrets when building a stage-scoped copy

### Fixed

- Error message for `password` format minimum length corrected from `"--length >= 4"` to `"length >= 4"` in both CLI output and test assertions

### Documentation

- `docs/guides/faq.md` — restructured as high-level explainer (what is strata, how it works with Terraform/Ansible/Helm)
- `docs/guides/config-faq.md` — new; configuration-specific questions (SSH key setup, existing Terraform state adoption, multi-stage deployment YAML, rollback procedure)
- `docs/guides/features.md` — new; practical capability overview aimed at DevOps engineers evaluating strata
- `docs/decisions/0013-auto-generated-secrets.md` — ADR for auto-generated secret design

### Testing

- `tests/strata/utils/test_utils_secret_generator.py` — new; covers all `generate_secret` formats and `mask_secret` edge cases
- `tests/strata/commands/secret/` — removed; unit tests relocated to `utils/`, CLI tests consolidated into `test_commands_secret.py`
- `tests/strata/commands/test_commands_secret.py` — renamed from `test_cli_secret.py` to match project convention
- `tests/strata/controllers/test_controllers_value.py` — updated all `_resolve_*` call sites to unpack 3-tuple `(val, err, _)`

---

## [0.11.0] — 2026-06-23

### Added

- **Promotion Strategies System (ADR 0011)**
  - Named progressions: ordered lists of environments for version promotion
  - Named strategies: policies that govern promotion waves and guardrails
  - Wave assignment on deployments via `spec.promotion.wave` (iteration, match_labels, or default)
  - Scope predicates: layer-based filtering for promotion targets
  - CLI command group: `strata promote` (start, rollback, status, matrix, history, log)
  - Activity log: `.strata/promotions/` for audit trail (gitignored)
  - Promotion-record in artifact store for state tracking

### Changed

- **Tenant Naming (ADR 0012) — BREAKING CHANGE**
  - Renamed concept: `customer` → `tenant`
  - Kind: `customer` → `tenant`; Model: `CustomerModel` → `TenantModel`; Service: `CustomerService` → `TenantService`
  - Policy: `customer_zone` → `tenant_zone`; Directory: `customers/` → `tenants/`
  - Field: `spec.customer` → `spec.tenant`; Properties: `properties.customer` → `properties.tenant`
  - Terraform variables: `customer.auto.tfvars.json` → `tenant.auto.tfvars.json`
  - Ansible hostvars: `strata_customer` → `strata_tenant`

### Migration Guide (v0.10.0 → v0.11.0)

```bash
mv customers/ tenants/
# Update kind: customer → kind: tenant and spec.customer → spec.tenant in all YAML files
```

### Documentation

- Updated all platform docs to reflect tenant terminology
- Added ADR 0011 (Promotion Strategies), ADR 0012 (Rename Customer → Tenant)
- Updated `at-scale.md` with multi-tenant design patterns

---

## [0.10.0] — 2026-06-22

### Added

- **`strata deploy show`** — new command that loads and displays the fully resolved deployment configuration (workspace, environments, variables, secrets, features) without executing. Useful for auditing what a deployment will use before running it.
- **`strata deploy list`** — new command that recursively scans a directory for `kind: deployment` YAML files and emits a flat table of all deployments with their layer fields promoted to top-level columns. Designed for CI matrix generation — pipe the JSON output directly into a GitHub Actions matrix strategy via `jq -c '.data.deployments'`.
- **`strata new` bundle templates** — `strata new <template>` now resolves bundle templates (directories) in addition to single-file templates. A bundle directory mirrors the desired output tree; `${var}` substitution is applied to both file contents and path segments. Workspace bundles override package bundles by the same name.
- **Overlap validation (`strata validate --path`)** — new `--path GLOB` option validates multiple deployment manifests for cross-manifest conflicts: duplicate artifact paths, Terraform backend collisions, and namespace overlaps across deployment layers. The non-overlap guarantee is now machine-checkable.
- **Remote reference overrides** — environment files now support `spec.overrides.remotes[]` to pin a specific remote to a version, tag, or branch for that environment only. The base reference is defined once in the configuration remote; deviations are explicit per-environment overrides. `BaseBuildCommand` checks out remotes to their effective reference at build time.
- **`OverlapController`** — new controller that orchestrates cross-manifest overlap checks (artifact paths, Terraform backends, namespaces). Used by `strata validate --path`.
- **`RepositoryController.ensure_remote_refs`** — new method that checks out all remotes in a deployment to their effective reference before a build begins.
- **`GitIntegration`** — new methods: `fetch`, `checkout`, `resolve_commit_sha` for fine-grained remote management.
- **`NamespaceType` enum** — `dedicated` vs `shared` namespace types added to the namespace model, affecting overlap validation behavior (shared namespaces are excluded from uniqueness checks).
- **Docs:** New guide section "Variable Flow: Customer Metadata → Terraform" in `docs/guides/at-scale.md` — documents the full chain from `customer.yaml spec.configuration` through environment variable stores to `TF_VAR_*` injection with three concrete patterns (tier-wide constants, per-customer overrides, CI-injected values).
- **Docs:** New section "Deploying ArgoCD ApplicationSets" in `docs/config/deployment.md` — covers the recommended pattern for managing ArgoCD via `server.additionalApplications` / `extraObjects` helm values, with full workspace + environment + override YAML examples.
- **Docs:** `strata deploy list` documented in `docs/platform/commands.md` with usage, option table, JSON output shape, and a complete GitHub Actions matrix workflow snippet.
- **Docs:** Workspace-per-layer pattern documented in `docs/guides/at-scale.md` (three workspaces: bootstrap, infrastructure, application; why one workspace per layer; 400-deployment example).

### Changed

- **`strata deploy output`** — unified output handling; stored artifact support enhanced.
- **Environment model** — `spec.overrides.remotes[]` field added with `RemoteOverrideModel` (name, reference, description). Duplicate remote names within one environment rejected at parse time.
- **`DeploymentService`** — applies remote reference overrides when resolving environments; effective reference for each remote is the environment override if present, otherwise the configuration default.

### Dependencies (GitHub Actions)

- `astral-sh/setup-uv` bumped from v2 to v7
- `actions/setup-python` bumped from v4 to v6
- `peaceiris/actions-gh-pages` bumped from v3 to v4
- `actions/checkout` bumped from v4 to v7

---

## [0.9.3] — 2026-06-20

### Added

- **HelmBuilder:** `meta.yaml` now includes chart coordinates (`chartName`, `chartVersion`, `chartRepository`) for registry-based modules. Build artifacts are fully self-contained — deployers and external tools can drive `helm upgrade` without re-reading the module spec.
- **HelmBuilder:** `spec.configuration` (module-level) is now merged into `values.yaml`. For service-less modules this creates the values file; for modules with services it merges on top as overrides.
- **HelmBuilder:** Service-less helm modules (registry chart + values file pattern) now produce `meta.yaml` correctly. Previously these modules were silently skipped.
- **HelmDeployer:** `--wait`, `--atomic`, and `--timeout 5m` flags added to `helm upgrade --install`. Deploys now block until pods are healthy and auto-rollback on failure.
- **Docs:** New guide `docs/guides/helm-modules.md` covering the full helm lifecycle (define, build, deploy, GitOps integration).

### Changed

- **HelmDeployer:** Chart coordinates are now read from `meta.yaml` instead of `module.spec.source` at deploy time. The deployer no longer depends on the module YAML for chart resolution — only the build artifact.
- **TerraformIntegration:** All integration methods (`init`, `validate`, `plan`, `apply`, `destroy`) now forward `**kwargs` to `_run_integration`, enabling `line_callback` for streaming output.
- **TerraformIntegration:** `plan()` passes `ok_returncodes={2}` when using `-detailed-exitcode`, suppressing the spurious "Integration command failed" warning for exit code 2 (success with changes).
- **BaseIntegration:** `_run_integration` accepts `ok_returncodes: Optional[set]` parameter to suppress warnings for expected non-zero exit codes.
- **Deploy verbose output:** Streaming output now prefixed with deployer tool name and `│` gutter (e.g. `terraform │ ...`), with cyan for stdout and yellow for stderr.

---

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder now skips writing variable files for empty sections (providers, topologies, resources, modules, namespaces, firewalls, DNS, networks). Previously all 9 files were written unconditionally, producing empty `strata_*.yml` files for features not configured in a deployment.
- `test_version.py` fixture replaced fragile `../../VERSION.txt` relative path with `Path(__file__)`-anchored navigation; added `.strip()` to handle trailing newline in `VERSION.txt`. Fixes version test failures in CI.
- Removed invalid `sync` input from `astral-sh/setup-uv@v8.1.0` in `install-python` action, eliminating a warning on every CI job.

---

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings (floating tags, parse errors) are now silent by default during `strata build run`. The SBOM is still generated with full fidelity — use `--verbose` or run `strata build sbom` explicitly to see advisories. Configured policies continue to evaluate component properties regardless of warning visibility.

---

## [0.9.0] — 2026-06-18

### Breaking Changes

- **Configuration YAML schema:** `spec.repositories` renamed to `spec.remotes` in configuration files (ADR 0010). Existing configuration YAML files must update the field name. Solution repos (`solution.json → spec.repositories`) are unchanged.

### Added

- Self-SBOM generation in CI (`ci-build.yml` Job 3): `strata build sbom --scan .` runs against its own source tree, producing a CycloneDX 1.6 `sbom.json` artifact (dogfooding)
- `sbom.json` artifact attached to every GitHub Release via `ci-release.yml`
- `workflow_dispatch` trigger on `ci-docs.yml` for manual doc builds and testing
- Edge image jobs now gate on SBOM job success (`needs: [build, docs, sbom]`)
- ADR 0010: Decision record for renaming configuration repositories to remotes

### Changed

- `RepositoryModel` renamed to `RemoteModel` (backwards-compatible alias retained)
- `RepositoryType` enum renamed to `RemoteType` (backwards-compatible alias retained)
- `ConfigurationService.get_repositories()` → `get_remotes()`
- `ConfigurationService.get_repo_map()` → `get_remote_map()`
- `ConfigurationModel.get_repo_map()` → `get_remote_map()`
- `SourceModel.repository` field description corrected to reference solution repos

### Fixed

- **Bug:** Workspace deep validation checked `configuration_model.spec.repositories` (manifest backends) instead of solution repos for provisioner `source.repository` references — repos registered via `strata repo add` now validate correctly

---

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow: Added `--group doc` to `uv sync` to include Sphinx dependencies
- CI docs workflow: Removed impossible `if` condition that prevented deployment on tag triggers

---

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for automated documentation deployment on release tags
- Enhanced workspace volume model with optional fields: `size`, `mount_path`, `driver`, `mode`, `configuration`

### Fixed

- Issue #109: Allow any string for topology volume type (removed restrictive enum)
- Clarified `access_mode` (container-level concurrency) vs `mode` (filesystem permissions) in volume model

### Changed

- Documentation now deployed to GitHub Pages (`https://huybrechtsxyz.github.io/strata`)
- Support link updated to GitHub Issues (`https://github.com/huybrechtsxyz/strata/issues`)
- Updated `DOCS_URL` and `SUPPORT_URL` constants in `src/strata/utils/config.py`

---

## [0.8.0] — 2026-06-17

### Added

- **CVE audit (`strata build sbom --audit`)** — vulnerability scanning step that runs after SBOM generation using a locally-installed scanner (Trivy preferred, Grype as fallback). No external API calls — the scanner runs offline against the generated `sbom.json`.
  - `--audit` flag enables scanning; no-op with a warning when no scanner is in PATH.
  - `--severity LEVEL` (default `MEDIUM`) sets the minimum severity to report (`CRITICAL` | `HIGH` | `MEDIUM` | `LOW` | `UNKNOWN`).
  - `--fail-on LEVEL` exits with code 3 when findings at or above the given severity exist. Without `--fail-on`, the audit is advisory only.
  - Console output: severity summary table + top 10 findings.
  - NDJSON output: each finding emitted as a `data` event with an `audit_finding` payload.
  - JSON/text output: `audit` key added to the result envelope with severity counts.
- **`CveScannerIntegration`** — new integration (`integrations/cve_scanner.py`) wrapping Trivy and Grype. Auto-detects whichever backend is in PATH. Exposes `scan_sbom(path, severity_threshold, timeout) → CveAuditResultModel`.
- **`CveFindingModel` / `CveAuditResultModel`** — Pydantic models for structured CVE scan results added to `models/sbom_model.py`.
- **`sbom_license` policy** — new built-in policy type that enforces license allow/deny lists on SBOM components at the `build` phase. Reads each component's `strata:license` property (set by collectors or lockfile parsers). Supports fnmatch globs (`BSD-*`, `GPL-*`). Deny always wins over allow when both lists are configured. `unknown_action` setting controls behaviour for components without license metadata (`allow` | `warn` | `deny`; default `warn`).
- **Expanded lockfile parser support** — six new built-in parsers added to `DependencyFileCollector`:
  - `NugetPackagesLockParser` — `packages.lock.json` (.NET / NuGet)
  - `PackagesConfigParser` — `packages.config` (.NET / NuGet)
  - `MavenPomParser` — `pom.xml` (Java / Maven)
  - `GradleLockParser` — `gradle.lockfile` (Java / Maven)
  - `GemfileLockParser` — `Gemfile.lock` (Ruby / gem)
  - `CargoLockParser` — `Cargo.lock` (Rust / cargo)
  - `ComposerLockParser` — `composer.lock` (PHP / Packagist)
- **Lockfile parser package refactor** — `builders/sbom/lockfile_parsers/` restructured from a single file into a package with one module per ecosystem; all parsers re-exported from `__init__.py` for backward compatibility.
- **Auto-discovery from `.strata/lockfile_parsers/`** — any `.py` file dropped in `.strata/lockfile_parsers/` at the workspace root is imported automatically before the first SBOM build. No `collectors.yaml` entry required. Files prefixed with `_` are skipped.
- **NDJSON datalines for `strata build sbom --scan`** — with `--output ndjson`, one `data` event is emitted per discovered SBOM component in addition to the terminal `complete` event.

### Changed

- `SbomBuildCommand` constructor extended with `audit`, `audit_severity`, and `fail_on` parameters wired from the new CLI flags.

---

## [0.7.0] — 2026-06-16

### Added

- **`strata policy check`** — standalone command to evaluate all declared policies for a given deployment file without running a deploy. Accepts `--file` / `-f` (required). Reports each policy result with pass/fail, enforcement level, and any violations. Exits with code 3 when one or more `deny` policies fail; exits 0 when all policies pass or only `warn`/`audit` policies are violated.
- **`strata deploy outputs`** — reads stored deployment output artifact files written by a previous `strata deploy run`. Accepts `--stage`, `--key`, `--version`, and `--all-versions`. Resolves artifacts from `{work_path}/{outputs.path}/{deployment_name}/{version}/{stage}.json`; supports filtering to a single key for scripting.
- **Deploy phase policy hook** — `RunDeployCommand._evaluate_phase_policies("deploy", …)` is now evaluated after Terraform plan succeeds and before apply runs. This extends the existing plan-phase gate so policies annotated with `phase: deploy` can block the apply step independently.
- **`ManifestOutputsReferenceModel`** — new Pydantic model that records the workspace-relative path, stage name, version, and `written_at` ISO-8601 timestamp of a stored outputs artifact. Added as `ManifestStageModel.outputs_artifact` so each stage in the deployment manifest carries a typed reference to its output file when one was written.
- **`NamingPolicy` `targets` parameter** — the `naming_pattern` built-in policy now accepts an optional `targets` list in its `configuration` block, defaulting to `["config_name"]` for full backward compatibility. Available targets: `config_name`, `deployment_name`, `stage_names`, `workspace_name`, `topology_names`, `resource_names`, `namespace_names`, `provisioner_names`, `module_names`, `volume_names`. Targets whose required service is absent from the evaluation context are silently skipped. Unknown target names produce a policy violation so misconfiguration is caught early.

### Changed

- `RunDeployCommand._evaluate_plan_policies` renamed to `_evaluate_phase_policies(phase, stage, deployer)` — the method now accepts an explicit phase string so it can serve both `plan` and `deploy` gates without duplication.

---

## [0.6.0] — 2026-06-15

### Added

- **Policy engine** — declarative deployment guardrails evaluated at validate, build, and plan phases.
  - `PolicyModel` — Pydantic model for policy declarations in `configuration.spec.policies` (`name`, `type`, `phase`, `enforcement`, `enabled`, `description`, `configuration`).
  - `BasePolicy` / `PolicyContext` / `PolicyResult` — abstract base, evaluation context dataclass, and typed result dataclass.
  - `PolicyEngine` — coordinator that instantiates built-in and custom policy types, evaluates a list of enabled policies for a given phase, and accumulates results.
  - Four built-in policy types:
    - `customer_zone` — denies deploy when the target cluster is not in an allowed customer zone.
    - `required_tags` — verifies that every namespace in the built platform artifact carries the configured required labels.
    - `naming_pattern` — validates `meta.name` of the active configuration against a required regex pattern.
    - `script` — delegates to an external script or tool (OPA, Checkov, custom) via subprocess; passes JSON context on stdin and reads violations from stdout/stderr.
  - **Validate phase hook** — `ValidateCommand` evaluates all `phase: validate` policies after structural validation; violations with `enforcement: deny` promote the command to exit code 3.
  - **Build phase hook** — `RunBuildCommand` evaluates all `phase: build` policies after a successful SBOM build step.
  - **Plan phase hook** — `RunDeployCommand` evaluates all `phase: plan` policies after Terraform plan output is available.
  - **Policies in platform artifact** — `PlatformSpecModel.policies` field carries the full policy declaration list into `platform.json` so deploy-time inspection doesn't require the source configuration.
  - **Policy results in deployment manifest** — `ManifestPolicyResultModel` captures per-policy outcomes; `DeploymentManifestSpecModel.policy_results` accumulates them across all evaluated phases, making audit data available in the signed manifest.
- **`PolicyController`** — `get_declared_policies(configuration_service)` extracts the policy list; `get_deployment_phases(deployment_service)` infers which lifecycle phases a deployment's stages can trigger via keyword matching on provisioner and topology names.
- **`strata policy list`** — introspection command that lists every policy declared in the active configuration. Columns: Name, Type, Phase, Enforcement, Enabled. `deny` enforcement highlighted red, `warn` yellow. Summary line reports enabled/disabled counts. Accepts `--file` / `-f` to load a deployment YAML and annotate the output with which lifecycle phases that deployment can trigger. Supports `--output json` / `--output text` / `--output ndjson` for machine consumption.

---

## [0.5.0] — 2026-06-12

### Added

- **SBOM generation** — CycloneDX 1.6 JSON Software Bill of Materials written automatically after every `strata build run`, stored as `sbom.json` alongside `platform.json` in the deployment build directory.
- **`strata build sbom`** — standalone command to regenerate the SBOM from an existing `platform.json` without a full rebuild.
- **Extensible collector pattern** — `BaseSbomCollector` abstract base with four built-in collectors:
  - `ContainerImageCollector` — scans `platform.spec.modules[].services[].image` for container images (`pkg:docker/…` PURLs). Floating tags (`latest`, `main`, `dev`, etc.) are flagged with a `strata:tag-stability=floating` CycloneDX property and a `WARNING` log.
  - `HelmChartCollector` — collects Helm charts from provisioners with `type: helm` (`pkg:helm/…` PURLs, with `repository_url` qualifier when a repository is set).
  - `TerraformProviderCollector` — parses `required_providers {}` blocks from `*.tf` files in the build directory via `python-hcl2` (`pkg:terraform/…` PURLs).
  - `AnsibleCollectionCollector` — reads `requirements.yml` files in the build directory for collections and roles (`pkg:ansible/…` PURLs).
- **`SbomReferenceModel`** — Pydantic model recording the SBOM path, format, SHA-256 digest, and component count. Stored on `DeploymentManifestSpecModel.artifacts.sbom`.
- **`utils/sbom_utils.py`** — pure-function PURL helpers and floating-tag detection with no dependency on the SBOM library (fully unit-testable).
- **`utils/ansible_utils.py`** — shared `find_ansible_requirements_file()` utility used by both `AnsibleCollectionCollector` and `AnsibleDeployer` to eliminate duplicated discovery logic.
- `cyclonedx-python-lib >=7.0,<9` and `packageurl-python >=0.11,<2` added as runtime dependencies.

### Changed

- `DeploymentManifestSpecModel.artifacts.sbom` field typed as `Optional[SbomReferenceModel]` (was `Optional[Dict[str, Any]]`).
- `AnsibleDeployer._get_requirements_file()` now delegates to `find_ansible_requirements_file()` from `utils/ansible_utils`.
- `strata build run` full pipeline now includes SBOM generation as step 6 (after Helm builder).

### Documentation

- Added `SbomBuilder` to `docs/platform/builders.md` — output location, collector table, floating-tag behaviour, three-phase pipeline, and extension guide.
- Added `strata build sbom` to `docs/platform/commands.md`; updated `build run` description to list all pipeline steps.
- Added `sbom_utils.py` and `ansible_utils.py` sections to `docs/platform/utilities.md`.

---

## [0.2.0] — 2026-06-09

### Added

- **`strata env status`** — show live infrastructure status per deployment stage (resources, serial, outputs from `terraform show -json`). Supports `--offline` for cached-only mode. `--all` / `--path DIR` scan multiple deployment manifests and show a one-line summary per deployment (offline/cache-based).
- **`strata env drift`** — detect drift between desired config and live infrastructure using `terraform plan -detailed-exitcode`. Reports create/update/delete/replace counts per stage.
- **`strata values set`** — write a value to its configured store backend. Dispatches to constant (print location), environment (export instruction), github (`gh secret set`), or integration backends. Supports `--value`, `--from-file`, and `--stdin` for multiline input.
- **`strata values resolve`** — diagnose value resolution paths without revealing actual values. Walks the resolution chain with checkpoints per store type. `--probe` flag attempts actual backend resolution (pass/fail only).
- **Environment module overrides** — new `spec.overrides.modules` schema in environment YAML for pinning container images, Helm chart versions, and module enabled state per environment without modifying module definitions.
  - `services` list with `name` + `image` for per-service image pinning.
  - `chart_version` field for Helm chart version overrides.
  - Optional `resource`, `namespace`, `slot_type` qualifiers for scoping when a module appears in multiple places.
  - Specificity-based matching: most specific override wins.
  - Validates: mutual exclusivity of resource/namespace, unique service names.

### Changed

- Renamed internal `commands/env/` package to `commands/envs/` to avoid collision with the reserved word `env`.
- `EnvironmentModuleOverrideModel` redesigned: `module` is now the primary key (required), `resource` is optional (was required).
- `get_module_override()` service method uses specificity scoring instead of exact tuple match.
- `get_overridden_module_keys()` returns `(module, resource, namespace, slot_type)` tuples.
- Deployment service applies module overrides by scanning all matching workspace resources rather than requiring exact resource targeting.

### Documentation

- Added `Overrides` section to `docs/config/environment.md` with full module override schema and examples.
- Added cross-reference in `docs/config/module.md` pointing to environment overrides for image pinning.
- Added `values set` and `values resolve` to `docs/platform/commands.md` and `docs/platform/workflow.md`.
- Created `.archive/releases.md` — release & version management analysis for multi-repo deployment orchestration.

---

## [0.1.1] — 2026-06-07

### Added

- **Cross-module `depends_on`** — `@module/service` syntax for dependencies between modules in the same namespace. Validated at build time. Shorthand `@module` when module name equals service name.
- `strata output` — show Terraform outputs from cache or live backend.

### Changed

- `ComposeBuilder` uses two-pass build: first pass builds a namespace service registry, second pass resolves all `depends_on` refs (intra-module and cross-module).

---

## [0.1.0] — 2026-05-14

_First real release. Everything before this was iterative scaffolding toward a working platform._

### Core Platform

- Layered architecture: `commands/` → `controllers/` → `services/` → `integrations/` → `models/` → `utils/`.
- Pydantic v2 models for all YAML document kinds: workspace, environment, deployment, configuration, resource, namespace, module, provider, firewall.
- Click CLI with flat `strata <group> <command>` structure.
- Exit codes: `0` success, `1` system failure, `2` usage error, `3` validation failure.
- Structured logging via `structlog`. Audit logging subsystem.
- Project renamed from `xyz-platform` to `strata`.

### CLI Commands

- `strata validate` — validate any YAML config file with structured error output.
- `strata build run` / `plan` / `clean` — artifact generation pipeline. `plan` is dry-run.
- `strata deploy run` / `destroy` / `status` / `health` — deployment lifecycle.
- `strata diff` — preview environment changes before deploying.
- `strata sln init` / `clean` / `status` / `export` — workspace lifecycle.
- `strata repo` — register, clone, sync external repositories.
- `strata profile` — manage environment profiles.
- `strata config` — persist CLI preferences to `.strata/cli.yaml`.
- `strata vars` — variable inspection.
- `strata log` — audit log listing and configuration.
- `strata tools` — list and check external integration availability.
- `strata schema` — export JSON schema for all document kinds.
- `--file` / `-f` option across build, deploy, validate commands (`STRATA_FILE` env var).

### Compose Builder

- Multi-service modules — `spec.services` with per-service image, ports, mounts, healthcheck, environment, `depends_on`.
- One `docker-compose.yml` per namespace. All compose modules merged into a single file.
- Service name prefixing: `{module}-{service}` (omitted when equal).
- Pass-through mode — `spec.compose_file` copies an external compose file verbatim.
- Module file copy — `spec.files` with glob, `@repo/` refs, and template substitution.
- `.env` file generated at deploy time with resolved secrets/variables.

### Integrations

- Terraform: `init` / `plan` / `apply` / `destroy` / `output` via subprocess.
- Secret resolution: Bitwarden, Azure Key Vault, HashiCorp Vault, environment variables.

### DevOps

- GitHub Actions composite actions: `setup-strata`, `validate`, `build-run`, `build-plan`, `diff`, `deploy-run`, `deploy-destroy`, `run`.
- CI workflows: `install-python`, `test-python`.
- Docker images: `Dockerfile.cli` (production), `Dockerfile.docs` (documentation site).
- Scaffold templates for `strata sln init --template <name>`.

### Documentation

- Platform docs: architecture, builders, deployers, CLI reference, getting started.
- Operator guides: cookbook, cross-env patterns, troubleshooting, FAQ.
- Config reference: all YAML document kinds.
- Shell completion (Bash, Zsh, Fish).

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
-->
