<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Absorbs: z-strata-output.md
  Supersedes: strata deploy status (live outputs), strata deploy outputs
  Status: Ready to implement (step 3 in implementation order)
-->

# Feature: `strata env output` — Live Terraform Outputs

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env output` to retrieve and display live Terraform outputs from deployed infrastructure. Replaces the need to manually locate build directories and run `tofu output` directly.

**Goal:** Answer "What are the live values from my deployed infrastructure?" without leaving strata.

---

## Motivation

- After `strata deploy run`, getting terraform outputs requires manually locating the opaque build directory (`build/{deployment_name}-{version}/terraform/{provisioner_name}/`)
- `strata deploy status` currently mixes live outputs with saved plan display — two unrelated operations behind one command
- `strata deploy outputs` exists but is under the deploy group; read-only inspection belongs in `env`
- Operators need outputs for scripting (e.g., `HEARTH_IP=$(strata env output ... --name hearth_public_ip --raw)`)

---

## Command Interface

```bash
strata env output -f deploy-prd.yaml                          # All outputs, table format
strata env output -f deploy-prd.yaml --name hearth_public_ip  # Single output value
strata env output -f deploy-prd.yaml --raw                    # Raw value (scripting)
strata env output -f deploy-prd.yaml --json                   # Raw JSON passthrough
strata env output -f deploy-prd.yaml --provisioner haven_iac  # Filter to specific provisioner
```

### Options

| Flag                     | Description                                      |
| ------------------------ | ------------------------------------------------ |
| `-f, --file FILE`        | Deployment file (required, same as build/deploy) |
| `--name NAME`            | Single output value only (machine-readable)      |
| `--provisioner NAME`     | Filter to a specific provisioner (default: all)  |
| `--raw`                  | Raw value, no formatting (for shell scripting)   |
| `--json`                 | Emit raw JSON (same as `tofu output -json`)      |
| `--output [table\|json]` | Output format (default: table)                   |

---

## Output Format (Console)

```
Provisioner: haven_iac
┌──────────────────┬────────────────────────────────┐
│ Output           │ Value                          │
├──────────────────┼────────────────────────────────┤
│ hearth_public_ip │ 65.21.x.x                      │
│ hearth_private_ip│ 10.0.0.2                       │
│ hearth_server_id │ 12345678                       │
│ network_cidr     │ 10.0.0.0/16                    │
│ platform         │ {workspace: haven, env: prd…}  │
└──────────────────┴────────────────────────────────┘
```

Single value:
```bash
$ strata env output -f deploy-prd.yaml --name hearth_public_ip --raw
65.21.x.x
```

Multiple provisioners:
```
Provisioner: haven_iac
┌──────────────────┬────────────────────┐
│ Output           │ Value              │
├──────────────────┼────────────────────┤
│ hearth_public_ip │ 65.21.x.x          │
└──────────────────┴────────────────────┘

Provisioner: forge_iac
┌──────────────────┬────────────────────┐
│ Output           │ Value              │
├──────────────────┼────────────────────┤
│ forge_endpoint   │ https://forge.xyz  │
└──────────────────┴────────────────────┘
```

---

## Architecture

```
commands/
  env/
    output_command.py            ← EnvOutputCommand extends BaseCommand
controllers/
  env_output_controller.py       ← Resolves build path, invokes tofu output
```

### Layer Rules

- `EnvOutputCommand` → `EnvOutputController` → `TerraformIntegration`
- `INIT_REQUIRED = True` — needs solution context to resolve build paths
- Reuse build path resolution from existing deploy infrastructure

### Build Path Resolution

The working directory for `tofu output` is: `build/{deployment_name}-{version}/terraform/{provisioner_name}/`

This is the same path that `strata deploy run` already resolves. Reuse that resolver — do not duplicate.

---

## Behaviour

1. Read the deployment file (`-f`) to resolve the active profile and build output path
2. For each terraform provisioner defined in the deployment:
   - Resolve `build/{deployment_name}-{version}/terraform/{provisioner_name}/`
   - Run `tofu output -json` in that directory
   - Parse and display the results
3. If the build directory doesn't exist → hard-fail: `"No build output found. Run 'strata build run' first."`
4. If TF state has no outputs yet (pre-apply) → hard-fail: `"No outputs available — infrastructure may not have been applied yet."`

### Sensitive Values

Terraform marks some outputs `sensitive = true` — they show as `(sensitive value)` in terraform's own output. Strata passes that through as-is — **never unwrap sensitive values**.

---

## Exit Codes

| Code | Meaning                                               |
| ---- | ----------------------------------------------------- |
| 0    | Outputs retrieved successfully                        |
| 1    | Build directory missing or terraform error            |
| 2    | No state (pre-apply, infrastructure not yet deployed) |

---

## Deprecation: `strata deploy status` / `strata deploy outputs`

- `strata deploy status` (live outputs mode): print deprecation warning → point to `strata env output`
- `strata deploy outputs`: print deprecation warning → point to `strata env output`
- Both remain functional during deprecation period
- Remove once `strata env output` is verified in CI

---

## Acceptance Criteria

- [ ] `strata env output -f FILE` displays all terraform outputs in table format
- [ ] `--name` returns a single output value
- [ ] `--raw` emits unformatted value (scriptable)
- [ ] `--json` passes through raw `tofu output -json`
- [ ] `--provisioner` filters to a specific provisioner
- [ ] Multiple provisioners grouped with headers
- [ ] Hard-fail with clear message when no build output exists
- [ ] Hard-fail with clear message when no state exists
- [ ] Sensitive outputs shown as `(sensitive value)`, never unwrapped
- [ ] `strata deploy status` and `strata deploy outputs` show deprecation warnings

## Relationships

- **Absorbs:** `z-strata-output.md` (design carried forward, command renamed to `env output`)
- **Supersedes:** `strata deploy status` (live outputs) and `strata deploy outputs`
- **Depends on:** `x-strata-cli-info.md` (env group must exist first)
- **Related:** `z-strata-commands.md` (already proposed `env output` placement)
