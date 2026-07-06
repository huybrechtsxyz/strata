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
  | 0023 Pluggable provisioners  | proposed       | **MARK ACCEPTED** — `DeployerFactory`, plugin discovery, `BaseDeployer` extensions, all command files migrated; guide + API ref + 2 example plugins          |
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
- ADR-0023: Pluggable provisioner framework — full design and implementation complete in v1.0
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

Track all deferred features via GitHub issues for post-v1.0 prioritization and planning.
Each issue includes enough implementation context for a session to proceed without re-reading the full ADR.

---

### ADR-0008: Infrastructure Drift Detection

- [x] **#XXX** — [ADR-0008] Implement infrastructure drift detection (Phase 1 — Terraform MVP) ✅ **DONE**

  > Detect configuration drift between deployed Terraform state and current workspace config.
  > Uses `terraform plan` with a classification layer. Exit code 3 when drift is found (same as validation failures).
  > Acquires deployment lock (strategy: wrap) before running plan.
  > ADR: `docs/decisions/0008-infrastructure-drift-detection.md`
  > Effort: 16–20h

  **New files:**
  - `src/strata/models/drift_model.py` — `DriftReport`, `DriftEntry`, `DriftSeverity` (critical/high/medium/low/info), `DriftChange`
  - `src/strata/controllers/drift_controller.py` — `DriftController` orchestrates plan → classify → store → report; calls `TerraformDeployer.drift()`; acquires lock
  - `src/strata/commands/deploy/drift_deploy_command.py` — `DriftDeployCommand` (extends `BaseDeployCommand`); flags: `--stage`, `--severity`, `--since`, `--output`; freshness check: warn if build artifacts are stale
  - `src/strata/utils/drift_history.py` — `DriftHistoryStore`; persists per-deployment at `.strata/drift/{deployment_name}.drift.json` (gitignored); tracks `first_detected`, `consecutive_checks`, `last_seen`
  - `src/strata/data/drift_rules.yaml` — built-in classification rules for Azure + AWS resource types; maps `resource_type` + optional `attribute` to severity

  **Modified files:**
  - `src/strata/commands/cli_deploy.py` — register `drift` subcommand under `deploy` group
  - `src/strata/deployers/base_deployer.py` — add non-abstract `drift()` method (default: `return True, {}, ["Drift not supported for this provisioner"]`)
  - `src/strata/deployers/terraform_deployer.py` — implement `drift()`: runs `terraform plan -detailed-exitcode -json`, parses resource_changes, redacts values where `before_sensitive`/`after_sensitive` is true (replace with `"(sensitive)"`)

  **Design decisions (from ADR):**
  - Stages run sequentially (not parallel) — shared Terraform backend state
  - Option C chosen: `terraform plan` with severity classification (not custom state diff)
  - Workspace-level `drift_rules.yaml` merges with built-in rules (user rules take precedence)
  - History: overwrite protection — never removes past entries; new entry written per run; `first_detected` + `consecutive_checks` tracked per resource address
  - Exit code 3 = drift found; exit code 1 = execution failure; exit code 0 = no drift

  **Acceptance criteria:**
  - `strata deploy drift -f <file>` runs and prints table of drifted resources (console) or structured JSON (`--output json`)
  - Sensitive Terraform values are redacted as `"(sensitive)"` in output
  - History stored at `.strata/drift/{name}.drift.json`; re-running accumulates entries
  - Exit code 3 when drift found, 0 when clean
  - Tests: `tests/strata/commands/test_commands_deploy.py`, `tests/strata/controllers/test_drift_controller.py`

- [ ] **#XXX** — [ADR-0008] Drift Phase 2 — Severity customization, `acknowledge`, `history` subcommands, CI helpers

  > Extends Phase 1 with user-configurable severity overrides, acknowledgement workflow, and history querying.
  > ADR: `docs/decisions/0008-infrastructure-drift-detection.md` (Phase 2 section)
  > Effort: 8–12h (after Phase 1)

  **Scope:**
  - Workspace-level `drift_rules.yaml` override/merge with built-in rules
  - `--baseline` flag: saves current drift as accepted baseline (suppresses those entries in future runs)
  - `strata deploy drift acknowledge --address <resource>` subcommand
  - `strata deploy drift history --last N` subcommand (reads `DriftHistoryStore`)
  - GitHub Actions reusable workflow (`.github/workflows/drift-check.yml` in docs/examples)

---

### ADR-0011: Promotion Strategies

- [ ] **#XXX** — [ADR-0011] Implement promotion strategies (Phase 1 — read-only visibility)

  > Read-only `strata promote status` and `strata promote matrix` commands to visualize version progression across environments.
  > No new models, no git automation. Validates the concept before adding automation.
  > ADR: `docs/decisions/0011-promotion-strategies-for-version-progression.md`
  > Effort: 4–6h

  **New files:**
  - `src/strata/commands/cli_promote.py` — Click group `promote` registered in `cli.py`
  - `src/strata/commands/promote/status_promote_command.py` — `PromoteStatusCommand`: scan environment files, parse `spec.version` fields, group by deployment name; console table + JSON
  - `src/strata/commands/promote/matrix_promote_command.py` — `PromoteMatrixCommand`: columns = environments, rows = deployments, cells = version values; highlights mismatches

  **Modified files:**
  - `src/strata/commands/cli.py` — register `promote` group

  **Design decisions (from ADR):**
  - Wave assignment is decentralized — deployments declare their own wave (not centrally managed)
  - No state files for Phase 1 (read-only)
  - Git remains source of truth; YAML version fields are the signal

  **Acceptance criteria:**
  - `strata promote status -f <file>` prints version-per-environment table
  - `strata promote matrix` works with multiple environment files in scope
  - `--output json` works for both subcommands

- [ ] **#XXX** — [ADR-0011] Implement promotion strategies (Phase 2 — model + validation guardrails)

  > Add `spec.promotions` to configuration model, `spec.promotion` to environment model.
  > `strata validate` emits warnings (not errors) when version jumps skip a progression step.
  > ADR: `docs/decisions/0011-promotion-strategies-for-version-progression.md` (Phase 2 section)
  > Effort: 6–8h (after Phase 1)

  **Modified files:**
  - `src/strata/models/configuration_model.py` — add `spec.promotions: List[PromotionStrategyModel]` (progressions, waves, gates)
  - `src/strata/models/environment_model.py` — add `spec.promotion: Optional[PromotionReferenceModel]` (strategy ref)
  - New: `src/strata/models/promotion_model.py` — `PromotionStrategyModel`, `PromotionWave`, `PromotionGate`
  - `src/strata/validators/` — new validator checks progression order; result is `WARNING` not `ERROR` (gate `require_progression_order`)

- [ ] **#XXX** — [ADR-0011] Implement promotion strategies (Phase 3 — automation: `start`, `rollback`, `history`)

  > Full automation: branch creation, YAML version field edits, git commit, activity log, `kind: promotion-record` artifact.
  > ADR: `docs/decisions/0011-promotion-strategies-for-version-progression.md` (Phase 3 section)
  > Effort: 10–14h (after Phase 2)

  **New files:**
  - `src/strata/commands/promote/start_promote_command.py` — `strata promote start --strategy <name> --wave <n> --to <env>`: creates branch, edits YAML, commits
  - `src/strata/commands/promote/rollback_promote_command.py` — reverses last promotion record
  - `src/strata/commands/promote/history_promote_command.py` — queries artifact store for `kind: promotion-record` entries
  - `src/strata/controllers/promotion_controller.py` — orchestrates git + YAML edits + activity log

  **Design decisions (from ADR):**
  - Activity log at `.strata/promotions/` (gitignored, diagnostic only)
  - Completed record (`kind: promotion-record`) written to artifact store (audit evidence)
  - Multi-promotion of same target deliberately causes git merge conflict (correct behavior)
  - Explicit `--wave` required per run (no auto-advance)

---

### ADR-0013: Auto-generated Secrets

- [ ] **#XXX** — [ADR-0013] Implement secret rotation (Phase 3)

  > Age-based advisory + opt-in regeneration for auto-generated secrets.
  > `strata secret rotate --key X --deployment Y` for explicit on-demand rotation.
  > ADR: `docs/decisions/0013-auto-generated-secrets.md` (Phase 3 section)
  > Effort: 16–20h

  **New / modified files:**
  - `src/strata/models/store_models.py` — add `SecretRotateSpec` model: `max_age` (days), `policy` (`advisory` | `rotate`); attach as optional field on `SecretStoreModel`
  - `src/strata/integrations/base_integration.py` — add `get_secret_age() → Optional[timedelta]` stub; use store-native metadata (Key Vault: `updated` field, Vault: `metadata.created_time`)
  - `src/strata/integrations/azure_keyvault.py` — implement `get_secret_age()` using Key Vault secret properties API
  - `src/strata/integrations/hashicorp_vault.py` — implement `get_secret_age()` from `metadata.created_time`
  - `src/strata/controllers/value_controller.py` — `_check_rotation_advisory()`: reads `max_age`, calls `get_secret_age()`, emits `WARNING` log when overdue; `policy: rotate` calls `update_secret()` (regenerate + overwrite)
  - New: `src/strata/commands/secret/rotate_secret_command.py` — `strata secret rotate --key <key> --deployment <file>`: explicit on-demand rotation

  **Design decisions (from ADR):**
  - Phase 3 is inform-only for `advisory` policy — application is responsible for consumer coordination
  - `update_secret()` is separate from `set_secret()` (create-if-not-exists) — rotation always overwrites
  - Only store-native metadata supported (no external metadata store)
  - Rotation writes structured audit log entry (action: `rotate`)

  **Acceptance criteria:**
  - `strata build plan` shows `[rotation overdue]` annotation when `max_age` exceeded
  - `strata secret rotate --key <k> --deployment <f>` regenerates the secret and updates the store
  - `policy: rotate` in YAML triggers automatic regeneration during `build run`

- [ ] **#XXX** — [ADR-0013] Add missing `set_*` methods for Vault, Consul, Flagsmith integrations

  > Phase 1+2 seed-on-missing is fully implemented for most integrations, but Vault, Consul, and Flagsmith are missing their write-side methods.
  > ADR: `docs/decisions/0013-auto-generated-secrets.md` (Phase 2 — Variable + Feature Defaults)
  > Effort: 8–12h

  **Modified files:**
  - `src/strata/integrations/hashicorp_vault.py` — implement `set_variable(key, value)` and `set_feature(key, value)` (uses KV v2 `data/` path for variables; Vault has no native feature flag concept — store under a configurable prefix)
  - `src/strata/integrations/consul.py` — implement `set_variable(key, value)` (PUT to Consul KV API); Consul has no feature flag concept — `set_feature()` can use `variables/` prefix or raise `NotImplementedError` with clear message
  - `src/strata/integrations/flagsmith.py` — implement `set_variable(key, value)` (Flagsmith remote config trait) and `set_feature(key, enabled)` (Flagsmith feature flag toggle)
  - All 3: must implement create-if-not-exists semantics — if key already exists, re-read and return existing value (race safety)
  - All 3: write structured audit log entry on each successful write

  **Existing pattern:** See `src/strata/integrations/azure_keyvault.py` and `src/strata/integrations/bitwarden.py` for reference implementations of `set_secret()`.

  **Acceptance criteria:**
  - `strata build run` with Vault/Consul/Flagsmith store + `default:` field seeds missing variables on first run
  - Re-running never overwrites existing values
  - Audit log shows `action: seed` entry with key, store type, deployment

- [ ] **#XXX** — [ADR-0013] Display seed-on-missing status in `strata build plan`

  > `strata build plan` currently reports values from YAML only (no network). Extend it to show `[generated]` / `[seeded]` annotations for auto-generated secrets and defaulted variables, and `[missing]` for required values that have no default and don't exist in the store.
  > ADR: `docs/decisions/0013-auto-generated-secrets.md` (Phase 1 — Plan awareness)
  > Effort: 6–8h

  **Modified files:**
  - `src/strata/commands/builders/plan_build_command.py` (or equivalent build plan command) — extend value table to include a `Status` column: `[generated]` (has `generate:` spec), `[seeded]` (has `default:`), `[required]` (no default, no generate), `[ok]` (literal value)
  - `src/strata/controllers/build_controller.py` — in dry-run/plan mode, check store for each `[required]` secret/variable and emit `[missing]` if not found (requires store read during plan)
  - `src/strata/models/store_models.py` — ensure `generate` and `default` fields are accessible during plan phase

  **Acceptance criteria:**
  - `strata build plan` table shows `[generated]` next to secrets with `generate:` spec
  - `strata build plan` table shows `[seeded]` next to variables with `default:` value
  - `strata build plan --check-store` (or default) reports `[missing]` for required values absent from store

---

### ADR-0014: Onboarding Experience

- [ ] **#XXX** — [ADR-0014 item 10] Implement `init` wizard inside `strata console` REPL

  > Guided Q&A flow accessible via `init` command inside the `strata console` REPL.
  > Asks: stack type (AKS/Compose/Swarm/etc.) → cloud provider → environment names → scaffolds YAML files.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 3, item 10)
  > Effort: 4–6h

  **Modified files:**
  - `src/strata/commands/console/console_command.py` (or REPL handler) — register `init` as a REPL command
  - `src/strata/controllers/guide_controller.py` — add `run_init_wizard()` method: uses `prompt_toolkit` for interactive Q&A; calls existing `strata new` scaffolding logic with resolved answers
  - `src/strata/commands/cli_new.py` — expose `scaffold_from_answers(stack, provider, envs)` as a callable (currently only wired to CLI flags)

  **Flow:** REPL `init` → select stack type → select provider → name environments → confirm → calls scaffold → reports created files → transitions to `guide status`

  **Acceptance criteria:**
  - Typing `init` in `strata console` starts Q&A flow
  - All standard stacks (AKS, GKE, EKS, Compose, Swarm) are selectable
  - Files created match `strata new --type <stack>` output
  - Works without exiting REPL; shows next recommended step after scaffold

- [ ] **#XXX** — [ADR-0014 item 15] Implement interactive `strata new` in REPL

  > When `new` is invoked inside `strata console` REPL without flags, prompt for missing values interactively (name, type, provider) instead of requiring all flags upfront.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 3, item 15)
  > Effort: 2–3h

  **Modified files:**
  - `src/strata/commands/cli_new.py` — detect when running inside REPL context (`ctx.obj["in_repl"]`); if True and required args missing, use `prompt_toolkit.prompt()` to ask interactively
  - `src/strata/controllers/guide_controller.py` — set `in_repl: True` in context when inside console session

  **Acceptance criteria:**
  - `new` inside REPL without arguments prompts for `--type`, `--name` interactively
  - Tab-completion works for type values
  - Outside REPL: behavior unchanged (flags required, no prompting)

- [ ] **#XXX** — [ADR-0014 item 17] Implement `strata env doctor` health check command

  > Workspace diagnostics command that checks tool availability, file validity, store connectivity, and deployment readiness. Outputs a checklist with pass/fail/warning per check, and fix suggestions for failures.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 3, item 17)
  > Effort: 3–4h

  **New files:**
  - `src/strata/commands/envs/doctor_env_command.py` — `DoctorEnvCommand`; runs sequential checks:
    1. Tool availability (`strata tools status` — existing `ToolsController`)
    2. YAML file validation (calls `strata validate --path "**"` equivalent)
    3. Store connectivity (ping each configured store: can it authenticate + read?)
    4. Build freshness (are build artifacts up to date?)
    5. Lock status (is deployment locked by another process?)
  - Register in `src/strata/commands/cli_envs.py` (or `cli_env.py`) under `env doctor`

  **Output format:**
  - Console: `✅ terraform available`, `⚠️  keyvault: auth failed — run 'az login'`, `❌ workspace.yaml invalid — line 12: unknown field 'foo'`
  - JSON: `{"checks": [{"name": "...", "status": "pass"|"warn"|"fail", "message": "...", "fix": "..."}]}`

  **Acceptance criteria:**
  - `strata env doctor -f <deployment>` runs all checks and prints checklist
  - Fix suggestions include exact commands where applicable
  - Exit code 3 when any check fails; 0 when all pass or only warnings

- [ ] **#XXX** — [ADR-0014 item 19] Implement progressive dependency scaffolding in `strata new`

  > When `strata new` creates a file that references another file (e.g. a deployment referencing a workspace), automatically offer to create the missing referenced file.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 3, item 19)
  > Effort: 2–3h

  **Modified files:**
  - `src/strata/commands/cli_new.py` — after creating a file, scan its cross-file references (`@repo/path` patterns and `spec.deployment_file`, `spec.workspace`, etc.); for each referenced path that doesn't exist, prompt: "Referenced file `workspace.yaml` not found — create it? [y/N]"; if yes, call `strata new <kind>` for that file
  - `src/strata/utils/reference_scanner.py` (may need to create) — extract all `@repo/path` and relative references from a newly created YAML model

  **Acceptance criteria:**
  - Creating a deployment YAML that references a missing workspace YAML prompts to create it
  - Scaffolded dependency file has a name matching the reference
  - `--no-scaffold-deps` flag skips the prompting

- [ ] **#XXX** — [ADR-0014 item 20] Implement auto-refresh REPL mode (`strata console --auto`)

  > Adds a `--auto` flag to `strata console` that starts a background file watcher. When workspace YAML files change, automatically re-runs `strata validate` and updates the `guide status` display without user action.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 5, item 20)
  > Effort: 3–4h

  **Modified files:**
  - `src/strata/commands/console/console_command.py` — add `--auto` flag; starts a `watchdog` (or `polling`) file watcher on `*.yaml` in `work_path` in a background thread
  - `src/strata/controllers/guide_controller.py` — add `refresh()` method that re-validates and re-renders status; called by the file watcher callback
  - `pyproject.toml` — add `watchdog` as optional dependency (`strata[console]` extra or included in base)

  **Acceptance criteria:**
  - `strata console --auto` starts REPL; editing a YAML file triggers automatic re-validation within 2 seconds
  - Changed files are highlighted in the re-rendered status
  - Ctrl+C cleanly stops both REPL and watcher

- [ ] **#XXX** — [ADR-0014 item 21] Implement template marketplace / community templates

  > `strata sln init --template <url>` downloads a community template from a git URL or registry URL and scaffolds from it.
  > ADR: `docs/decisions/0014-onboarding-experience.md` (Phase 5, item 21)
  > Effort: 3–5h

  **Modified files:**
  - `src/strata/commands/sln/init_solution_command.py` — add `--template <url>` flag; if URL starts with `https://`, clone to a temp dir and treat as template source; if it matches `<registry>/<name>`, fetch from a central `strata-templates` registry manifest (future)
  - `src/strata/utils/template_loader.py` (new) — `fetch_template(url) → Path`: handles git clone via `GitIntegration`, validates template structure, returns local path

  **Acceptance criteria:**
  - `strata sln init --template https://github.com/example/my-template` scaffolds from the remote template
  - Template is validated before files are written
  - `--template` and `--type` are mutually exclusive

---

### ADR-0017b: Tag-based Release Workflow

- [ ] **#XXX** — [ADR-0017b] Implement tag-based release workflow: `GitIntegration.list_tags()`, `RefConventionPolicy`, enhanced `strata repo status`

  > Validates that remote repository tags match declared naming conventions. Surfaces tag metadata (latest release tag, latest quality-gate tag) in `strata repo status`. No CI orchestration — strata validates and reports; CI/CD owns release creation.
  > ADR: `docs/decisions/0017-tag-based-release-workflow-option-c.md`
  > Effort: 8–12h

  **New files:**
  - `src/strata/validators/policies/ref_convention_policy.py` — `RefConventionPolicy` (built-in policy type): validates remote ref names against declared patterns (`release_pattern`, `quality_pattern` regex); heuristic: if ref looks like a tag (starts with `v`, no `/`), validate against tag patterns; register as `"ref_convention"` in `policy_engine.py`

  **Modified files:**
  - `src/strata/integrations/git.py` — add `TagInfo` dataclass (`name`, `commit`, `created`, `message`, `is_annotated`); implement `list_tags(pattern: str = "*", sort: str = "creatordate") → List[TagInfo]` (runs `git tag --list --sort=-creatordate --format=...`)
  - `src/strata/validators/policies/policy_engine.py` — register `"ref_convention"` type
  - `src/strata/commands/repo/status_repo_solution_command.py` — for each remote, call `GitIntegration.list_tags()`, find latest release tag (matches `release_pattern`) and latest quality-gate tag (matches `quality_pattern`); display in console table with tag name, commit short SHA, age; include in JSON output

  **Configuration (in configuration YAML):**
  ```yaml
  spec:
    policies:
      configuration:
        remotes:
          - name: my-repo
            release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
            quality_pattern: "^quality-gate-\\d+$"
  ```

  **Acceptance criteria:**
  - `strata repo status` shows latest release + quality-gate tag per remote (or "no tags" if none found)
  - `strata validate` emits policy warning when ref name doesn't match declared pattern
  - `strata policy check -f <config>` includes `ref_convention` results
  - Tests in `tests/strata/validators/policies/test_ref_convention_policy.py`

---

### ADR-0018: Deployment Audit Traceability

- [ ] **#XXX** — [ADR-0018 Layer 4] Extend SIEM integrations — ELK (Elasticsearch) and OpenTelemetry exporters

  > Layer 4 of ADR-0018. Splunk HEC integration ships in v1.0. This issue adds ELK (Elasticsearch bulk index API) and OpenTelemetry (OTLP gRPC/HTTP) SIEM sinks for `strata audit export --siem <name>`.
  > ADR: `docs/decisions/0018-deployment-audit-traceability.md` (Layer 4 section)
  > Effort: 16–24h

  **New files:**
  - `src/strata/integrations/siem/elk_integration.py` — `ElkSiemIntegration` extends `SiemBaseIntegration`; uses Elasticsearch bulk index API (`POST /_bulk`); authentication via API key (`ELASTICSEARCH_API_KEY`) or username/password; index name configurable (default: `strata-audit-{date}`)
  - `src/strata/integrations/siem/otel_integration.py` — `OtelSiemIntegration` extends `SiemBaseIntegration`; exports as OTLP logs (gRPC or HTTP/JSON); uses `opentelemetry-sdk` + `opentelemetry-exporter-otlp`; endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT`

  **Modified files:**
  - `src/strata/integrations/siem/__init__.py` — export both new classes
  - `src/strata/integrations/factory.py` — register `"elk"` and `"otel"` sink types
  - `src/strata/models/audit_config_model.py` — add `"elk"` and `"otel"` as valid sink type values
  - `pyproject.toml` — add `opentelemetry-sdk` and `opentelemetry-exporter-otlp` as optional deps (e.g. `strata[otel]` extra)

  **Existing pattern:** See `src/strata/integrations/siem/splunk_siem_integration.py` for reference implementation of `ISiemSink.send_event()` and `send_batch()`.

  **Acceptance criteria:**
  - `strata audit export --siem elk --output json` forwards deploy-log entries to Elasticsearch
  - `strata audit export --siem otel` exports as OTLP log records
  - Non-blocking: SIEM delivery failure logs warning and continues (never fails deployment)
  - Tests in `tests/strata/integrations/siem/`

---

### ADR-0020: Lifecycle Phases [done]

- [x] ~~[ADR-0020] Implement `config_fetch` lifecycle phase~~ — **done**: `config_fetch_before/after` wired in `BaseBuildCommand._before_execute()` around `_load_configuration_service()`
- [x] ~~[ADR-0020] Implement `config_clean` lifecycle phase~~ — **done**: `config_clean_before/after` wired in `CleanBuildCommand.execute()` after build artifact cleanup; `ConfigurationService.reset()` called between hooks (skipped on `--dry-run`)

---

### ADR-0022: SIEM Integration (Splunk)

- [ ] **#XXX** — [ADR-0022] Implement CEF syslog format for SIEM (`format: cef` on syslog sink)

  > Splunk HEC ships in v1.0. This issue adds CEF (Common Event Format) encoding as an alternative format on existing syslog sinks. CEF is required by SIEM tools that ingest syslog streams (not HEC). Format is transport-identical to `syslog_json` — only payload encoding changes.
  > ADR: `docs/decisions/0022-siem-integration-splunk-hec-cef.md` (Phase 2 section)
  > Effort: 6–8h

  **Modified files:**
  - `src/strata/models/audit_config_model.py` — add `format: Literal["json", "cef"] = "json"` to the syslog sink model
  - `src/strata/controllers/audit_controller.py` — add `_format_cef(event: dict) → str` method:
    - CEF header: `CEF:0|huybrechts|strata|{version}|{event_type}|{event_name}|{severity}|`
    - Severity: `3` (Low) on success, `7` (High) on failure
    - Extension fields: `shost`, `duser`, `cs1` (deployment), `cs2` (stage), `msg`
    - In `_send_syslog()`: check `sink.format`, call `_format_cef()` when `"cef"` instead of JSON serialization
  - `src/strata/commands/cli_audit.py` — add `--siem <name>` flag to `strata audit export` subcommand; resolves integration by name from `AuditSinkModel`, verifies it implements `ISiemSink`, calls `send_batch()`

  **Configuration example:**
  ```yaml
  spec:
    audit:
      sinks:
        - name: my-siem
          type: syslog
          format: cef                 # new field
          endpoint: syslog.example.com:514
  ```

  **Acceptance criteria:**
  - `strata audit export --siem my-siem` forwards entries in CEF format when `format: cef`
  - CEF string validates against CEF 0 specification (header + at least 5 extension fields)
  - `format: json` (default) behavior unchanged
  - Tests in `tests/strata/controllers/test_audit_controller.py`

---

### ADR-0023: Pluggable Provisioners [done]

- [x] ~~[ADR-0023] Implement pluggable provisioner framework~~ — **done**: `DeployerFactory` with plugin discovery, `BaseDeployer` extensions, all command files migrated; guide + API reference + 2 example plugins added

---

### ADR-0024: Environment Composition

- [ ] **#XXX** — [ADR-0024] Implement `--trace` flag for merge provenance on `strata values list`

  > Show which environment file each resolved value originates from. The flat merge is already implemented; provenance tracking (which file "won") is the remaining piece.
  > ADR: `docs/decisions/0024-environment-composition-flat-merge-fix.md` (Phase 3 section)
  > Effort: 4–6h

  **New files:**
  - `src/strata/utils/merge_provenance.py` — `MergeProvenance` dataclass: `variable_sources: Dict[str, str]` (key → source file path), `secret_sources: Dict[str, str]`, `feature_sources: Dict[str, str]`, `override_sources: Dict[str, str]`, `merge_order: List[str]` (ordered file list)

  **Modified files:**
  - `src/strata/services/environment_service.py` — extend `merge_envfiles()` to populate `MergeProvenance` alongside the merged model; track which file last set each key (last-wins = last file in `merge_order` wins)
  - `src/strata/services/deployment_service.py` — store and expose `MergeProvenance` from the merged environment; accessible as `deployment_service.merge_provenance`
  - `src/strata/utils/resolved_values.py` — add `variable_sources: Dict[str, str]`, `secret_sources: Dict[str, str]`, `feature_sources: Dict[str, str]` (populated from `MergeProvenance` during value resolution)
  - `src/strata/controllers/value_controller.py` — accept `MergeProvenance` from `deployment_service` and forward to `ResolvedValues`
  - `src/strata/commands/cli_values.py` — add `--trace` flag to `strata values list`
  - `src/strata/commands/deploy/list_values_deploy_command.py` — when `--trace`:
    - Console: add `Source` column showing base filename; annotate overridden keys with `(overrides <earlier-file>)`; print "Merge order:" footer
    - JSON: add `source` and `overridden_from` fields per entry

  **Design decisions (from ADR):**
  - Provenance is runtime-only (not stored in models — models must load without filesystem)
  - No YAML schema changes — fully backward-compatible
  - `merge_order` reflects the `environments:` list from the deployment YAML (left to right = base to override)

  **Acceptance criteria:**
  - `strata values list --trace -f <deployment>` shows source file per value
  - Values overridden by a later environment show `(overrides <base-file>)` annotation
  - `--output json --trace` includes `source` and `overridden_from` fields
  - `--trace` without `--output json` shows "Merge order:" footer in console output
  - Tests in `tests/strata/commands/test_commands_values.py`

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
| ADR-0024 Provenance tracing                          | 4–6h        | Low      |

**Total estimated post-v1.0 backlog: ~134–184 hours** (2.5–3.5 engineer-months at standard sprint capacity)
