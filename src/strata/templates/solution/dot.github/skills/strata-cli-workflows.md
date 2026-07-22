---
description: Strata CLI command structure, exit codes, JSON output parsing, and dry-run patterns
applyTo: "**"
confidence: high
---

# Strata CLI Workflows — AI Skill File

## Exit Code Convention

Strata uses a four-code system. Always check the exit code first:

| Code | Meaning                     | Agent Action                                     |
| ---- | --------------------------- | ------------------------------------------------ |
| `0`  | Success                     | Proceed normally                                 |
| `1`  | System/execution failure    | Read `messages` in JSON output for crash reason  |
| `2`  | Usage error (bad arguments) | Fix command syntax                               |
| `3`  | Validation failure          | Read `errors` array in JSON output for specifics |

**Always pass `--output json`** to every command — this gives structured responses that are safe to parse.

---

## JSON Output Envelope

Every command returns this structure when `--output json` is set:

```json
{
  "success": true,
  "data": { ... },
  "errors": [],
  "messages": []
}
```

**Agent parsing rules:**
- Check `success` first — if false, read `messages` and `errors` to understand what failed
- Exit code 3 (validation) means the file was processed but is invalid — inspect `errors` array
- Exit code 1 means system crash — read `messages` for the reason
- `data` contains command-specific output (file list, build results, deployment status, etc.)

---

## Command Groups & Exit Code Patterns

### `strata sln` — Solution Lifecycle

```bash
strata sln init [name]                          # Initialize workspace
strata sln status --output json                 # Single JSON snapshot of workspace state
strata sln clean                                # Clear build artifacts (safe)
strata sln export --output json                 # Export as archive (for CI)
```

**Agent workflow:** Always start with `sln status` to understand workspace readiness. Use `status.readiness.next_step.hint` to guide the user through remaining setup.

### `strata validate` — Schema & Cross-Reference Checks

```bash
strata validate <file> --output json            # Structural schema validation
strata validate <file> --deep --output json     # Deep validation (requires active profile)
strata validate <file> --explain --output json  # Include explanation of what passed/failed
```

**Agent workflow:** Validate BEFORE every build or deploy. Exit 3 = schema error (read `errors`). Exit 0 = safe to proceed.

### `strata build` — Artifact Generation

```bash
strata build run -f <deploy> --output json                    # Full build (generates Terraform artifacts)
strata build run -f <deploy> --dry-run --output json          # Dry-run (validate + plan without writing)
strata build plan -f <deploy> --output json                   # Show what would change
strata build plan -f <deploy> --stage <stage> --output json   # Limit to single stage
strata build clean -f <deploy> --output json                  # Remove artifacts (safe)
```

**Agent workflow:** Always dry-run first (`--dry-run`). If dry-run succeeds (exit 0), production build should succeed.

### `strata deploy` — Infrastructure Provisioning & Configuration

```bash
strata deploy run -f <deploy> --dry-run --force --output json      # Dry-run (plan only)
strata deploy run -f <deploy> --force --output json                # Execute (--force skips prompts)
strata deploy run -f <deploy> --stage <stage> --force --output json # Run single stage only
strata deploy status -f <deploy> --output json                     # Current deployment state
strata deploy history -f <deploy> --output json                    # Past deployments
strata deploy health -f <deploy> --output json                     # Health check
strata deploy destroy -f <deploy> --force --output json            # Tear down (requires --force)
```

**Agent workflow:** 
- Always dry-run FIRST: `strata deploy run -f <file> --dry-run --output json`
- If dry-run succeeds, execute with `--force`
- Check `health` afterward to verify resources are available
- Store deployment IDs from `history` for audit

### `strata repo` — Repository Management

```bash
strata repo add --name <name> --path <path> --output json      # Register a config repo
strata repo remove --name <name> --output json                 # Unregister
strata repo list --output json                                 # Show all registered repos
strata repo sync --name <name> --output json                   # Pull latest from repo
strata repo status --output json                               # Check clone status
```

**Agent workflow:** Repos are registered once. Use `repo list` to verify existing registrations before adding duplicates.

### `strata profile` — Environment Profiles

```bash
strata profile create --name <env> --output json               # Create profile
strata profile activate --name <env> --output json             # Set active profile (required for deep validation)
strata profile list --output json                              # Show all profiles
strata profile show --output json                              # Show active profile details
strata profile remove --name <env> --output json               # Delete profile
```

**Agent workflow:** Profiles are environments (dev, staging, prod). Activate the correct one before building. Deep validation requires an active profile.

### `strata ref` — Profile References (Secrets, Configs, Data)

```bash
strata ref env <key> --output json                 # Get environment variable value
strata ref config <key> --output json              # Get configuration value
strata ref data <key> --output json                # Get data reference
strata ref secret <key> --output json              # Get secret (never print output)
```

**Agent workflow:** Use to verify secret names and configuration keys exist before referencing them in YAML.

### `strata values` — Value Resolution

```bash
strata values list -f <deploy> --output json       # Show all resolved values for deployment
strata values get -f <deploy> <key> --output json  # Get single resolved value
```

**Agent workflow:** Use to debug why a value isn't resolving as expected. Shows the value at deployment time.

### `strata audit` — Execution History

```bash
strata audit list --last --output json                    # Last command only
strata audit list --level ERROR --output json             # Filter by severity
strata audit list --execution-id <id> --output json       # Filter by run
strata audit list --minutes 10 --output json              # Last N minutes
```

**Agent workflow:** Use after a failed deploy to see what actually happened. Audit is append-only — never edited.

### `strata tools` — External Tool Status

```bash
strata tools status --output json                 # Check terraform, ansible, git, docker availability
strata tools check <tool> --output json           # Check specific tool
```

**Agent workflow:** Run before complex operations to ensure required tools are installed.

### `strata guide` — Workspace Readiness Checklist

```bash
strata guide show                                 # Interactive human-readable checklist
```

**Agent workflow:** Not JSON-capable. Use `sln status --output json` for machine-readable equivalent.

### `strata schema` — Schema Reference

```bash
strata schema list --output json                  # List all kinds and their versions
strata schema get <kind> --output json            # Get schema for a specific kind
```

**Agent workflow:** Use to validate your YAML structure against the official schema. Before writing YAML, check the schema.

### `strata new` — Scaffolding

```bash
strata new --list --output json                   # List available templates
strata new <kind> <name> --path <dir> --output json  # Scaffold a new file
```

**Agent workflow:** Use to generate boilerplate YAML. Always validate after scaffolding.

---

## Dry-Run Pattern (Critical!)

**Before ANY destructive operation, always dry-run:**

```bash
# Build dry-run (plan without writing artifacts)
strata build run -f deploy.yaml --dry-run --output json

# Deploy dry-run (plan infrastructure changes without provisioning)
strata deploy run -f deploy.yaml --dry-run --output json
```

Parse the response:
- Exit 0 + `success: true` = safe to proceed
- Exit 0 + `success: false` = operation would fail — read `errors`
- Exit 3 = validation error — read `errors` array

---

## Common Error Patterns

| Error                                                   | Likely Cause                                      | Fix                                                |
| ------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------- |
| `TF400813: Resource not available for anonymous access` | Missing authentication                            | Ensure Azure/AWS/GCP credentials configured        |
| `Validation error: unknown field`                       | Schema mismatch                                   | Run `strata schema get <kind>` to see valid fields |
| `Profile not found`                                     | No active profile                                 | Run `strata profile activate <name>` first         |
| `Repository not found (@repo_name/...)`                 | Remote not registered                             | Run `strata repo add --name <name> --path <path>`  |
| `Dry-run succeeded but deploy failed`                   | Environment difference between dry-run and deploy | Check profiles, secrets, external tool versions    |

---

## Agent Hygiene Rules

1. **Always parse `--output json`** — never scrape console output
2. **Check exit code first** — determines how to interpret the JSON
3. **Never skip dry-run** — always test before destructive ops
4. **Profile must be active for deep validation** — `strata profile activate` before `validate --deep`
5. **Store intermediate results** — use shell variables to avoid repeating commands
6. **Log what you actually ran** — include the exact command in your response
