# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.13.0] — 2026-06-24

### Added

- **Guided Onboarding Experience (ADR 0014)**
  - `strata console` — interactive REPL session with prompt_toolkit (status, check, next, do, new, validate, graph, tools, open, reload, templates, help)
  - `GuideController` extracted from `GuideCommand` for stateful workspace analysis
  - `strata validate graph` — Mermaid dependency graph visualization with live validation status
  - `strata validate --explain` — plain-English summary of what a validated file does
  - Validation error fix suggestions with "did you mean?" for misspelled fields
  - `strata validate --path "**"` — batch validation of all workspace YAML
  - `strata sln init --list` — discover available init templates
  - `strata new --list` — shows bundles with descriptions
  - Rich rendering (panels, tables, progress indicators) in guide REPL
  - Standalone LLM skill file (`docs/skills/strata-onboarding.md`) bundled into init scaffold at `.github/skills/`
  - CI template validation test — all built-in templates validated on every run
  - `config/` formalized as reference example workspace with README annotations and CI validation
  - Contributing guide section for adding community example workspaces

### Fixed

- Template bundles: replaced invalid `type:` fields on stages with `provisioner:` (Pydantic `extra="forbid"` compliance)
- Validation error messages: `extra_forbidden` now names the offending field
- Template scaffold: `deploy.yml` wrapped in `{% raw %}` to prevent Jinja2 conflicts with GitHub Actions `${{ }}` expressions
- Template scaffold: `_substitute()` now handles both `${key}` and `{{ key }}` placeholder syntax
- Model tests: updated references from deleted `config/xyz-configuration/` to new cloud-provider examples

### Documentation

- Added ADR 0014 (Guided Onboarding and Cold-Start Experience)
- Updated CONTRIBUTING.md with example workspace contribution guidelines

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
  - Renamed concept: `customer` → `tenant` (platform-standard term, inclusive for internal teams, personal projects, edge deployments)
  - Kind: `customer` → `tenant`
  - Model: `CustomerModel` → `TenantModel`
  - Service: `CustomerService` → `TenantService`
  - Policy: `customer_zone` → `tenant_zone`
  - Directory: `customers/` → `tenants/`
  - Field: `spec.customer` → `spec.tenant`
  - Properties: `properties.customer` → `properties.tenant`
  - Terraform variables: `customer.auto.tfvars.json` → `tenant.auto.tfvars.json`
  - Ansible hostvars: `strata_customer` → `strata_tenant`

### Migration Guide (v0.10.0 → v0.11.0)

1. **Rename your config directories:**
   ```bash
   mv customers/ tenants/
   ```

2. **Update your YAML files:**
   ```yaml
   # Before
   apiVersion: strata.huybrechts.xyz/v1
   kind: customer
   spec:
     customer: acme
   
   # After
   apiVersion: strata.huybrechts.xyz/v1
   kind: tenant
   spec:
     tenant: acme
   ```

3. **Update deployment manifests:**
   ```yaml
   # Before
   spec:
     properties:
       customer: acme
   
   # After
   spec:
     properties:
       tenant: acme
   ```

4. **CLI commands — Coming in v0.11.1:**
   - `strata customer` → `strata tenant` (will be implemented)
   - For now, use the YAML/config approach above

### Fixed

- None

### Deprecated

- None (clean break — no deprecation period)

### Removed

- None

### Security

- None

### Infrastructure

- None

### Documentation

- Updated all platform docs to reflect tenant terminology
- Added ADR 0011 (Promotion Strategies)
- Added ADR 0012 (Rename Customer → Tenant)
- Updated at-scale.md with multi-tenant design patterns using tenant terminology
- Updated CLI command documentation (pending actual CLI implementation)

### Testing

- All existing tests updated to use tenant terminology
- 3037 tests passing
- Lint, format, and type checks: all passing
- Sphinx docs build: successful

---

## [0.10.0] — Previous Release

[Previous features and changes documented here...]

---

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes
