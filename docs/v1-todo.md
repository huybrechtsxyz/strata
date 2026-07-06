# strata v1.0 — Release Checklist

> Goal: freeze the public contract, clean up rough edges, ensure the tool is usable end-to-end.
> Features that are designed but not blocking usability are deferred to post-v1.

---

## Must-Have for v1.0

### 1. Schema & API Contract

- [x] **Freeze `apiVersion`** — canonical is `strata.huybrechts.xyz/v1`; keep `strata.omp.com/v1` as a hidden alias (accepted in validation, not advertised in docs/schema)
- [x] **Mark internal kinds** — `platform_model` and `deployment-manifest` stay in the schema registry but must be annotated as `internal: true` in `strata schema list` output and docs. Users writing lifecycle scripts may inspect these schemas but should not author YAML files with these kinds
- [x] **Verify schema export** — `strata schema get <kind>` already works for all 14 kinds via Pydantic `model_json_schema()`. Verify output is clean for all kinds; no code change expected
- [x] **ADR-0010: rename repositories → remotes** — already implemented: `spec.remotes` in model/services/docs, `RemoteModel`/`get_remote_map()` in code, backward compat alias retained

### 2. Code Cleanup

- [x] **deployment_service.py TODOs (lines ~1053, ~1058)** — implemented: stage `provisioner` and `topology` names are now validated against `workspace.spec.provisioners[]` and `workspace.spec.topology[]`; error message lists available names
- [x] **lock_factory.py remote backends** — replaced `NotImplementedError` with `PlatformConfigurationError` for `s3` and `gcs` backends; error message lists supported backends and suggests disabling locking; tests updated
- [x] **TODO sweep** — only the 2 deployment_service.py TODOs existed (now resolved above). 3 template scaffolding TODOs in `my_lockfile_parser.py` are intentional. No FIXME/HACK/XXX markers. **Codebase is clean.**

### 3. Documentation

- [ ] **ADR status sweep** — update every ADR to either **accepted** or **deferred**:

  | ADR                          | Current Status | Action for v1                                                                                                                                                |
  | ---------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
  | 0001–0005                    | accepted       | No change                                                                                                                                                    |
  | 0006 Policy engine           | implemented    | Mark **accepted** — fully implemented: 12 built-in policies, CLI commands, all hook points wired                                                             |
  | 0007 State locking           | implemented    | Mark **accepted** — 6 backends (local, azurerm, tfc, consul, s3, gcs); s3/gcs added in v1                                                                    |
  | 0008 Drift detection         | in design      | Mark **deferred** — not implemented                                                                                                                          |
  | 0009 SBOM extended           | active         | Mark **accepted** — core collectors done                                                                                                                     |
  | 0010 Rename repos→remotes    | proposed       | Mark **accepted** — implement for v1                                                                                                                         |
  | 0011 Promotion strategies    | proposed       | Mark **deferred** — not implemented                                                                                                                          |
  | 0012 Customer→tenant         | implemented    | Mark **accepted** — TenantModel complete; deployed in v0.11.0; no backward compat by design                                                                  |
  | 0013 Auto-generated secrets  | implemented    | Mark **accepted** — core 100% complete (seed-on-missing, all store types); 5/7 integrations; rotation deferred                                               |
  | 0014 Onboarding              | accepted       | Mark **accepted** — 18/21 items done (console REPL, guide controller, flow graph, batch validate, error hints). Only init wizard (phase 3) remains → post-v1 |
  | 0015 Dependency graph        | implemented    | Mark **accepted**                                                                                                                                            |
  | 0016 Console REPL            | accepted       | **KEEP** — Core REPL production-ready for v1. Only init wizard (Phase 3) deferred to post-v1                                                                 |
  | 0017 Jinja2 templates        | accepted       | **KEEP** — 100% complete: dual-mode processor (strict/lenient), conditionals, loops all working                                                              |
  | 0017b Tag-based release      | proposed       | **MARK DEFERRED** — Git infrastructure complete; CLI UX/docs enhancements post-v1                                                                            |
  | 0018 Audit traceability      | accepted       | **KEEP** — Layers 2–3 production-ready (deploy-log JSON, audit CLI). Layer 4 (SIEM) extensible post-v1                                                       |
  | 0019 Terraform build output  | accepted       | **KEEP** — 100% complete: all output modes, backend var resolution, security controls wired and tested                                                       |
  | 0020 Lifecycle phases        | accepted       | **KEEP** — All 27 lifecycle phases + STRATA_* env vars complete for v1. Only config_fetch/config_clean deferred                                              |
  | 0021 Deployment manifests    | accepted       | **KEEP** — Build + deploy manifests fully implemented; compliance audit trail ready for v1.0                                                                 |
  | 0022 SIEM Splunk             | accepted       | **KEEP CORE** — Splunk HEC integration shipped. CEF syslog format deferred to post-v1                                                                        |
  | 0023 Pluggable provisioners  | proposed       | **MARK DEFERRED** — Plugin discovery not implemented; 5 built-in provisioners (terraform, ansible, helm, compose, script) sufficient for v1                  |
  | 0024 Environment composition | proposed       | **MARK ACCEPTED** — Merge + provenance shipped in v0.16.0. Only --trace flag deferred to post-v1                                                             |

- [ ] **commands.md gaps** — add documentation sections for these 6 undocumented command groups:
  - `env` — info, output, show, status, drift, doctor
  - `service` — list, status, deploy, destroy
  - `policy` — list, check
  - `manifest` — list, show, export
  - `mcp` — serve
  - `console` — interactive REPL
  - Also: add `sbom` under `build` subcommands; clarify `deploy lock` subcommands (status, release, history)

- [ ] **Getting Started guide gaps** — the existing guide covers cold-start through first deploy. Add:
  - `strata values list` — inspecting resolved variables/secrets before deploy
  - `strata deploy run --dry-run` — explicit dry-run workflow
  - Brief troubleshooting section (common errors, `strata audit list --last`)

- [ ] **ADR-0014 / ADR-0020 TBD items** — mark remaining TBD items as explicitly "post-v1": init wizard, explain flag, fix suggestions (0014); config_fetch/config_clean phases (0020)

### 4. ~~ADR-0010 Implementation: repositories → remotes~~ ✅ Complete

All changes were already implemented prior to this checklist:

- `ConfigurationModel.spec.remotes` — field renamed, `RemoteModel` class, `RemoteType` enum
- `ConfigurationService.get_remotes()` / `get_remote_map()` — methods renamed
- `@repo_name/path` resolution — unchanged (uses repo_map dict, not field name)
- `strata repo` CLI group — kept as-is (manages solution source repos, a different concept)
- All config docs reference `spec.remotes`
- Backward compat alias: `RepositoryModel = RemoteModel` retained in `repository_model.py`

---

## Post-v1.0 Deferred Features

These are designed (ADRs exist) but explicitly deferred beyond v1. Core infrastructure is stable; gaps are enhancements or integrations.

**ADR-level gaps (by ADR):**
- ADR-0008: Infrastructure drift detection — full design, no implementation
- ADR-0011: Promotion strategies — full design, no implementation
- ADR-0014 item 10: Console init wizard — Phase 3 only; core REPL already production-ready
- ADR-0017b: Tag-based release — git tag infrastructure done; CLI visibility/UX enhancements post-v1
- ADR-0018 Layer 1: PR template generation tooling — by design, user-provided in `.github/pull_request_template.md`
- ADR-0018 Layer 4: SIEM integrations — Sentinel partially done; Splunk/ELK/OpenTelemetry post-v1
- ADR-0020: `config_fetch` / `config_clean` lifecycle phases — full design, no implementation (all other 27 phases complete)
- ADR-0022: CEF syslog format — design complete; Splunk HEC core ships in v1.0
- ADR-0023: Pluggable provisioner framework — full design, no implementation; 5 built-in provisioners sufficient for v1
- ADR-0024: `--trace` flag for merge provenance — design complete; core merge + provenance tracking ships in v1.0

**Feature-level backlog:**
- Progressive dependency scaffolding (ADR-0014 Phase 5)
- Auto-refresh REPL mode (ADR-0014 Phase 5)
- Template marketplace / community templates (ADR-0014 Phase 5)
- Controller-level and CLI command-level test expansion
- CEF syslog output for SIEM compatibility (ADR-0022 Phase 2)
- Secret rotation (Phase 3, ADR-0013) — 5/7 integrations, design complete
- Missing integrations for value seeding (ADR-0013: Vault, Consul, Flagsmith `set_*` methods)
- Build plan seed status display (ADR-0013: values tracked, display missing)

---

## Metrics (Current State)

| Area               | Count                                                |
| ------------------ | ---------------------------------------------------- |
| CLI command groups | 28                                                   |
| YAML model kinds   | 15 (12 user-facing, 2 internal, 1 solution)          |
| Services           | 20                                                   |
| Controllers        | 18                                                   |
| Builders           | 5 provisioner types + SBOM + platform                |
| Deployers          | 5 provisioner types                                  |
| Integrations       | 22                                                   |
| Lock backends      | 6 implemented (local, azurerm, tfc, consul, s3, gcs) |
| ADRs               | 26 (14 accepted, 4 deferred, 8 to update)            |
| Tests              | ~3750 passing                                        |
| Production TODOs   | 2 (both in deployment_service.py)                    |
| Undocumented cmds  | 6 command groups missing from commands.md            |

---

## Post-v1.0 GitHub Issues for Deferred Work

Track all deferred features via GitHub issues for post-v1.0 prioritization and planning:

### ADR-0008: Infrastructure Drift Detection

- [ ] **#XXX** — [ADR-0008] Implement infrastructure drift detection (`strata env drift`) — full implementation with plan-based detection and detailed change reporting

### ADR-0011: Promotion Strategies

- [ ] **#XXX** — [ADR-0011] Implement promotion strategies for version progression across environments

### ADR-0013: Auto-generated Secrets

- [ ] **#XXX** — [ADR-0013] Implement secret rotation (Phase 3) — periodic key regeneration and vault updates
- [ ] **#XXX** — [ADR-0013] Add missing integrations: Vault, Consul, Flagsmith `set_secret` / `set_variable` / `set_feature` methods
- [ ] **#XXX** — [ADR-0013] Display seed-on-missing status in `strata build plan` — show which secrets/variables were auto-generated

### ADR-0014: Onboarding Experience

- [ ] **#XXX** — [ADR-0014 item 10] Implement `init` wizard inside `strata console` REPL (Phase 3) — Q&A flow for guided workspace scaffolding
- [ ] **#XXX** — [ADR-0014 item 15] Implement interactive `strata new` in REPL — conversational scaffolding prompts
- [ ] **#XXX** — [ADR-0014 item 17] Implement `strata env doctor` health check command — workspace diagnostics with fix suggestions
- [ ] **#XXX** — [ADR-0014 item 19] Implement progressive dependency scaffolding — auto-create missing referenced files during `strata new`
- [ ] **#XXX** — [ADR-0014 item 20] Implement auto-refresh REPL mode (`strata console --auto`) — live polling on workspace file changes
- [ ] **#XXX** — [ADR-0014 item 21] Implement template marketplace / community templates — registry for `strata sln init --template https://...`

### ADR-0017b: Tag-based Release Workflow

- [ ] **#XXX** — [ADR-0017b] Enhance CLI UX for tag-based release workflow — `strata repo tag` commands, validation heuristics, documentation

### ADR-0018: Deployment Audit Traceability

- [ ] **#XXX** — [ADR-0018 Layer 4] Extend SIEM integrations — Splunk CEF syslog format, ELK, OpenTelemetry exporters

### ADR-0020: Lifecycle Phases

- [ ] **#XXX** — [ADR-0020] Implement `config_fetch` lifecycle phase — pre-fetch hooks for remote config optimization
- [ ] **#XXX** — [ADR-0020] Implement `config_clean` lifecycle phase — companion to `config_fetch` for cleanup

### ADR-0022: SIEM Integration (Splunk)

- [ ] **#XXX** — [ADR-0022] Implement CEF syslog format for SIEM — structured event format for security monitoring

### ADR-0023: Pluggable Provisioners

- [ ] **#XXX** — [ADR-0023] Implement pluggable provisioner framework — third-party provisioner discovery, registration, and loading

### ADR-0024: Environment Composition

- [ ] **#XXX** — [ADR-0024] Implement `--trace` flag for merge provenance — interactive visualization of multi-file environment merges

---

## Post-v1.0 Backlog Summary

| Category                                             | Total Hours | Priority |
| ---------------------------------------------------- | ----------- | -------- |
| ADR-0008 Drift detection                             | 16–20h      | Medium   |
| ADR-0011 Promotion strategies                        | 12–16h      | Low      |
| ADR-0013 Secrets (rotation + integrations + display) | 48–62h      | Medium   |
| ADR-0014 Onboarding (6 items)                        | 20–30h      | High     |
| ADR-0017b Tag-based release UX                       | 8–12h       | Low      |
| ADR-0018 SIEM Layer 4                                | 16–24h      | Low      |
| ADR-0020 Config phases (2 items)                     | 4–6h        | Very Low |
| ADR-0022 CEF syslog                                  | 6–8h        | Low      |
| ADR-0023 Pluggable provisioners                      | 24–32h      | Medium   |
| ADR-0024 Provenance tracing                          | 4–6h        | Low      |

**Total estimated post-v1.0 backlog: ~158–216 hours** (3–4 engineer-months at standard sprint capacity)
