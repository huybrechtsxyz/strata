# How To: Run a Script as a Pipeline Step (`tool: script`)

A `script` provisioner runs a `.ps1`, `.sh`, `.bash`, `.py` or `.bat` file as an ordinary step of a workspace. This guide
builds one around a real case: a script that manages secrets and does three things, `plan`, `reload` and `revoke`.
A schedule runs `reload`, a person runs `revoke` for a list of records, and a preview must never change anything.

How it works, and why, is in [script-provisioner.md](../design/script-provisioner.md). The fields are in
[workspace.md](../config/workspace.md#script-provisioner).

## 1. Declare the step

```yaml
kind: workspace
meta:
  name: secrets
spec:
  provisioners:
    - name: secrets-script
      tool: script
      source:
        source_path: scripts/secrets
      script:
        file: secrets.py
        args: [--action, reload]
        plan_args: [--action, plan]
        destroy_args: [--action, revoke, --records, "${var:revoke_records}"]
        values: [VAULT_TOKEN]
  execution:
    - name: manage-secrets
      provisioner: secrets-script
      resources: [placeholder]
```

Each list is one phase of the same script. (A step needs at least one target; `resources` here names a resource of
the workspace.)

| Command                                          | Runs the script with                                 | `STRATA_PHASE` |
| ------------------------------------------------ | ---------------------------------------------------- | -------------- |
| `strata deploy run D`                            | `args`                                               | `apply`        |
| `strata deploy run D --dry-run`, `deploy plan D` | `plan_args`, when set; otherwise nothing is executed | `plan`         |
| `strata deploy destroy D --force`                | `destroy_args`                                       | `destroy`      |
| `strata deploy destroy D --dry-run`              | nothing: it prints `would run: ...`                  | none           |

- Without `plan_args`, a preview prints the command it would run and executes nothing.
- Without `destroy_args`, the step cannot be destroyed, and `deploy destroy` refuses it before anything is removed.
  Leave it out with `--stage` or `--scope`.
- A real `deploy run` never runs `plan_args`. It would otherwise run the script twice.
- Every argument can use value tokens: `${var:}`, `${secret:}`, `${feature:}`, `${output:}` and `${deployment:}`.

## 2. Write the script

strata cannot check that a script is free of side effects when it runs with `plan_args`. The script has to guard
itself. `STRATA_PHASE` says which phase it was started in, whatever the arguments are:

```python
import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--action", choices=["plan", "reload", "revoke"], required=True)
parser.add_argument("--records", default="")
args = parser.parse_args()

# A preview must not change anything, even when plan_args is wrong.
phase = os.environ["STRATA_PHASE"]
if phase == "plan" and args.action != "plan":
    sys.exit(f"refusing '{args.action}' during a preview")
if phase == "destroy" and args.action != "revoke":
    sys.exit(f"refusing '{args.action}' during a destroy")

token = json.loads(os.environ["STRATA_VALUES"])["VAULT_TOKEN"]

# ... your logic: plan, reload or revoke, using `token` ...

with open(os.environ["STRATA_OUTPUT_FILE"], "w", encoding="utf-8") as handle:
    json.dump({"version": 7}, handle)
```

The same guard in Bash and PowerShell:

```bash
if [ "$STRATA_PHASE" = "plan" ] && [ "$ACTION" != "plan" ]; then
  echo "refusing '$ACTION' during a preview" >&2
  exit 1
fi
```

```powershell
if ($env:STRATA_PHASE -eq 'plan' -and $Action -ne 'plan') {
    Write-Error "refusing '$Action' during a preview"
    exit 1
}
```

What the script receives, besides its arguments:

| Environment variable | Holds                                                                          |
| -------------------- | ------------------------------------------------------------------------------ |
| `STRATA_PHASE`       | `plan`, `apply` or `destroy`.                                                  |
| `STRATA_VALUES`      | JSON: the resolved values (see step 3).                                        |
| `STRATA_OUTPUT_FILE` | A path the script may write a JSON object to (see step 4). Set in every phase. |

The working directory is the step's directory in the build output. The process also inherits the pipeline's own
environment.

## 3. Give it only the values it needs

By default `STRATA_VALUES` holds every resolved value of the deployment, so a script that needs one token also
receives every unrelated secret. List the keys it needs in `values`:

```yaml
values: [VAULT_TOKEN]
```

The script then receives `VAULT_TOKEN`, `deployment:name` and `deployment:tenant` (when the deployment has a tenant).
An empty list `[]` delivers only the two `deployment:` entries.

- Keys are variable, secret and feature keys, and `<step>.<key>` for an output of an earlier step.
- `strata validate` reports a key that no environment of the deployment declares. A key that does not resolve at
  deploy time fails the step before the script runs, in a preview too.
- `values` limits `STRATA_VALUES` only. Arguments can still use any token, and a secret in an argument is visible in
  the process list of the machine. Prefer `STRATA_VALUES` for a secret.

## 4. Pass results to the next step

A script writes one JSON object to the file named by `STRATA_OUTPUT_FILE`. The steps that depend on it read the keys as
`${output:<step>.<key>}`:

```json
{ "version": 7, "token": "abc", "_sensitive": ["token"] }
```

```yaml
- name: use-secrets
  provisioner: consumer
  depends_on: [manage-secrets]
  resources: [placeholder]
```

with `args: [--version, "${output:manage-secrets.version}"]` on the consumer.

- Values may be any JSON. Anything other than a string is passed on as its JSON text: `7` becomes `"7"`.
- `_sensitive` lists the keys that are secret. They can be used by later steps in the same run and are never recorded
  in the workspace's `output_store`. A key that is not listed is recorded, as in Terraform.
- Write nothing, or `{}`, for no outputs.
- A file that is not valid JSON, is not an object, or has a key a token cannot name gives no outputs at all and a
  warning naming the step. The step itself does not fail.
- Only a successful `deploy run` produces outputs. A preview and a destroy collect none.
- The file is in a private directory outside the build output and is removed when the script ends.

## 5. Trust it step by step

A preview that runs the script is only as safe as the script. Before it goes on a schedule:

1. `strata deploy run D --smoke-test` runs nothing and reports which steps would run. A value that cannot be resolved
   fails it.
2. `strata deploy run D --dry-run --follow` in a test environment. The script runs with `plan_args`; `--follow` shows
   its output (`deploy plan` follows by default). A secret in the output is redacted.
3. `strata deploy destroy D --dry-run` shows `would run: ...` for the destroy and executes nothing.
4. Only then `strata deploy run D` on the schedule.

## Troubleshooting

- **The preview only prints `would run: ...`.** `plan_args` is not set, so nothing was executed. Set it, or accept the
  command line as the preview.
- **`does not support destroy: set 'destroy_args'`.** The provisioner has no `destroy_args`.
- **`script.values lists 'X', which did not resolve to a value`.** `X` is not declared by the deployment's environments,
  or is an output of a step that has not run in this invocation. A destroy only has outputs recorded in the
  `output_store`.
- **A failed step shows `exit code N`** and the last 10 lines of what the script printed, with secrets redacted. A
  script that was killed by its timeout says `timed out and was killed`. Raise it with `timeouts.apply` on the
  deployment stage or `STRATA_TIMEOUT_<STEP>_APPLY`.
- **`deploy apply` refuses a script step.** A script writes no saved plan. Use `deploy run`, or leave the step out
  with `--stage` or `--scope`.
- **`build run` says the script file does not exist.** `script.file` is relative to the step's `source`.
- **The interpreter.** `.py` runs with the first `python3` or `python` on `PATH`, not strata's own Python, so the script
  can use its own packages. `.ps1` prefers `pwsh`, `.sh` and `.bash` use `bash`, and `.bat` needs Windows.

See [ci-deploy-pipeline.md](ci-deploy-pipeline.md) for putting these commands in a pipeline.
