# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

> ⚠️ **Versioning note:** The project is currently at `0.0.1`. The sections below are structured
> as if they were releases to communicate the scope and intent of each change batch clearly.
> Formal version tags and release artifacts will follow once the adoption-readiness branch is merged.

---

## [Unreleased]

_Changes staged on `adoption-readiness` not yet merged to `main`._

### Added

- `strata diff` — shows what would change in the environment before deploying (artifact diff + terraform plan). Read-only; nothing is modified.
- `strata log` command group (renamed from `audit`) with `list` subcommand and `config` subcommand for log configuration.
- `strata sln` command group consolidating workspace lifecycle: `init`, `clean`, `status`, `export`.
- `strata vars` command group (renamed from `context`) for variable inspection.
- `--file` / `-f` option across all build, deploy, and validate commands with `STRATA_FILE` env var support for persisting a default deployment file.
- `strata build plan` dry-run mode: previews generated artifacts without writing to the build path.
- Audit logging subsystem (`strata.logger.audit`) — structured log of every user action and outcome, separate from application logs.
- GitHub Actions composite actions for CI integration: `setup-strata`, `validate`, `build-run`, `build-plan`, `diff`, `deploy-run`, `deploy-destroy`, `run`.
- Scaffold templates for `strata sln init --template <name>` — AKS-flavored starter config.
- Operator-facing documentation guides: `docs/guides/cookbook-add-environment.md`, `pattern-cross-env-changes.md`, `troubleshooting-what-changed.md`, `faq.md`.
- `docs/platform/value-proposition.md` — escape hatch documentation, provisioner-agnostic design, and the "walking away" guide.
- `docs/platform/ci-integration.md` — exit codes, `--output json`, and pipeline integration examples.
- Shell completion documentation in `getting-started.md` (Bash, Zsh, Fish).

### Changed

- Project renamed from `xyz-platform` to `strata` (repo, package, CLI entrypoint, env var prefix).
- `auto_envvar_prefix` corrected to `"STRATA"` (was stale `"XYZ"`).
- `_DEFAULT_MAP_KEYS` in `cli.py` extended to include `"file"` so `strata config set file` persists the default deployment file.
- `commands/log/` renamed to `commands/logger/` to avoid `.gitignore` conflict with `[Ll]og/` pattern.
- Cross-reference added in `strata log --help` pointing to `strata config log` for log configuration.
- `BaseBuildCommand` and `BaseDeployCommand` now log `"Using deployment file"` at `INFO` level after file resolution.

### Removed

- `strata context` command group — replaced by `strata vars`.
- `strata audit` command group — replaced by `strata log`.
- Stale `cli_context.py` and `test_commands_context.py` files.
- Obsolete rename scripts (`Rename-Layer*.ps1`).

---

## [0.0.1] — 2025-07-16

_Initial working version. Establishes the core architecture and CLI skeleton._

### Added

- Core architecture layers: `commands/` → `controllers/` → `services/` → `integrations/` → `models/` → `utils/`.
- Pydantic v2 models for all YAML document kinds: `workspace`, `environment`, `deployment`, `configuration`, `resource`, `namespace`, `module`, `provider`, `firewall`.
- Click CLI with flat `strata <group> <command>` structure and `auto_envvar_prefix`.
- `strata validate` — validates any YAML config file with structured error output.
- `strata build run` / `strata build plan` / `strata build clean` — artifact generation pipeline.
- `strata deploy run` / `strata deploy destroy` / `strata deploy status` / `strata deploy health` — deployment lifecycle.
- `strata repo` — register, clone, sync external repositories.
- `strata profile` — manage environment profiles.
- `strata config` — persist CLI preferences to `.strata/cli.yaml`.
- `strata tools` — list and check external integration availability.
- `strata schema` — export JSON schema for all YAML document kinds.
- Terraform integration via subprocess with `terraform init / plan / apply / destroy / output`.
- Secret resolution from Bitwarden, Azure Key Vault, HashiCorp Vault, environment variables.
- Structured logging via `structlog`, configurable via `strata config log`.
- Exit codes: `0` success, `1` system failure, `2` usage error, `3` validation failure.
- Dev container for `xyz-configuration` repo (Python 3.13, Terraform, Azure CLI, kubectl, Helm).
- YAML JSON schemas for all document kinds, wired to VS Code via `.vscode/settings.json`.
- CI workflows: `install-python`, `test-python` composite actions.

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
-->
