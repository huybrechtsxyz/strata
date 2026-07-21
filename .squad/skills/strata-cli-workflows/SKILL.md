---
name: "strata-cli-workflows"
description: "Comprehensive guide to strata CLI commands, exit codes, output formats, and command patterns"
domain: "cli-operations"
confidence: "high"
source: "strata.instructions.md + ADRs"
tools:
  - name: "strata_cli"
    description: "strata DevOps CLI tool"
    when: "executing any strata command; use --output json for machine-readable output"
---

## Context

The strata CLI is the primary interface for managing infrastructure-as-code deployments. It follows a flat `strata <group> <command>` structure. Understanding command patterns, exit codes, and output format is essential for agents executing deployment workflows.

All commands support `--work-path`, `--output`, `--verbose`, and `--quiet` flags. Always use `--output json` for parsing and automation.

## Exit Codes (Critical)

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Proceed normally |
| 1 | System/execution failure | Read `messages` in JSON output for crash reason |
| 2 | Usage error (bad arguments) | Fix command syntax |
| 3 | Validation failure | **Most important**: file was processed but invalid — read `errors` array in JSON output for specifics. Always check before retry. |

**Agent workflow:** Check exit code FIRST. Exit 3 ≠ crash — it means the file has structural/policy issues. Exit 1 means something broke.

## Command Groups

### sln — Solution Workspace Lifecycle

```bash
strata sln init --name <name> [--template <template>] [--guided]
strata sln update
strata sln clean
strata sln status
strata sln export
```

**When:** Initialize new projects, clean build artifacts, check solution state.

### validate — Structural & Policy Validation

```bash
strata validate <file> --output json
strata validate <file> --deep --output json
```

**Key:** Use `--deep` only when a profile is active (otherwise exit 1). Always validate before build/deploy.

### build — Generate Terraform & Platform Artifacts

```bash
strata build run -f <deployment.yaml> --output json
strata build run -f <deployment.yaml> --dry-run --output json
strata build plan -f <deployment.yaml> --output json
strata build clean -f <deployment.yaml> --output json
strata build sbom -f <deployment.yaml> --output json
strata build sbom -f <deployment.yaml> --report inventory
```

**When:** Generate deployment artifacts, plan changes, generate SBOM/inventory.

### deploy — Execute Infrastructure Deployment

```bash
strata deploy run -f <deployment.yaml> --dry-run --output json
strata deploy run -f <deployment.yaml> --force --output json
strata deploy run -f <deployment.yaml> --stage <stage-name> --force --output json
strata deploy status -f <deployment.yaml> --output json
strata deploy history -f <deployment.yaml> --output json
strata deploy health -f <deployment.yaml> --output json
strata deploy destroy -f <deployment.yaml> --force --output json
```

**When:** Deploy infrastructure, check deployment state, run health checks. Always `--dry-run` first.

### repo — Manage Configuration Repositories

```bash
strata repo add --name <name> --path <path> --output json
strata repo remove --name <name> --output json
strata repo list --output json
strata repo sync --output json
strata repo status --output json
```

**When:** Register/remove external configuration repositories (cross-repo references use `@repo_name/path.yaml`).

### profile — Environment Profile Management

```bash
strata profile create --name <name> --output json
strata profile remove --name <name> --output json
strata profile list --output json
strata profile activate <name> --output json
strata profile show --output json
```

**When:** Set up deployment environments (dev/staging/prod), activate for deep validation/deploy.

### ref — File Reference Management

```bash
strata ref env <key> --set <value> --profile <profile> --output json
strata ref config <key> --source <source> --value <value> --profile <profile> --output json
strata ref data <key> --source <source> --value <value> --profile <profile> --output json
strata ref secret <key> --source <source> --value <value> --profile <profile> --output json
```

**When:** Bind environment variables, config values, data sources, and secrets to profiles.

### values — Inspect Resolved Values

```bash
strata values list --profile <profile> --output json
strata values get <key> --profile <profile> --output json
```

**When:** Debug value resolution, check what gets injected at deploy time.

### audit — Deployment Audit Trail

```bash
strata audit list --output json
strata audit list --last --output json
strata audit list --level ERROR --output json
strata audit list --minutes 10 --output json
```

**When:** Review what happened in the last build/deploy, troubleshoot failures.

### schema — Inspect YAML Schema

```bash
strata schema list --output json
strata schema get <kind> --output json
```

**When:** Understand valid fields for a YAML kind (deployment, workspace, environment, etc.).

### tools — Verify External Tool Availability

```bash
strata tools status --output json
```

**When:** Check if terraform, ansible, git, docker are available before operations.

### guide — Workspace Readiness Checklist

```bash
strata guide show --output json
strata guide show -f <deployment.yaml> --output json
```

**When:** Step-by-step checklist for project readiness (repos added, profiles created, build done, SBOM generated, etc.).

### new — Scaffold Files from Templates

```bash
strata new --template <name> --name <file-name> --output json
strata new --list --output json
```

**When:** Generate new configuration files from templates.

## JSON Output Structure

All commands with `--output json` return this envelope:

```json
{
  "success": true,
  "data": { },
  "errors": [],
  "messages": []
}
```

- **success:** boolean — check this FIRST (replaces exit code for quick parsing)
- **data:** command-specific payload
- **errors:** array of validation/execution errors (populated when exit code = 3)
- **messages:** informational messages, warnings, failure context

## Standard Flags

| Flag | Env Var | Default | Purpose |
|------|---------|---------|---------|
| `--work-path PATH` | `STRATA_WORK_PATH` | auto-detected | Workspace root |
| `--output FORMAT` | `STRATA_OUTPUT` | `console` | Output format: `console`, `text`, `json` |
| `--verbose` | `STRATA_VERBOSE` | off | Verbose output |
| `--quiet` | `STRATA_QUIET` | off | Suppress output |

**Priority:** explicit flag → env var → `.strata/cli.yaml` → built-in default.

## Patterns & Best Practices

### Always Validate Before Deploy

```bash
strata validate deploy/deploy-prd.yaml --output json  # quick check
strata validate deploy/deploy-prd.yaml --deep --output json  # deep check (requires active profile)
strata build run -f deploy/deploy-prd.yaml --dry-run --output json  # full dry-run
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json  # deploy dry-run
```

### Use `--force` for Automation

Deployment commands require confirmation by default. Use `--force` to skip prompts:

```bash
strata deploy run -f deploy/deploy-prd.yaml --force --output json
strata deploy destroy -f deploy/deploy-prd.yaml --force --output json
```

### Limit to Specific Stage

```bash
strata deploy run -f deploy/deploy-prd.yaml --stage infrastructure --force --output json
strata deploy run -f deploy/deploy-prd.yaml --stage configuration --force --output json
```

### Generate SBOM

```bash
strata build sbom -f deploy/deploy-prd.yaml --output json  # CycloneDX JSON
strata build sbom -f deploy/deploy-prd.yaml --report inventory  # human-readable inventory
```

## Common Error Patterns

| Error | Signal | Fix |
|-------|--------|-----|
| "Validation failed (exit 3)" | Read `errors` array in JSON | Fix YAML schema or policy violation |
| "System error (exit 1)" | Read `messages` in JSON | Check tool availability, permissions, network |
| "Missing option '--name'" | Bad arguments (exit 2) | Check CLI syntax |
| "Solution not found" | Workspace not initialized | Run `strata sln init` first |
| "Profile not active" | Deep validation failed | Run `strata profile activate <name>` |

## Agent Responsibilities

- **Always check exit code first** — determines next action
- **Parse JSON output** — never scrape console text
- **Validate before deploy** — prevent surprises
- **Use `--dry-run`** — test changes before committing
- **Use `--force` for automation** — eliminate prompts
- **Use `--output json`** — machine-readable, consistent
- **Set `STRATA_WORK_PATH`** — avoid ambiguity
