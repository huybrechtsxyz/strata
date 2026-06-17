# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

- **`strata env state`** — show live infrastructure state per deployment stage (resources, serial, outputs from `terraform show -json`). Supports `--offline` for cached-only mode.
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
