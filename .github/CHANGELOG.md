# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
