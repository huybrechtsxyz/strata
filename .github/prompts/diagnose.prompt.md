---
agent: agent
description: "Diagnose and fix strata build or deploy errors"
---

Diagnose the error in the strata workspace.

Run the following to collect context:
```bash
strata sln status
strata guide show
```

Then identify whether the problem is:

**Validation error (exit 3)** — the YAML file has a structural or cross-reference problem.
- Read the `errors` array from the output
- Open the failing file and fix the flagged fields
- Re-run `strata validate <file>` until it passes
- Common cause: using fields that don't exist (models use `extra="forbid"`)

**Build error (exit 1)** — the build failed at runtime.
- Run with `--verbose` to get more detail: `strata build run -f <file> --dry-run --verbose`
- Check if referenced files exist: `@repo/path` references require the repo to be registered
- Check `strata repo list` and `strata tools status`

**Deploy error (exit 1)** — the deploy step failed.
- Run `strata deploy status -f <file>` to see current state
- Check `strata build run -f <file> --dry-run --verbose` for the root cause
- If Terraform: check the plan output
- If compose/helm: check service logs

After diagnosing, fix the root cause, validate the fix, and confirm the workspace is healthy:
```bash
strata sln status
strata validate <fixed-file>
```
