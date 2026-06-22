<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Source: z-strata-commands.md (env state list / env state show)
  Status: Ready to implement (step 4 in implementation order)
-->

# Feature: `strata env state` — Terraform State Inspection

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env state list` and `strata env state show` as thin wrappers around `tofu state list` / `tofu state show`, resolving the correct build directory automatically.

**Goal:** Answer "What resources does terraform know about?" without manually navigating build directories.

---

## Motivation

- Inspecting terraform state currently requires knowing the opaque build path and running `tofu state list` / `tofu state show` manually
- Operators troubleshooting deployment issues need quick access to state without leaving strata
- Complements `strata env output` (outputs = values, state = resources)

---

## Command Interface

```bash
strata env state list -f deploy-prd.yaml                               # List all resources in state
strata env state list -f deploy-prd.yaml --provisioner haven_iac       # Filter to provisioner
strata env state show -f deploy-prd.yaml <resource>                    # Show details of a resource
strata env state show -f deploy-prd.yaml hcloud_server.hearth          # Example
```

### Options

| Flag                    | Description                                     |
| ----------------------- | ----------------------------------------------- |
| `-f, --file FILE`       | Deployment file (required)                      |
| `--provisioner NAME`    | Filter to a specific provisioner (default: all) |
| `--output [text\|json]` | Output format (default: text)                   |

---

## Output Format

### `strata env state list`

```
Provisioner: haven_iac
  hcloud_server.hearth
  hcloud_network.main
  hcloud_network_subnet.main
  hcloud_firewall.hearth
  hcloud_rdns.hearth_ipv4
  hcloud_rdns.hearth_ipv6
```

### `strata env state show`

```
$ strata env state show -f deploy-prd.yaml hcloud_server.hearth

# hcloud_server.hearth:
resource "hcloud_server" "hearth" {
    id          = "12345678"
    name        = "haven-prd-hearth"
    server_type = "cx22"
    location    = "fsn1"
    status      = "running"
    ...
}
```

---

## Architecture

```
commands/
  env/
    state_command.py             ← EnvStateListCommand + EnvStateShowCommand (BaseCommand)
controllers/
  env_state_controller.py        ← Resolves build path, invokes tofu state
```

### Layer Rules

- `EnvStateListCommand` / `EnvStateShowCommand` → `EnvStateController` → `TerraformIntegration`
- `INIT_REQUIRED = True`
- Reuse build path resolution from existing deploy infrastructure (same as `env output`)
- Pass-through to `tofu state list` / `tofu state show` — minimal transformation

---

## Behaviour

1. Resolve build directory for the deployment file (same path resolution as `env output`)
2. For `state list`: run `tofu state list` in the provisioner's build directory
3. For `state show <resource>`: run `tofu state show <resource>` in the provisioner's build directory
4. Hard-fail with clear message when:
   - No build output exists: `"No build output found. Run 'strata build run' first."`
   - No state exists: `"No terraform state found. Infrastructure may not have been deployed yet."`

---

## Exit Codes

| Code | Meaning                                                         |
| ---- | --------------------------------------------------------------- |
| 0    | State retrieved successfully                                    |
| 1    | Build directory missing, terraform error, or resource not found |
| 2    | No state (infrastructure not yet deployed)                      |

---

## Acceptance Criteria

- [ ] `strata env state list -f FILE` lists all terraform resources
- [ ] `strata env state show -f FILE <resource>` shows resource details
- [ ] `--provisioner` filters to a specific provisioner
- [ ] Hard-fail with clear message when no build output exists
- [ ] Hard-fail with clear message when no state exists
- [ ] Output passthrough from tofu is clean and readable

## Relationships

- **Source:** `z-strata-commands.md` (proposed `env state list` / `env state show`)
- **Depends on:** `x-strata-cli-info.md` (env group), `x-strata-cli-output.md` (shared build path resolution)
- **Related:** `x-strata-cli-output.md` (outputs = values, state = resources)
