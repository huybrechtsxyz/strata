# strata CLI - Exit Codes

## Exit Code Definitions

| Code | Meaning                | Description                                                     |
| ---- | ---------------------- | --------------------------------------------------------------- |
| 0    | Success                | Operation completed successfully                                |
| 1    | System/Execution Error | Crashes, missing files, initialization failures, exceptions     |
| 2    | Usage Error            | Invalid CLI arguments, missing required options (Click handles) |
| 3    | Validation Failure     | File processed but contains validation errors                   |
| 4    | Lock Conflict          | Deployment locked by another process (safe to retry)            |
| 5    | Hand-off Required      | Gate paused deployment; awaiting approval/verification          |

## Usage by Command

| Command    | Exit 0 | Exit 1 | Exit 2       | Exit 3                |
| ---------- | ------ | ------ | ------------ | --------------------- |
| `sln`      | ✅      | ✅      | ⚠️ Click only | ❌                     |
| `config`   | ✅      | ✅      | ⚠️ Click only | ❌                     |
| `repo`     | ✅      | ✅      | ⚠️ Click only | ❌                     |
| `profile`  | ✅      | ✅      | ⚠️ Click only | ❌                     |
| `ref`      | ✅      | ✅      | ⚠️ Click only | ❌                     |
| `validate` | ✅      | ✅      | ⚠️ Click only | ✅ Schema-invalid file |
| `build`    | ✅      | ✅      | ⚠️ Click only | ✅ Invalid deployment  |
| `deploy`   | ✅      | ✅      | ⚠️ Click only | ✅ Invalid deployment  | ✅ Lock held by other process | ✅ Gate paused |
| `values`   | ✅      | ✅      | ⚠️ Click only | ✅ Unresolved entries  |
| `log`      | ✅      | ✅      | ⚠️ Click only | ❌                     |

**Legend:** ✅ Used | ⚠️ Automatic | ❌ Not used

## Examples

### Bash

```bash
strata validate config.yaml
if [ $? -eq 3 ]; then
    echo "Invalid configuration"
fi
```

### PowerShell

```powershell
strata validate config.yaml
if ($LASTEXITCODE -eq 3) {
    Write-Error "Invalid configuration"
}
```