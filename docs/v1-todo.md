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
  | 0016 Console REPL            | accepted       | Keep                                                                                                                                                         |
  | 0017 Jinja2 templates        | accepted       | Keep                                                                                                                                                         |
  | 0017b Tag-based release      | proposed       | Mark **deferred**                                                                                                                                            |
  | 0018 Audit traceability      | accepted       | Keep                                                                                                                                                         |
  | 0019 Terraform build output  | accepted       | Keep                                                                                                                                                         |
  | 0020 Lifecycle phases        | accepted       | Keep — clear TBD items for config_fetch/config_clean (mark as post-v1)                                                                                       |
  | 0021 Deployment manifests    | accepted       | Keep                                                                                                                                                         |
  | 0022 SIEM Splunk             | accepted       | Keep — CEF syslog is post-v1                                                                                                                                 |
  | 0023 Pluggable provisioners  | proposed       | Mark **deferred** — not implemented                                                                                                                          |
  | 0024 Environment composition | proposed       | Mark **accepted** — implemented in v0.16.0                                                                                                                   |

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

## Nice-to-Have (Post-v1)

These are designed (ADRs exist) but not required for a usable v1:

- Infrastructure drift detection (ADR-0008)
- Promotion strategies for version progression (ADR-0011)
- Console init wizard (ADR-0014, item 10)
- `--explain` flag on validation errors (ADR-0014)
- Validation error fix suggestions (ADR-0014)
- `config_fetch` / `config_clean` lifecycle phases (ADR-0020)
- Pluggable provisioner framework (ADR-0023)
- Tag-based release workflow (ADR-0017b)
- ~~S3/GCS lock backends~~ — **done**: `lock_s3.py` and `lock_gcs.py` implemented; wired into `lock_factory.py`; 65 passing tests
- CEF syslog format for Splunk SIEM
- Controller-level and CLI command-level unit test expansion

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
