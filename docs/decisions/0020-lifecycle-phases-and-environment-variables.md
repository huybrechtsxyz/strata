# Lifecycle phases and environment variables — standardization and completeness

- Status: proposed
- Date: 2026-07-03

## Context and Problem Statement

The strata lifecycle scripts system allows users to hook custom scripts into deployment and build commands. However, there are critical gaps between the implemented feature and its documentation:

1. **Environment variables use wrong prefix** — LifecycleController injects `XYZ_*` variables, but documentation promises `STRATA_*` prefix.

2. **Documented phases are not implemented** — `build_validate`, `build_generate`, `deploy_health`, `deploy_configure`, and `deploy_apply_before/after` are in documentation but never called by commands.

3. **15+ implemented phases are undocumented** — phases like `validate_before/after`, `solution_init_before/after`, `deploy_stage_before/after`, and `deploy_plan_after` exist in code but users have no way to discover them.

4. **Hierarchical execution not implemented** — documentation claims top-down execution (workspace → namespace → module → provider → resource), but only configuration-level scripts execute. `execute_workspace_phase()` exists but is never called.

5. **ScriptDeployer uses different environment** — deployment-level scripts receive different environment variables (`WORK_PATH`, `BUILD_PATH`, `STAGE_NAME`) vs. command-level scripts (`XYZ_*` prefix), creating inconsistent behaviour.

6. **Inconsistent phase naming patterns** — before/after phases (`build_run_before`) coexist with single-name phases (`deploy_setup`, `deploy_check`) without clear guidance.

**Result:** Users cannot reliably use lifecycle hooks. Documentation and implementation diverge. The feature feels incomplete and undiscoverable.

## Decision Drivers

- **Usability** — Users must be able to discover and use all available lifecycle phases without reading source code.
- **Consistency** — All lifecycle scripts must receive the same environment variables with predictable names.
- **Composability** — Lifecycle hooks at multiple levels (configuration, workspace, namespace, module, provider, resource) enable modular infrastructure-as-code.
- **Documentation-as-contract** — If a phase is documented, it must be implemented. If implemented, it must be documented.
- **Backward compatibility** — Existing workspaces using lifecycle phases should continue to work.

## Considered Options

### Option A — Status quo

Keep current implementation. Update documentation to match code.
- Good: No code changes.
- Bad: 15+ useful phases remain undocumented; hierarchical execution remains unimplemented; inconsistent naming persists.
- Bad: ScriptDeployer behaviour still differs from LifecycleController.

### Option B — Fix documentation only

Document all 27 implemented phases. Remove undocumented phases from documentation. Accept environment variable naming as-is.
- Good: Minimal code changes; users can discover phases.
- Bad: `XYZ_*` prefix contradicts all public documentation; hierarchical execution gap remains.
- Bad: Inconsistent phase naming not addressed.

### Option C — Full standardization and completeness (recommended)

1. **Standardize environment variables** — All lifecycle scripts receive `STRATA_*` prefix (STRATA_PHASE, STRATA_WORKSPACE_PATH, STRATA_CONFIG_PATH, STRATA_BUILD_PATH, STRATA_OBJECT_PATH).
2. **Document all 27 implemented phases** — Complete phase reference with triggers, context variables, and use cases.
3. **Implement missing documented phases** — `build_validate`, `build_generate`, `deploy_health`, `deploy_configure`, `deploy_apply_before/after`.
4. **Implement hierarchical execution** — Execute lifecycle scripts at workspace, namespace, module, provider, and resource levels.
5. **Standardize naming pattern** — Move toward consistent before/after pattern where applicable.

## Decision Outcome

**Option C.** A three-phase implementation plan:

### Phase 1: Environment Variable Standardization (P0 — blocking)

All lifecycle scripts receive `STRATA_*` environment variables:

```bash
STRATA_PHASE="deploy_stage_before"              # Current phase name
STRATA_WORKSPACE_PATH="/path/to/workspace"      # Workspace root
STRATA_CONFIG_PATH="/path/to/.strata/config"    # Config directory
STRATA_BUILD_PATH="/path/to/build/artifacts"    # Build output directory
STRATA_OBJECT_PATH="/path/to/build/objects"     # Objects directory
```

**Implementation scope:**
- Update `LifecycleController._prepare_environment()` to inject `STRATA_*` variables.
- Update `LifecycleController._execute_script()` to explicitly set `STRATA_PHASE`.
- Update `ScriptDeployer._execute_script()` to use the same environment variable naming.
- Context parameters passed to `_run_lifecycle_phase()` are converted to `STRATA_<KEY>` (not `XYZ_`).

**Backward compatibility:** `XYZ_*` variables are deprecated but still injected for one major version to allow users to migrate scripts.

### Phase 2: Complete Phase Documentation (P1 — UX improvement)

Document all 27 implemented lifecycle phases with:
- Phase name and naming pattern explanation
- Trigger points (which command/step calls this phase)
- Available context variables
- Execution scope (configuration-level only, or file-level + config-level, or hierarchical)
- Use case examples

**Update `docs/platform/lifecycles.md` to include:**

| Phase                    | Trigger                            | Scope               | Available Context                 |
| ------------------------ | ---------------------------------- | ------------------- | --------------------------------- |
| `validate_before`        | Before file validation             | file + config       | file, kind, validation_passed     |
| `validate_after`         | After file validation              | file + config       | file, kind, validation_passed     |
| `solution_init_before`   | Before solution init               | config              | solution_name, work_path          |
| `solution_init_after`    | After solution init                | config              | solution_name, work_path          |
| `solution_clean_before`  | Before solution clean              | config              | work_path, dry_run                |
| `solution_clean_after`   | After solution clean               | config              | work_path, dry_run                |
| `solution_update_before` | Before solution update             | config              | work_path                         |
| `solution_update_after`  | After solution update              | config              | work_path                         |
| `config_fetch_before`    | Before config fetch                | config              | (TBD by implementation)           |
| `config_fetch_after`     | After config fetch                 | config              | (TBD by implementation)           |
| `config_clean_before`    | Before config clean                | config              | (TBD by implementation)           |
| `config_clean_after`     | After config clean                 | config              | (TBD by implementation)           |
| `build_run_before`       | Before build start                 | config              | file, dry_run                     |
| `build_run_after`        | After build complete               | config              | file, dry_run                     |
| `build_validate`         | (Not yet implemented)              | config              | —                                 |
| `build_generate`         | (Not yet implemented)              | config              | —                                 |
| `deploy_run_before`      | Before deploy start                | config              | file, stage, dry_run              |
| `deploy_run_after`       | After deploy complete              | config              | file, stage, dry_run              |
| `deploy_destroy_before`  | Before destroy start               | config              | file, stage, dry_run              |
| `deploy_destroy_after`   | After destroy complete             | config              | file, stage, dry_run              |
| `deploy_stage_before`    | Before stage executes              | config + deployment | stage, dry_run                    |
| `deploy_stage_after`     | After stage completes              | config + deployment | stage, dry_run                    |
| `deploy_plan_after`      | After plan, before apply           | config + deployment | stage, dry_run (gates apply step) |
| `deploy_setup`           | Setup step (ScriptDeployer)        | deployment          | (injected by deployer)            |
| `deploy_check`           | Check step (ScriptDeployer)        | deployment          | (injected by deployer)            |
| `deploy_plan`            | Plan step (ScriptDeployer)         | deployment          | (injected by deployer)            |
| `deploy_apply`           | Apply step (ScriptDeployer)        | deployment          | (injected by deployer)            |
| `deploy_destroy`         | Destroy step (ScriptDeployer)      | deployment          | (injected by deployer)            |
| `deploy_plan_destroy`    | Plan-destroy step (ScriptDeployer) | deployment          | (injected by deployer)            |
| `deploy_output`          | Output step (ScriptDeployer)       | deployment          | (injected by deployer)            |
| `deploy_health`          | (Not yet implemented)              | config              | —                                 |
| `deploy_configure`       | (Not yet implemented)              | config              | —                                 |

### Phase 3: Hierarchical Execution (P2 — composability)

Implement top-down lifecycle script execution:

1. **Configuration level** (today — already implemented)
2. **Workspace level** (to be added)
3. **Namespace level** (to be added)
4. **Module level** (to be added)
5. **Provider level** (to be added)
6. **Resource level** (to be added)

When a phase executes, scripts run in hierarchy order, with each level's scripts executing before the next. Failures at any level stop the pipeline unless `continue_on_failure` is set.

**Scope:** Deployment-related commands (deploy, destroy) only. Build, validate, and solution commands remain configuration-level only (unless explicitly extended).

## Design: Phase Naming

### Naming pattern: `{command}_{action}[_{suffix}]`

- `{command}` — CLI command group (`build`, `deploy`, `validate`, `solution`, `config`)
- `{action}` — operation name (`run`, `plan`, `apply`, `destroy`, `stage`, `setup`, `validate`, `fetch`, `clean`, `init`, `update`)
- `{suffix}` — optional qualifier (`before`, `after`, or custom like `_plan_after` for gates)

**Examples:**
```
build_run_before         → Before 'strata build run' executes
deploy_stage_before      → Before a stage begins provisioning
deploy_plan_after        → After planning, before apply (gate)
validate_before          → Before validation begins
solution_init_before     → Before solution init
```

This pattern applies consistently to all new and existing phases.

## Consequences

### Phase 1 (Environment Variables) — High impact, medium risk

- **Good:** Scripts become portable across environments; users don't need to re-learn variable names.
- **Good:** Deployment scripts and build scripts receive consistent environment.
- **Risk:** Breaking change if users hard-code `XYZ_*` in existing scripts. Mitigated by one-release deprecation window.

### Phase 2 (Documentation) — High impact, low risk

- **Good:** Users can discover all available phases without reading source code.
- **Good:** Clear documentation enables adoption of advanced patterns (gates, per-stage hooks).
- **Risk:** None — purely additive.

### Phase 3 (Hierarchical Execution) — Medium impact, medium-to-high risk

- **Good:** Modules, namespaces, providers, and resources can define their own lifecycle hooks; enables composable, modular infrastructure-as-code.
- **Good:** Reduces need for top-level orchestration scripts.
- **Risk:** Execution order complexity; users must understand the top-down ordering. Mitigated by clear documentation and examples.
- **Risk:** Performance impact if many nested levels define expensive hooks. Mitigated by clear guidance on script design.

## Implementation Plan

1. **Phase 1 (Weeks 1–2):**
   - [ ] Update `LifecycleController` to inject `STRATA_*` variables.
   - [ ] Update `ScriptDeployer` to use `STRATA_*` naming.
   - [ ] Add deprecation notice to `XYZ_*` injection.
   - [ ] Update all tests.
   - [ ] Release in next minor version with deprecation warnings.

2. **Phase 2 (Weeks 2–3):**
   - [ ] Document all 27 phases in `docs/platform/lifecycles.md`.
   - [ ] Implement `build_validate`, `build_generate`, `deploy_health`, `deploy_configure` hooks.
   - [ ] Implement `deploy_apply_before/after` gates in `run_deploy_command.py`.
   - [ ] Update phase naming documentation.
   - [ ] Release in same minor version.

3. **Phase 3 (Weeks 4–6):**
   - [ ] Extend deployment-related services (WorkspaceService, NamespaceService, etc.) with lifecycle phase support.
   - [ ] Implement hierarchical execution in deployment controllers.
   - [ ] Update `run_deploy_command.py` to traverse hierarchy.
   - [ ] Update tests and examples.
   - [ ] Release in next minor version.

## Related Decisions

- **ADR 0003 (Layered Architecture)** — LifecycleController sits in the controllers layer; services provide lifecycle models.
- **ADR 0004 (Exit Code Convention)** — Exit code 1 for system failures in lifecycle hooks; exit code 3 for validation errors if a pre-validate hook fails.

## References

- Current implementation: [src/strata/controllers/lifecycle_controller.py](src/strata/controllers/lifecycle_controller.py)
- Current documentation: [docs/platform/lifecycles.md](docs/platform/lifecycles.md)
- Deployment pipeline: [src/strata/commands/deploy/run_deploy_command.py](src/strata/commands/deploy/run_deploy_command.py)
- Gap analysis: See GitHub issue #XXX (lifecycle scripts gaps)
