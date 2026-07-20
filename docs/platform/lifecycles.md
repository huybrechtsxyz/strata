# Lifecycles

Extension points for custom scripts during command execution. Scripts are defined in the YAML lifecycle block and executed by the `LifecycleController` at well-defined points in each command.

**Supported script types:** `.ps1`, `.sh`, `.bash`, `.bat`, `.cmd`, `.py`  
**Default timeout:** 5 minutes per script  
**Skip hooks:** `--no-hooks` flag

## Configuration

Scripts are defined in any YAML file that supports a `lifecycle:` block (workspace, namespace, configuration, environment):

```yaml
lifecycle:
  build_validate:
    scripts:
      - scripts/check-tools.sh
  deploy_apply_before:
    scripts:
      - scripts/backup-state.ps1
  deploy_apply_after:
    scripts:
      - scripts/notify-slack.py
```

**Paths:** Relative to the workspace root (`work_path`)  
**Order:** Scripts within a phase execute in the order listed

## Template Substitution in Scripts

When `enable_templating` is active (default), strata pre-processes each lifecycle script with Jinja2 before execution. Use `{{ STRATA_* }}` syntax to reference strata-injected variables directly in the script source:

```bash
#!/bin/bash
echo "Phase: {{ STRATA_PHASE }}"
echo "Workspace: {{ STRATA_WORKSPACE_PATH }}"

# Native shell variables are left untouched — resolved by the shell at runtime
cd $HOME
export PATH=$PATH:/usr/local/bin
```

Only `STRATA_*` variables are available as Jinja2 template context. Undefined `{{ var }}` references are left visible in the rendered output rather than causing an error.

Scripts also receive all `STRATA_*` variables as OS environment variables regardless of whether Jinja2 substitution is used, so both access styles work:

```python
import os
# Option A: Jinja2 pre-substitution (value baked into script at template time)
phase = "{{ STRATA_PHASE }}"

# Option B: env var lookup at runtime (works without templating too)
phase = os.environ["STRATA_PHASE"]
```

## Template Substitution in Scripts

When `enable_templating` is active (default), strata pre-processes each lifecycle script with Jinja2 before execution. Use `{{ STRATA_* }}` syntax to reference strata-injected variables directly in the script source:

```bash
#!/bin/bash
echo "Phase: {{ STRATA_PHASE }}"
echo "Workspace: {{ STRATA_WORKSPACE_PATH }}"

# Native shell variables are left untouched — resolved by the shell at runtime
cd $HOME
export PATH=$PATH:/usr/local/bin
```

Only `STRATA_*` variables are available as Jinja2 template context. Undefined `{{ var }}` references are left visible in the rendered output rather than causing an error.

Scripts also receive all `STRATA_*` variables as OS environment variables regardless of whether Jinja2 substitution is used, so both access styles work:

```python
import os
# Option A: Jinja2 pre-substitution (value baked into script at template time)
phase = "{{ STRATA_PHASE }}"

# Option B: env var lookup at runtime (works without templating too)
phase = os.environ["STRATA_PHASE"]
```

## Environment Variables

Every script receives these variables:

```bash
STRATA_PHASE=deploy_apply_before       # Current lifecycle phase name
STRATA_WORKSPACE_PATH=/path/to/ws      # Workspace root directory
STRATA_CONFIG_PATH=/path/to/ws/.strata # strata state directory
STRATA_BUILD_PATH=/path/to/ws/build    # Build artifacts directory
STRATA_OBJECT_PATH=/path/to/ws/build/objects  # Objects sub-directory
```

Phase-specific context is also injected as `STRATA_<KEY>` (all caps). For example, a phase with `context={"file": "...", "dry_run": True}` injects `STRATA_FILE` and `STRATA_DRY_RUN`.

## Phase Reference

### validate

| Phase             | Trigger                                        | Context variables                                        |
| ----------------- | ---------------------------------------------- | -------------------------------------------------------- |
| `validate_before` | Before a file is validated (`strata validate`) | `STRATA_FILE`, `STRATA_KIND`                             |
| `validate_after`  | After validation completes                     | `STRATA_FILE`, `STRATA_KIND`, `STRATA_VALIDATION_PASSED` |

### solution

| Phase                    | Trigger                    | Context variables      |
| ------------------------ | -------------------------- | ---------------------- |
| `solution_init_before`   | Before `strata sln init`   | `STRATA_SOLUTION_NAME` |
| `solution_init_after`    | After `strata sln init`    | `STRATA_SOLUTION_NAME` |
| `solution_clean_before`  | Before `strata sln clean`  | `STRATA_DRY_RUN`       |
| `solution_clean_after`   | After `strata sln clean`   | `STRATA_DRY_RUN`       |
| `solution_update_before` | Before `strata sln update` | —                      |
| `solution_update_after`  | After `strata sln update`  | —                      |

### config

| Phase                 | Trigger             | Context variables |
| --------------------- | ------------------- | ----------------- |
| `config_fetch_before` | Before config fetch | —                 |
| `config_fetch_after`  | After config fetch  | —                 |
| `config_clean_before` | Before config clean | —                 |
| `config_clean_after`  | After config clean  | —                 |

### build

| Phase              | Trigger                                               | Context variables               |
| ------------------ | ----------------------------------------------------- | ------------------------------- |
| `build_run_before` | Before `strata build run` starts                      | `STRATA_FILE`, `STRATA_DRY_RUN` |
| `build_validate`   | After services loaded, before any artifact generation | `STRATA_FILE`, `STRATA_DRY_RUN` |
| `build_generate`   | After all builders complete, before policy evaluation | `STRATA_FILE`, `STRATA_DRY_RUN` |
| `build_run_after`  | After `strata build run` completes                    | `STRATA_FILE`, `STRATA_DRY_RUN` |

### deploy run

| Phase                 | Trigger                                                     | Scope            | Context variables                               |
| --------------------- | ----------------------------------------------------------- | ---------------- | ----------------------------------------------- |
| `deploy_run_before`   | Before provisioning starts                                  | config           | `STRATA_FILE`, `STRATA_STAGE`, `STRATA_DRY_RUN` |
| `deploy_stage_before` | Before each stage executes                                  | **hierarchical** | `STRATA_STAGE`, `STRATA_DRY_RUN`                |
| `deploy_plan_after`   | After plan, before apply (gates apply — non-zero blocks it) | config           | `STRATA_STAGE`, `STRATA_DRY_RUN`                |
| `deploy_apply_before` | Before the apply step of a stage (gates apply)              | **hierarchical** | `STRATA_STAGE`, `STRATA_DRY_RUN`                |
| `deploy_apply_after`  | After the apply step of a stage succeeds                    | **hierarchical** | `STRATA_STAGE`, `STRATA_DRY_RUN`                |
| `deploy_stage_after`  | After each stage completes                                  | **hierarchical** | `STRATA_STAGE`, `STRATA_DRY_RUN`                |
| `deploy_configure`    | After all stages finish, before `deploy_run_after`          | config           | `STRATA_FILE`, `STRATA_STAGE`, `STRATA_DRY_RUN` |
| `deploy_run_after`    | After all provisioning completes                            | config           | `STRATA_FILE`, `STRATA_STAGE`, `STRATA_DRY_RUN` |

#### Hierarchical execution

Phases marked **hierarchical** run scripts at each level of the service tree in order:

```
configuration → workspace → namespaces → providers → resources → modules
```

Scripts at each level run before the next level starts. A non-zero exit at any level aborts the remaining levels and the deployment stage.

Each level injects additional `STRATA_*` variables so scripts can self-scope:

| Level     | Extra variable     |
| --------- | ------------------ |
| namespace | `STRATA_NAMESPACE` |
| provider  | `STRATA_PROVIDER`  |
| resource  | `STRATA_RESOURCE`  |
| module    | `STRATA_MODULE`    |

Phases without a **hierarchical** scope (`deploy_run_before`, `deploy_configure`, `deploy_run_after`) run only at the configuration level.

### deploy destroy

| Phase                   | Trigger                                 | Context variables                               |
| ----------------------- | --------------------------------------- | ----------------------------------------------- |
| `deploy_destroy_before` | Before `strata deploy destroy` starts   | `STRATA_FILE`, `STRATA_STAGE`, `STRATA_DRY_RUN` |
| `deploy_destroy_after`  | After `strata deploy destroy` completes | `STRATA_FILE`, `STRATA_STAGE`, `STRATA_DRY_RUN` |

### deploy health

| Phase           | Trigger                                               | Context variables |
| --------------- | ----------------------------------------------------- | ----------------- |
| `deploy_health` | Before health checks execute (`strata deploy health`) | `STRATA_FILE`     |

### ScriptDeployer steps

When a deployment stage uses `provisioner: script`, each step maps to a lifecycle phase:

| Step           | Phase                 | Context variables                        |
| -------------- | --------------------- | ---------------------------------------- |
| `setup`        | `deploy_setup`        | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `check`        | `deploy_check`        | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `plan`         | `deploy_plan`         | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `apply`        | `deploy_apply`        | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `destroy`      | `deploy_destroy`      | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `plan_destroy` | `deploy_plan_destroy` | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |
| `output`       | `deploy_output`       | `STRATA_STAGE_NAME`, `STRATA_BUILD_PATH` |

## Phase Naming Convention

Format: `{command}_{action}[_before|_after]`

- `{command}` — CLI group: `build`, `deploy`, `validate`, `solution`, `config`
- `{action}` — operation: `run`, `stage`, `plan`, `apply`, `destroy`, `validate`, `configure`, `health`, `generate`, `fetch`, `clean`, `init`, `update`
- `before`/`after` suffix — hooks that fire before or after the named action; phases without a suffix are the action itself

## Gate Behaviour

Some phases block the next step when they fail:

- `deploy_plan_after` — non-zero exit **blocks** the apply step
- `deploy_apply_before` — non-zero exit **blocks** the apply step
- All other before/after hooks — non-zero exit **aborts** the command

## Examples

**Pre-apply backup:**

```yaml
lifecycle:
  deploy_apply_before:
    scripts:
      - scripts/backup-terraform-state.sh
```

**Validate custom tool versions before build:**

```yaml
lifecycle:
  build_validate:
    scripts:
      - scripts/check-tool-versions.py
```

**Post-apply Slack notification:**

```yaml
lifecycle:
  deploy_apply_after:
    scripts:
      - scripts/notify-slack.py
```

In `notify-slack.py`:

```python
import os
stage = os.environ["STRATA_STAGE"]
phase = os.environ["STRATA_PHASE"]
print(f"Stage {stage} completed ({phase})")
```

## Best Practices

- **Order matters:** Scripts within a phase execute in the order listed in YAML
- **Idempotency:** Design scripts to be safe to run multiple times
- **Exit codes:** Exit non-zero on failure — strata treats any non-zero as an error
- **Logging:** Write to stdout/stderr; strata captures and logs both
- **Timeout:** 5 minutes default per script; design accordingly