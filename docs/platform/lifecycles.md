# Lifecycles

Extension points for custom scripts during command execution. LifecycleController runs scripts from phase-specific directories.

**Scripts:** `.ps1`, `.sh`, `.bat/.cmd`, `.py` | **Skip:** `--no-hooks` | **Context:** `XYZ_*` environment variables

## Configuration

Scripts defined in YAML (workspace/namespace/module/provider/resource):

```yaml
lifecycle:
  config_fetch_before:
    scripts:
      - config/scripts/check-network.ps1
      - setup/verify-disk.sh
  deploy_provision:
    scripts:
      - provision-vm.sh
```

**Paths:** Relative to work path | **Order:** As listed in YAML

## Platform-Agnostic Execution

**Windows:** `script.ps1` → tries `.ps1`, `.bat`, `.cmd`, `.py`, `.sh`  
**Linux:** `script.ps1` → tries `.sh`, `.py`, `.ps1` (if pwsh available)

## Hierarchical Execution

Deploy commands execute top-down:

1. Workspace → 2. Namespace → 3. Module → 4. Provider → 5. Resource

Each level runs its configured scripts in order.

## Environment Variables

```bash
XYZ_PHASE=config_fetch_before
XYZ_WORKSPACE_PATH=/path/to/workspace
XYZ_CONFIG_PATH=/path/to/config
XYZ_BUILD_PATH=/path/to/build
XYZ_OBJECT_PATH=/path/to/objects
```

## Phase Naming

Format: `{command}_{action}`

**Examples:** `config_fetch_before`, `config_fetch_after`, `build_validate`, `deploy_provision`, `deploy_provision_after`

| CLI Command   | Phase Hook Examples                                                         |
| ------------- | --------------------------------------------------------------------------- |
| `deploy run`  | `deploy_provision`, `deploy_configure`, `deploy_health`, `deploy_output`    |
| `deploy run`  | `deploy_apply_before`, `deploy_apply_after`                                 |
| `build run`   | `build_validate`, `build_generate`                                          |

## Example

```yaml
# workspace.yaml
lifecycle:
  deploy_apply_before:
    scripts:
      - backup-state.ps1
  deploy_provision:
    scripts:
      - provision-vm.sh
  deploy_configure:
    scripts:
      - install-apps.py
  deploy_output:
    scripts:
      - export-ips.ps1
```

## Best Practices

- **Order matters:** Scripts execute as listed in YAML
- **Platform-agnostic:** Specify primary extension, fallback automatic
- **Naming:** Descriptive names with platform extension (`check-network.ps1`)
- **Idempotency:** Safe to run multiple times
- **Error handling:** Exit non-zero on failure, log to stdout/stderr
- **Timeout:** 5 minutes default per script