# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
