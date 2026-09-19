# strata CLI - Exit Codes

## Exit Code Definitions

| Code | Meaning                | Description                                                           |
| ---- | ---------------------- | --------------------------------------------------------------------- |
| 0    | Success                | Operation completed successfully                                      |
| 1    | System/Execution Error | Crashes, missing files, initialization failures, exceptions           |
| 2    | Usage Error            | Invalid CLI arguments, missing required options (Click handles)       |
| 3    | Refused                | Processed, then declined — schema invalid, or a policy/review said no |
| 4    | Lock Conflict          | Deployment locked by another process (safe to retry)                  |
| 5    | Hand-off Required      | Gate paused deployment; awaiting approval/verification                |

`3` and `1` are the pair worth getting right in a pipeline: `1` means something
broke and retrying may help; `3` means strata understood the request and refused
it, so retrying unchanged will refuse again. `5` is neither — the deploy is paused
and resumable with `--resume`.

## Usage by Command

| Command          | 0   | 1   | 2            | 3                                     | 4           | 5             |
| ---------------- | --- | --- | ------------ | ------------------------------------- | ----------- | ------------- |
| `sln`            | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `config`         | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `repo`           | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `profile`        | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `ref`            | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `validate`       | ✅   | ✅   | ⚠️ Click only | ✅ Schema-invalid file                 | ❌           | ❌             |
| `build run`      | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |
| `build plan`     | ✅   | ✅   | ⚠️ Click only | ✅ `--strict-ai-review` rejected it    | ❌           | ❌             |
| `policies check` | ✅   | ✅   | ⚠️ Click only | ✅ A `deny` policy failed              | ❌           | ❌             |
| `deploy run`     | ✅   | ✅   | ⚠️ Click only | ✅ Invalid, or a policy/review said no | ✅ Lock held | ✅ Gate paused |
| `deploy destroy` | ✅   | ✅   | ⚠️ Click only | ✅ Schema-invalid file                 | ✅ Lock held | ❌             |
| `deploy health`  | ✅   | ✅   | ⚠️ Click only | ✅ Schema-invalid file                 | ❌           | ❌             |
| `values`         | ✅   | ✅   | ⚠️ Click only | ✅ Schema-invalid file                 | ❌           | ❌             |
| `log`            | ✅   | ✅   | ⚠️ Click only | ❌                                     | ❌           | ❌             |

**Legend:** ✅ Used | ⚠️ Automatic | ❌ Not used

Every command built on `BaseDeployCommand` (`deploy *`, `values *`, `cost *`) can
return `3` when the deployment file itself fails schema/cross-reference validation
— that check runs before the command does anything. Only `deploy run` and
`policies check` additionally return `3` for a *refusal*.

> Corrected 2026-09-19. This table previously claimed `build run` returns `3` for
> an invalid deployment (it has no exit-3 classification at all — every failure is
> `1`) and that `values` returns `3` for unresolved entries (unresolved values exit
> `1`; `3` there means the file is schema-invalid).

## Examples

### Bash

```bash
strata validate config.yaml
if [ $? -eq 3 ]; then
    echo "Invalid configuration"
fi
```

### Distinguishing a refusal from a breakage

```bash
strata deploy run -f deploy-prd.yaml
case $? in
  0) echo "Deployed" ;;
  3) echo "Refused — a policy or review said no. Do not retry." ; exit 1 ;;
  4) echo "Locked — retry shortly" ;;
  5) echo "Paused at a gate — approve, then re-run with --resume" ;;
  *) echo "Execution failure — alert" ; exit 1 ;;
esac
```

### PowerShell

```powershell
strata validate config.yaml
if ($LASTEXITCODE -eq 3) {
    Write-Error "Invalid configuration"
}
```