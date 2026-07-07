# Changelog

All notable changes to this project are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Design & ADR Progress

#### ADR-0013: Auto-generated Secrets — Model Acceptance Criteria Updates
- [x] **Model field**: `SecretStoreModel.rotate: Optional[SecretRotateSpec]` (sibling of `generate:`)
- [x] **Validator 1**: `policy: rotate` without `generate:` → Pydantic validation error
- [x] **Validator 2**: `max_age` is `int` (days, >= 1) with field_validator enforcing range
- [ ] **YAML examples**: All docs examples using integer `max_age` (audit of docs still needed)

**Status**: Model layer 100% complete. Remaining: documentation consistency sweep across ADR-0013 and companion docs.

#### ADR-0011: Promotion Strategies — Phase 3 Design Gaps Identified
Five blocking issues identified for Phase 3 (automation: `start`, `rollback`, `history`):

- **#11** — Phase 1 `status`/`matrix` wrongly describe reading `spec.version`; should read `spec.overrides.remotes[].reference` (correct field)
- **#12** — No deployment discovery mechanism for `promote start --to production`; three options proposed (explicit flags, directory scan, solution registry)
- **#13** — Wave-to-file mapping ambiguous; conflation of `kind: tenant` vs `kind: environment`; three resolution options
- **#14** — Rollback depends on gitignored `.strata/promotions/` activity log; three recovery options proposed
- **#15** — Single-layer configs (`scope: tenant` matches 0 deployments); needs explicit graceful degradation behavior

**Status**: Phase 1 (read-only) and Phase 2 (model + validation) unblocked. Phase 3 deferred pending resolution of #11–#15.

#### ADR-0018: SIEM Integrations — Layer 4 Completed
- [x] **ELK Syslog Integration** (`ElkSiemIntegration`) — dual-protocol: TCP (Logstash) + HTTP (Elasticsearch bulk)
- [x] **OpenTelemetry Integration** (`OtelSiemIntegration`) — OTLP/HTTP JSON; no SDK dependency needed
- [x] **Factory registration** — both types registered as `"elk"` and `"otel"`
- [x] **Tests** — full coverage in `test_elk_siem_integration.py` and `test_otel_siem_integration.py`

**Design note**: Uses integration-reference model (`integration: <name>` in `AuditSinkModel`) rather than built-in sink types. Both can forward to same ELK stack independently of `LogstashHandler` (operational logs via TCP vs. compliance audit events via HTTP).

#### ADR-0022: CEF Syslog Format — Implementation Complete
- [x] **Model field**: `AuditSinkModel.format: Optional[str]` — syslog sink accepts `"json"` (default) or `"cef"`
- [x] **CEF encoder** — `AuditController._format_cef(data)` → CEF:0 header + 6-field extension (rt/src/dst/act/externalId/msg)
  - Severity: `3` (Low) on success, `7` (High) on failure
  - Proper escaping of pipes/backslashes per CEF spec
- [x] **Syslog routing** — `_send_syslog(data, address, fmt)` routes to formatter based on `fmt` parameter
- [x] **CLI flag** — `--siem <name>` on `strata audit export` for on-demand SIEM forwarding by integration name
- [x] **Tests** — `test_syslog_sink_passes_cef_format`, `TestFormatCef` class (header, severity, escaping, extension fields)

**Status**: Ready for v1.0. CEF output validated against CEF 0 specification.

---

## [v0.16.1] — 2026-06-15

### Features
- Enhanced deployment audit trail with Layer 3 (`strata audit` commands) fully operational
- Deployment lock backends expanded: local, azurerm, TFC, Consul, S3, GCS (6 total)
- All 27 lifecycle phases (`deploy_before`, `deploy_after`, etc.) wired and operational
- Secret store integrations: Azure Key Vault, Vault, Consul, Bitwarden, Flagsmith
- Terraform build output modes: all 5 output variants + backend var resolution
- SBOM generation with dependency lockfile scanning (Python, Node, Go, .NET, Java, Ruby, Rust, PHP)
- Pluggable provisioner framework: Terraform, Helm, Compose, Ansible, Scripts
- Jinja2 template processing: dual-mode (strict/lenient), conditionals, loops
- Console REPL with guide controller (18/21 onboarding items)

### Fixed
- Deployment service layer validation: stage `provisioner` and `topology` names now validated against workspace declarations
- Lock factory backends: replaced `NotImplementedError` for s3/gcs with clear `PlatformConfigurationError` + fix suggestions

### Documentation
- ADR-0010 implemented: repositories → remotes rename complete
- All ADR statuses updated: 14 accepted, 4 deferred, remainder in active implementation

### Schema
- Frozen `apiVersion: strata.huybrechts.xyz/v1` (hidden alias `strata.omp.com/v1` accepted)
- 15 YAML kinds: 12 user-facing, 2 internal (`platform_model`, `deployment-manifest`), 1 solution

### Tests
- ~3950 passing tests across all layers
- Full coverage for model validation, services, controllers, CLI commands
- Integration tests for lock backends, SIEM forwarding, provisioner dispatch

---

## [v0.15.0] — 2026-04-10

### Features
- State locking: local, azurerm, Terraform Cloud backends
- Policy engine: 12 built-in policies with validation framework
- Deployment manifests: build + deploy artifacts with compliance audit trail
- Environment composition: flat merge from environment files with last-wins semantics
- Deploy-log (Layer 2): JSON event storage with file discovery and filtering
- Configuration remotes (repositories): multi-source remote YAML loading

### Documentation
- ADRs 0001–0010 finalized and accepted
- Initial architectural decision records for all major components

---

## [v0.14.0] — 2026-02-20

### Features
- Core deployment orchestration: `strata deploy run`
- Terraform provisioner integration with state management
- Secret value resolution from multiple stores
- Workspace model: YAML schema for infrastructure definitions
- Configuration service: loads and merges YAML files
- Build command: `strata build run` with artifact generation
- Basic CLI structure: command groups, context management

### Documentation
- Initial README and getting-started guide
- Schema documentation for all 6 foundational kinds

---

## [v0.1.0] — 2025-11-01

### Initial Release
- CLI framework: Click-based command structure
- Pydantic models for YAML validation
- Basic logging infrastructure
- Foundation for extensible architecture

---

## Development Status

### Shipping in v1.0
- Core deployment workflow (build → validate → deploy)
- All 27 lifecycle phases
- 6 lock backends
- 22 integrations (stores, provisioners, SIEM)
- 28 CLI command groups
- Audit trail + SIEM forwarding (Splunk, Sentinel, ELK, OTel)
- CEF syslog format

### Post-v1.0 Backlog
| Feature | Hours | Priority |
|---------|-------|----------|
| ADR-0008 Drift detection | 16–20h | Medium |
| ADR-0011 Promotion Phase 3 | 10–14h | Low |
| ADR-0013 Secret rotation | 8–12h | Medium |
| ADR-0014 Onboarding (6 items) | 20–30h | High |
| ADR-0017b Release UX | 8–12h | Low |
| ADR-0024 Provenance tracing | 4–6h | Low |

**Total estimated backlog: ~134–184 hours** (2.5–3.5 engineer-months)

---

## License

Licensed under the MIT License. See LICENSE file for details.
