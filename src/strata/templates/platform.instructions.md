---
description: Instructions for AI agents working with the strata CLI tool to manage infrastructure deployments
applyTo: '**'
---

# strata — Agent Operating Instructions

You are working with **strata**, a DevOps CLI tool for managing infrastructure-as-code deployments. This file tells you how to use the CLI effectively as an AI agent.

---

## Quick Start

```bash
# Always use JSON output for machine-readable responses
export STRATA_OUTPUT=json

# Point to workspace (or let auto-discovery find .strata/)
export STRATA_WORK_PATH=/path/to/workspace

# Validate before any deploy
xyz validate <file> --output json

# Build artifacts
xyz build run -f <deployment.yaml> --output json

# Deploy (with dry-run first)
xyz deploy run -f <deployment.yaml> --dry-run --output json
```

---

## CLI Structure

Commands follow a flat `xyz <group> <command>` pattern:

| Group | Key Commands | Purpose |
|-------|-------------|---------|
| `init` | — | Initialize workspace (creates `.strata/`) |
| `config` | `set` `unset` `list` | Manage workspace defaults |
| `validate` | — | Validate a YAML file against schema |
| `build` | `run` `plan` `clean` | Build platform & Terraform artifacts |
| `deploy` | `run` `destroy` `status` `history` `health` | Deploy infrastructure |
| `audit` | `list` | View execution logs |
| `repo` | `add` `remove` `list` `sync` `status` | Manage solution repositories |
| `profile` | `add` `remove` `list` `activate` `show` | Manage environment profiles |
| `ref` | `env` `config` `data` `secret` | Manage file references in profiles |
| `values` | `list` `get` | Inspect resolved deployment values |
| `schema` | `list` `get` | Inspect YAML schemas |
| `tools` | `status` `check` | Verify external tool availability |
| `status` | — | Show workspace health |
| `version` | — | Show CLI version |

---

## Standard Flags

Every command accepts these:

| Flag | Env Var | Default | Purpose |
|------|---------|---------|---------|
| `--work-path PATH` | `STRATA_WORK_PATH` | auto-detected | Workspace root |
| `--output FORMAT` | `STRATA_OUTPUT` | `console` | Output format: `console`, `text`, `json` |
| `--verbose` | `STRATA_VERBOSE` | off | Verbose output |
| `--quiet` | `STRATA_QUIET` | off | Suppress output |

**Priority:** explicit flag → env var → `.strata/cli.yaml` → built-in default.

---

## Exit Codes

| Code | Meaning | Agent Action |
|------|---------|--------------|
| `0` | Success | Proceed normally |
| `1` | System/execution failure | Read `messages` in JSON output for crash reason |
| `2` | Usage error (bad arguments) | Fix command syntax |
| `3` | Validation failure | Read `errors` array in JSON output for specifics |

**Always check exit code first.** Exit 3 means the file was processed but is invalid — inspect the errors array.

---

## Output Format

Always pass `--output json` (or set `STRATA_OUTPUT=json`). JSON responses use a standard envelope:

```json
{
  "success": true,
  "data": { ... },
  "errors": [],
  "messages": []
}
```

- `success` — boolean, check this first
- `data` — command-specific payload
- `errors` — array of validation/execution errors (populated when exit code = 3)
- `messages` — informational messages, warnings, or failure context

---

## Work Path Resolution

The CLI auto-discovers the workspace by walking up from CWD looking for a `.strata/` directory. Override with:

1. `--work-path /explicit/path` (highest priority)
2. `STRATA_WORK_PATH=/env/path` (environment variable)
3. Automatic upward walk from CWD

**Recommendation:** Set `STRATA_WORK_PATH` in your environment to avoid ambiguity.

---

## Validation Workflow

```bash
# Phase 1 — structural validation (schema check)
xyz validate path/to/file.yaml --output json

# Phase 2 — deep validation (cross-reference checks, requires active profile)
xyz validate path/to/file.yaml --deep --output json
```

**Important:**
- `validate` processes one file at a time
- Use `--deep` only when a profile is active (otherwise exit 1)
- Always validate before build/deploy

---

## Build Workflow

```bash
# Full build (generates Terraform artifacts)
xyz build run -f deploy/deploy-prd.yaml --output json

# Dry-run (validate + plan without writing)
xyz build run -f deploy/deploy-prd.yaml --dry-run --output json

# Show what would change (diff existing vs new artifacts)
xyz build plan -f deploy/deploy-prd.yaml --output json

# Limit to a single stage
xyz build plan -f deploy/deploy-prd.yaml --stage staging --output json

# Artifacts diff only (skip terraform plan)
xyz build plan -f deploy/deploy-prd.yaml --artifacts-only --output json

# Clean build artifacts
xyz build clean -f deploy/deploy-prd.yaml --output json
```

---

## Deploy Workflow

```bash
# Always dry-run first
xyz deploy run -f deploy/deploy-prd.yaml --dry-run --output json

# Execute deploy (--force skips confirmation prompts)
xyz deploy run -f deploy/deploy-prd.yaml --force --output json

# Limit to a specific stage
xyz deploy run -f deploy/deploy-prd.yaml --stage networking --force --output json

# Check current state
xyz deploy status -f deploy/deploy-prd.yaml --output json

# View deployment history
xyz deploy history -f deploy/deploy-prd.yaml --output json

# Health check
xyz deploy health -f deploy/deploy-prd.yaml --output json

# Destroy (requires --force)
xyz deploy destroy -f deploy/deploy-prd.yaml --force --output json
```

**Caution:** `deploy run` and `deploy destroy` are long-running operations. They may take minutes and produce no output until completion.

---

## Audit & Debugging

```bash
# Last execution only
xyz audit list --last --output json

# Filter by level
xyz audit list --level ERROR --output json

# Filter by execution ID
xyz audit list --execution-id <id> --output json

# Last N minutes
xyz audit list --minutes 10 --output json

# Check tool availability
xyz tools status --output json
```

---

## File References & Cross-Repo Paths

YAML files use `@repo_name/relative/path.yaml` notation for cross-repository references:

```yaml
spec:
  source: "@haven/config/config.yaml"
```

The `@repo_name` prefix resolves via the solution's repository map. Repositories are managed with `xyz repo add|remove|list`.

---

## YAML Document Structure

All platform YAML files follow Kubernetes-style structure:

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>
  annotations:
    description: "..."
  labels:
    version: "1.0.0"
spec:
  ...
```

Valid kinds: `deployment`, `workspace`, `configuration`, `environment`, `namespace`, `module`, `resource`, `provider`, `firewall`, `datacenter`.

---

## Workspace State

```
.strata/                  # State directory
├── solution.json           # Solution registry (repos, profiles)
├── cli.yaml                # Workspace defaults
└── logging.yaml            # Logging configuration
```

- `solution.json` — managed by the CLI, do not edit manually
- `cli.yaml` — user preferences, manage via `xyz config set|unset|list`

---

## Environment Variables Injected During Execution

These are available in lifecycle scripts during build/deploy:

| Variable | Content |
|----------|---------|
| `STRATA_PHASE` | Current lifecycle phase (e.g., `deploy_provision_before`) |
| `STRATA_WORKSPACE_PATH` | Path to workspace root |
| `STRATA_CONFIG_PATH` | Path to configuration files |
| `STRATA_BUILD_PATH` | Path to build artifacts |
| `STRATA_OBJECT_PATH` | Path to objects directory |

---

## Agent Best Practices

1. **Always use `--output json`** — parse structured responses, never scrape console output.
2. **Check exit code first** — `3` means validation errors (read `errors`); `1` means system failure (read `messages`).
3. **Set `STRATA_WORK_PATH`** — eliminates ambiguity about which workspace you're targeting.
4. **Validate before deploy** — `xyz validate` is safe and read-only. Run it first.
5. **Use `--dry-run`** — available on `build run`, `build clean`, `deploy run`, `deploy destroy`.
6. **Use `--force` for automation** — skips interactive confirmation prompts.
7. **Use `xyz audit list --last --output json`** — inspect what the last command actually did.
8. **Use `xyz tools status`** — verify required tools (terraform, git, docker) are available before operations.
9. **Long operations produce no streaming output** — `deploy run` and `build run` may take minutes. Set appropriate timeouts.
10. **Profile must be active for deep validation** — activate with `xyz profile activate <name>` before `validate --deep`.

---

## Common Workflows

### Initial Setup
```bash
xyz init --output json
xyz repo add --name haven --path ../xyz-configuration --output json
xyz profile add --name prd --output json
xyz profile activate prd --output json
```

### Validate → Build → Deploy
```bash
xyz validate deploy/deploy-prd.yaml --output json
xyz build run -f deploy/deploy-prd.yaml --output json
xyz deploy run -f deploy/deploy-prd.yaml --dry-run --output json
xyz deploy run -f deploy/deploy-prd.yaml --force --output json
```

### Troubleshooting a Failed Deploy
```bash
xyz audit list --last --level ERROR --output json
xyz deploy status -f deploy/deploy-prd.yaml --output json
xyz tools status --output json
```
