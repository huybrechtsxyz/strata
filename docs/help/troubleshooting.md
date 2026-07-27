# Troubleshooting

The most common errors operators hit, and exactly how to fix them.

## "Not inside an strata workspace"

**Cause:** strata cannot find a `.strata/solution.json` by walking up from the
current directory, and no `--work-path` or `STRATA_WORK_PATH` was provided.

**Fix:**
```
cd /path/to/your/workspace          # move into the workspace root
# OR
strata <command> --work-path /path/to/workspace
# OR
$env:STRATA_WORK_PATH = "C:\path\to\workspace"   # PowerShell
export STRATA_WORK_PATH=/path/to/workspace        # bash
```

## Unresolved `@repo_name/` Reference

**Cause:** A ref path uses `@repo_name/...` but that repo isn't registered, or
the repo path on disk doesn't exist (not yet cloned).

**Fix:**
```
strata repo list                      # verify the repo is registered
strata repo sync                      # clone/pull registered repos
strata ref configfile list --profile dev   # verify the path is correct
```

## Exit Code 3 — Validation Failure

**Cause:** A file was processed but failed semantic validation (e.g., a YAML
file loaded but a required field is missing or a cross-reference is broken).

**What to do:** Exit code 3 means the tool ran successfully but the content is
invalid. Check `strata audit list --level warning` for the specific validation error.
This is different from exit code 1 (system crash or missing file).

## Exit Code 1 — System Failure

**Cause:** The tool crashed, a required file is missing, or initialization
failed (e.g., `solution.json` not found but `INIT_REQUIRED = True`).

**What to do:** Check `strata audit list --level error`. Common causes:
- Running a command that requires `strata sln init` first
- `solution.json` is corrupted or missing — re-run `strata sln init`
- A required integration (git, terraform) is not installed

## `service not validated` Error

**Cause:** Phase-2 validation failed. A service loaded its model (phase 1 OK)
but cross-reference checks failed — e.g., a ref points to a repo that doesn't
exist in the solution.

**Fix:** Review the error message for which service and which reference failed.
Correct the YAML and re-run. Check `strata audit list --level debug` for the full
validation trace.

## Stale State After a Failed `strata sln init`

If `strata sln init` failed partway through, `.strata/` may exist in a partial state.

**Fix:**
```
strata sln clean                          # removes .strata/ cleanly
strata sln init --name myproject          # re-run from scratch
```

## Config Looks Wrong / Missing Values

Check in order:
1. `strata profile list` — is the right profile active?
2. `strata ref configfile list --profile dev` — are all refs registered?
3. `cat .strata/configuration.yaml` — what did the last merge produce?
4. `strata audit list --level debug` — were any files skipped due to missing paths?

See also: `strata help --topic config-merge`, `strata help --topic workspace`
