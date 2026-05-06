# XYZ Platform CLI - Exit Codes

## Exit Code Definitions

| Code | Meaning                | Description                                                     |
| ---- | ---------------------- | --------------------------------------------------------------- |
| 0    | Success                | Operation completed successfully                                |
| 1    | System/Execution Error | Crashes, missing files, initialization failures, exceptions     |
| 2    | Usage Error            | Invalid CLI arguments, missing required options (Click handles) |
| 3    | Validation Failure     | File processed but contains validation errors                   |

## Usage by Command

| Command    | Exit 0 | Exit 1 | Exit 2       | Exit 3         |
| ---------- | ------ | ------ | ------------ | -------------- |
| `validate` | ✅      | ✅      | ⚠️ Click only | ✅ Invalid file |
| `config`   | ✅      | ✅      | ⚠️ Click only | ❌              |
| `session`  | ✅      | ✅      | ⚠️ Click only | ❌              |
| `tools`    | ✅      | ✅      | ⚠️ Click only | ❌              |

**Legend:** ✅ Used | ⚠️ Automatic | ❌ Not used

## Examples

### Bash

```bash
xyz-platform validate config.yaml
if [ $? -eq 3 ]; then
    echo "Invalid configuration"
fi
```

### PowerShell

```powershell
xyz-platform validate config.yaml
if ($LASTEXITCODE -eq 3) {
    Write-Error "Invalid configuration"
}
```