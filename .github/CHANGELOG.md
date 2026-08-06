# strata — Changelog

Concise, user-facing summary of each release. Full implementation detail (per-phase notes,file/method names, bug-fix specifics) lives in [HISTORY.md](./HISTORY.md). Design rationale lives in the ADRs under [docs/decisions/](../docs/decisions/).

This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Breaking Changes

- **Cost estimation now requires an `infracost` integration declaration** — `strata cost show`, `strata cost diff`, and the automatic post-plan cost diff in `deploy run --dry-run` previously worked off of any `infracost` binary found on PATH, regardless of `configuration.yaml`. They now require an explicit `infracost` entry under `spec.integrations` (with `capabilities: [cost]`, `enabled: true`), matching how every other integration (secret stores, provisioners) is gated. An installed binary with no declaration no longer does anything. `strata cost history` is unaffected — it reads past `cost show` snapshots and needs no estimator.

### Added

- **Git ref pinning on `SourceModel` (ADR-0063, Gap 1)** — Provisioner sources now accept an optional `reference` field (branch, tag, or commit SHA) that overrides the workspace-level remote default. This allows two provisioners referencing the same remote to pin different versions (e.g., platform baseline on `v1.4.0` and VCT module on `main`). Resolution priority: `source.reference` → environment remote override → remote default. When a ref is pinned, `git archive` extracts the subtree without mutating the working tree.
- **Structured variable types (ADR-0063, Gap 2)** — `VariableStoreModel` now accepts an optional `type` field (`string`, `number`, `bool`, `object`, `list`, `map`) that declares the intended HCL type. When set, strata validates that the YAML value matches the declared type and emits it as a native JSON type in `.auto.tfvars.json` instead of always stringifying. Complex values can now be authored as native YAML mappings/sequences instead of JSON-in-string.

---

## [1.6.1] - 2026-08-03

### Added

- **SQLite-backed resolved-model cache — `strata cache` (ADR-0026)** — deployments' resolved model is now cached keyed by a hash of its source YAML files, so `build run`/`build plan`/`policy check` don't need to re-resolve unchanged deployments. New `strata cache warm/status/clear/export` commands; the affected build/policy commands auto-warm on success (`--no-cache-warm` to opt out). The VS Code extension warms the cache in the background on save and startup. See ADR-0026.

### Fixed

- **`deploy run`/`deploy destroy` exit codes for invalid deployments** — a deployment file that fails schema/cross-reference validation now correctly exits 3 (was 1); a missing/unresolvable deployment file now exits 2. See ADR-0004.

### Security

- **Secret/variable/feature store outages no longer conflated with "not found"** — integrations (Infisical, HashiCorp Vault/OpenBao, Bitwarden, Azure Key Vault) now raise a new `SecretStoreUnavailableError` on connectivity/auth failures instead of returning `None`. Previously a transient outage could be mistaken for a missing secret, triggering silent auto-generation of a fresh value (for `generate:` secrets) or letting a deploy proceed with a blank value. `deploy run` now aborts instead of continuing when a store is unavailable.
- **Pre-flight availability checks for secret stores and provisioners** — `deploy run` now verifies all referenced secret/variable/feature stores and required provisioner tools (terraform, ansible, etc.) are reachable before resolving any values or acquiring the deployment lock, failing fast instead of partway through a multi-stage deploy.

---

## [1.6.0] - 2026-07-31

### Breaking Changes

- **`env` command group removed (ADR-0062)** — its six commands are redistributed: `env show`/`env output`/`env drift` merge into the existing `deploy` equivalents, `deploy status` is revived with corrected live-state behavior, `env status --all/--path` becomes the new `rollout status` group, and `env info`/`env doctor` move to `sln status`/`sln doctor`. No deprecation shim — update any `strata env ...` usage in scripts, pipelines, or MCP tool calls.
- **Unified `spec.gates` schema (ADR-0059)** — `spec.approvals`/`approvers` and the separate ADR-0057 `spec.gates` block are merged into a single deployment-level `spec.gates` list with typed approver refs (`github-team`/`user`/`ado-group`). Existing YAML using either old shape fails validation; see ADR-0059 for the field-by-field migration mapping.

### Fixed

- **`ref_convention` policy / `strata repo status` design drift (ADR-0017)** — tag naming conventions now live in one place, `spec.remotes[].conventions` (`RemoteConventionsModel`), instead of being duplicated inside the policy's own config. `repo status` no longer guesses release/quality tags from hardcoded name prefixes — it links a local repo to its configured remote by comparing normalized git remote URLs and only classifies tags when that remote declares `conventions`. No backward compatibility with the old policy-level `configuration.remotes[]` shape (never released/documented before this fix).

### Changed

- Subprocess execution consolidated onto a single `run_command()` path with consistent SIGTERM handling and timeout parity across builders, deployers, and controllers (ADR-0061).

## [1.5.0] - 2026-07-27

### Added

- Deployment workflow orchestration — approval/cost/security gates, work-item lifecycle (`strata workitem`), `deploy run --resume`, exit code 5 for hand-off. See ADR-0057.
- Comprehensive help documentation for all 12 platform YAML kinds; `docs/help/` is now the single source of truth, synced to the CLI and VS Code extension at build time.
- AI agent integration — advisory LLM analysis across build/deploy/validate/policy/audit commands via `--ai`, plus VS Code chat participant commands (`/review`, `/diagnose`, `/sbom`). See ADR-0025.

## [1.4.0] - 2026-07-24

### Added

- `--timeout` for `deploy run`/`deploy destroy` (ADR-0027) and SIGTERM graceful shutdown (ADR-0028).
- Cloud CLI integrations + lifecycle scripts for GCP (ADR-0055), AWS, and Azure.
- Scoped multi-scheme layering (ADR-0042), path convention validation (ADR-0052), Checkov IaC security scanning (ADR-0051), Bicep provisioner (ADR-0046), Azure CLI integration (ADR-0053), OPA policy integration (ADR-0050).
- UTC datetime standardization across all timestamps (ADR-0045).

### Changed

- Deployment layers no longer require the final layer to be named `"environment"`; `spec.layering` deprecated in favor of `spec.layerings`.

### Breaking Changes

- Removed hardcoded final-layer-name constraint — review configs that relied on it.

## [1.3.1] — 2026-07-22

### Added

- Cost estimation via Infracost — `strata cost show/diff/history`, `cost_threshold` policy (ADR-0031 Phase 1).
- VS Code extension reworked around deployment-centric UX — Deployments/Operations views replace the flat Files view.

### Changed

- Removed unused provider `engine` and resource `unit_cost` fields.

## [1.2.1] — 2026-07-20

### Changed

- `strata new --output-file` (was `--path`); `strata validate run --pattern` (was `--path`).
- Exit code 4 for deployment lock conflicts.

### Fixed

- `strata secret mask` positional-argument dash handling; Sphinx docs build warnings.

## [1.2.0] — 2026-07-16

### Added

- GitOps controller integration — `argocd`/`flux` provisioner types with reconciliation health checks (ADR-0041).
- Platform artifact convenience fields — name, labels, revision, resolved_variables, chart/image versions.

### Changed

- `WorkspaceIacModel.source` is now optional for sync-type provisioners.

## [1.1.1] — 2026-07-15

### Added

- `strata new --validate` — validates generated files immediately after creation.

### Changed

- Command lifecycle migrated to `_execute()` across the entire command layer; `execute()` sealed on `BaseCommand` (ADR-0030).

## [1.1.0] — 2026-07-14

### Added

- Environment provider overrides — per-environment provider file/configuration swaps (ADR-0036).
- Promotion strategy framework groundwork (ADR-0011); tenant scaffolding bundle template (`strata new tenant`).

## [1.0.1] — 2026-07-09

### Added

- Tenant `spec.environments`/`spec.properties`/`spec.custom` now applied at build time.
- `sln deployment` subcommand group (`add`/`remove`/`list`/`scan`).

### Fixed

- Tenant `spec.environments` was validated but never merged into the build pipeline.

## [1.0.0] — 2026-07-08

### Added

- S3 distributed lock backend (ADR-0007).
- VS Code extension reached CLI feature parity — Values Inspector, lock status/release, drift detection, SBOM generation, stage-targeted deploy, repository write operations.
- Umbrella JSON schema (`strata.json`) — a single schema entry replaces 12 per-kind entries.

### Changed

- Unified exception handling and structured logging across all commands.

## [0.16.1] — 2026-07-06

### Fixed

- `uv sync --group doc` dependency group resolution.

## [0.16.0] — 2026-07-05

### Added

- Environment composition — flat merge now covers all 8 spec sections with provenance tracking (`values list --trace`) (ADR-0024).

## [0.15.0] — 2026-07-03

### Added

- Configurable Terraform build output profiles (ADR-0019).
- `strata env status` (renamed from `env state`); VS Code Environments and Audit Trail panels.

## [0.14.0] — 2026-06-26

### Added

- Deployment audit and traceability — SIEM sinks for Sentinel, ELK, and OpenTelemetry (ADR-0018).

## [0.13.0] — 2026-06-24

### Added

- Guided onboarding experience — `strata console` REPL, `validate graph`/`--explain`, batch validation (ADR-0014).

## [0.12.0] — 2026-06-23

### Added

- Auto-generated secrets and seed-on-missing for variables/features (ADR-0013).

## [0.11.0] — 2026-06-23

### Added

- Promotion strategies system — waves, progressions, `strata promote` (ADR-0011).

### Changed — Breaking

- Renamed `customer` → `tenant` throughout the platform (ADR-0012). See migration guide in [HISTORY.md](./HISTORY.md).

## [0.10.0] — 2026-06-22

### Added

- `strata deploy show`/`list`, bundle templates for `strata new`, cross-manifest overlap validation (`validate --path`), remote reference overrides per environment.

## [0.9.3] — 2026-06-20

### Added

- Helm module build/deploy improvements — chart coordinates in `meta.yaml`; `--wait`/`--atomic` on `helm upgrade`.

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder no longer writes empty variable files for unused sections.

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings are silenced by default (use `--verbose` to see them).

## [0.9.0] — 2026-06-18

### Breaking Changes

- Configuration YAML: `spec.repositories` renamed to `spec.remotes` (ADR-0010).

### Added

- Self-SBOM generation in CI; SBOM attached to every GitHub Release.

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow dependency group and tag-trigger condition.

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for documentation.

### Fixed

- Topology volume `type` field restriction removed.

## [0.8.0] — 2026-06-17

### Added

- CVE audit (`strata build sbom --audit`) via Trivy/Grype; `sbom_license` policy; 6 new lockfile parsers (NuGet, Maven, Gradle, Gemfile, Cargo, Composer); `.strata/lockfile_parsers/` auto-discovery.

## [0.7.0] — 2026-06-16

### Added

- `strata policy check` standalone command; `strata deploy outputs`; deploy-phase policy hook.

## [0.6.0] — 2026-06-15

### Added

- Policy engine — declarative guardrails at validate/build/plan phases (`customer_zone`, `required_tags`, `naming_pattern`, `script` policy types); `strata policy list`.

## [0.5.0] — 2026-06-12

### Added

- SBOM generation (CycloneDX 1.6) — `strata build sbom`; container image, Helm, Terraform provider, and Ansible collection collectors.

## [0.2.0] — 2026-06-09

### Added

- `strata env status`/`drift`, `strata values set`/`resolve`, environment module overrides (image/chart pinning per environment).

## [0.1.1] — 2026-06-07

### Added

- Cross-module `depends_on` (`@module/service` syntax); `strata output`.

## [0.1.0] — 2026-05-14

_First real release._

- Layered architecture, Pydantic v2 models for all YAML kinds, Click CLI, exit codes 0-3, structured logging.
- Core commands: `validate`, `build`, `deploy`, `diff`, `sln`, `repo`, `profile`, `config`, `vars`, `log`, `tools`, `schema`.
- Terraform subprocess integration; secret resolution (Bitwarden, Azure Key Vault, HashiCorp Vault, env vars).

---

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
- Keep entries to 1-3 lines per feature, referencing the ADR for detail.
  Full narrative detail goes in HISTORY.md only if truly needed for archaeology.
-->

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes
