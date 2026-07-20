# CLI Parameter Consistency Standard for all 80+ strata Subcommands

- Status: completed
- Date: 2026-07-11
- Completed: 2026-07-20
- Squad Review: danny (DevOps), basher (Automation) — YELLOW/B assessments with critical follow-on work identified

## Context and Problem Statement

strata CLI has grown to 80+ subcommands across 20+ command groups (sln, config, validate, build, deploy, repo, profile, ref, values, etc.). Each command is independently implemented using Click decorators, leading to inconsistent:

- **Parameter naming**: file inputs use `-f`, `--file`, `-p`, `--path`, `--output-file` interchangeably
- **Parameter ordering**: standard flags appear in different positions across commands (some: work-path → output → verbose → quiet; others: output → work-path)
- **Required vs. optional marking**: not consistently documented; operators guess
- **Default values**: inconsistently shown; some commands omit defaults entirely
- **Type annotations**: NAME vs. name vs. RESOURCE_NAME; FILE vs. path vs. filepath
- **Boolean flag patterns**: `--verbose` and `--quiet` not marked as mutually exclusive; some commands have both with undefined behavior
- **Template vs. name arguments**: templates sometimes positional, sometimes flags; names sometimes positional, sometimes flags — no pattern
- **Output format choices**: inconsistent across commands (some support `console, text, json`; others add `ndjson` or `cyclonedx` without documentation)

**Operator Impact**: Discoverability is poor. Operators must read code or trial-error each command to understand parameter requirements. CI/CD automation becomes fragile because no predictable patterns exist. Operators frequently misuse flags.

**Codebase Impact**: New commands copy previous implementations without enforcing consistency. Code review has no objective standard to apply. Help text generation is ad-hoc.

## Considered Options

### Option A: No Change
- Keep current ad-hoc implementations
- Consistency emerges organically as engineers gain experience
- **Rejected:** 80+ commands already shipped; organic convergence is too slow; backwards compatibility prevents retroactive fixes

### Option B: Style Guide Only
- Document preferred patterns (parameter ordering, type annotations, etc.) in contributing guide
- Leave enforcement to code review
- **Rejected:** No automated enforcement; code reviews are subjective; existing 80+ commands don't conform

### Option C: Comprehensive Standard + Migration Plan (CHOSEN)
- Establish a single definitive standard for all CLI parameters across all commands
- Document standard in this ADR and in Click decorator patterns
- Apply the standard to all new commands at code review time
- Incrementally migrate existing commands to the standard
- Add validation to code review checklist

## Decision Outcome

Chosen: **Option C — Comprehensive Standard with Migration Plan**, with the following specifications:

### Parameter Ordering (Fixed Sequence)
All commands MUST use this order:
1. **Required positional arguments** (if any): e.g., `NAME`, `FILE`, `PATH`
2. **Input/Output flags**: e.g., `-f, --file FILE`, `-o, --output-file FILE`
3. **Domain-specific flags**: e.g., `--stage NAME`, `--profile NAME`, `--scope LABEL`
4. **Action modifiers**: e.g., `--dry-run`, `--force`, `--audit`
5. **Standard flags** (always last, in this fixed order):
   - `--work-path PATH` — workspace root
   - `-o, --output FORMAT` — output format
   - `--verbose` — verbose output
   - `--quiet` — suppress non-error output

### Type Annotations (Standardized Set)
- `FILE` — file path (for `-f, --file`)
- `PATH` — directory path
- `PATTERN` — glob pattern
- `NAME` — identifier/name (resources, stages, profiles, keys)
- `KEY` — configuration key
- `VALUE` — configuration value
- `INT` — integer number
- `FORMAT` — output format (console, text, json, ndjson, cyclonedx, sarif, inventory, vex)
- `LEVEL` — log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `SEVERITY` — severity level (CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN)
- `SHELL` — shell type (bash, zsh, fish, powershell)
- `KIND` — configuration kind (deployment, environment, namespace, etc.)
- `ID` — execution/transaction/lock ID (UUID or opaque identifier)
- `TIMESTAMP` — ISO 8601 format with explicit timezone (see ADR-0045 for full standard; e.g., `2026-07-20T14:30:00+00:00`)

### Template vs. Name Conventions
- **Templates**: always `--template TEMPLATE` flag (never positional argument)
  - Rationale: templates are optional selections; flags make intent explicit
  - Example: `strata new NAME --template namespace`
- **Resource Names**: positional command arguments where possible (not flags)
  - Rationale: names are the primary object of the command
  - Example: `strata sln init WORKSPACE_NAME`
  - Exception: when names are optional filters, use `--name NAME` flag
  - Example: `strata sln deployment list [--name NAME]`

### Required vs. Optional Marking
- Mark all required flags/arguments with `(required)` suffix
- Leave optional flags/arguments blank (implicit optional)
- Example:
  ```
  - `FILE` (required) — path to deployment YAML file
  - `--stage NAME` (optional) — limit to specific stage
  ```

### Default Values
- Always document defaults as `(default: VALUE)` at end of parameter description
- For choices, show as `(choices: val1, val2; default: val1)`
- Never omit defaults
- Example:
  ```
  - `--output FORMAT` — Output format (choices: console, text, json; default: console)
  ```

### Mutual Exclusivity
- Document conflicts between flags with `(cannot use with: --other-flag)` notation
- Examples:
  - `-f, --file FILE` (cannot use with: `--scan`) — file-based vs. directory-based SBOM
  - `--verbose` (cannot use with: `--quiet`) — output verbosity is mutually exclusive

### Environment Variables
- Reference external to each command in a centralized "Standard Environment Variables" table
- Each command inherits the table; individual commands do NOT repeat environment variable documentation inline
- Canonical table: in CLI reference and in code comments

### File Input Naming
- **Always** use `-f, --file FILE` for file inputs (no variants)
- **Always** use `-o, --output-file FILE` for file outputs (no variants)
- **Always** use `-p, --pattern PATTERN` for glob patterns (no variants)
- Rationale: operators learn once, apply everywhere; tools can standardize argument parsing

### Boolean Flags
- Use `--flag` (no value) for enable/disable switches
- Examples: `--dry-run`, `--verbose`, `--force`, `--audit`
- Never use `--flag=true|false` or `--[no-]flag` (Click supports this but adds cognitive load)

### Output Format Consistency
- **All commands** must document which output formats they support
- Minimum supported formats: `console` (human), `text` (plain text), `json` (structured)
- Additional formats only if they add value: `ndjson` (streaming), `cyclonedx` (SBOM), `sarif` (scanning), `vex` (VEX attestation), `inventory` (human-readable inventory)
- Default: `console`
- **Critical:** Every command MUST list its supported formats; no guessing

### Exit Codes
- All commands MUST return one of five codes (extends ADR-0004):
  - `0` — success
  - `1` — system failure (infrastructure unavailable, permissions error, timeout, service down)
  - `2` — usage error (bad CLI arguments, file not found, --stage not found)
  - `3` — validation failure (YAML schema error, config conflict, cross-ref broken)
  - `4` — lock conflict (another deployment is in progress; safe to retry after delay)
- **Critical:** Exit codes MUST be documented in every command's help text and in the central reference

### Reference Implementation
- File: `docs/platform/cli-commands-reference.md` (generated from this ADR)
- Shows all 80+ commands in corrected format
- Serves as the single source of truth for operator documentation
- Generated from Click introspection + manual review

### Migration Strategy
**CRITICAL TIMING:** strata is pre-1.0. This is the moment to make breaking CLI changes without ongoing backwards-compatibility burden. Post-1.0, operator workflows are locked in; changing parameter patterns becomes a major breaking change. Apply this standard **now**, and lock it in at 1.0 release.

- Existing commands CAN be refactored immediately (breaking changes are acceptable pre-1.0)
- Each new command MUST conform to this standard at code review time
- Priority for refactoring: high-frequency commands first (sln, deploy, build, validate)
- At 1.0 release, CLI becomes stable; breaking changes become rare

## Consequences

### Positive Consequences

- **Good:** Operators learn standard once, apply everywhere — `--work-path`, `-f, --file`, `--dry-run` work the same in every command
- **Good:** Discoverability improves — new operators can predict flags they need without trial-error
- **Good:** CI/CD automation becomes robust — scripts can rely on predictable parameter patterns, error codes, and defaults
- **Good:** Code review gains objective standard — "does this follow the parameter ordering and type annotation standard?" is a clear yes/no question
- **Good:** Help text generation can be automated or standardized — no more ad-hoc help strings
- **Good:** New commands faster to implement — copy the standard template, fill in domain-specific flags
- **Good:** Reference documentation is single source of truth — one file to maintain, one place for operators to check

### Negative Consequences

- ~~**Bad:** Backwards compatibility burden~~ — **MITIGATED:** strata is pre-1.0; this is the moment to make breaking CLI changes without ongoing support burden. Post-1.0, this window closes. Apply the standard now, break the inconsistent commands now, and lock in the standard at 1.0 release.
- **Bad:** Refactoring effort — retrofitting 80+ commands takes time; prioritization decisions required (mitigated by applying standard to all new commands immediately)
- **Bad:** Templates as flags (vs. positional args) less discoverable — `--template` is less obvious than a positional argument, but consistency wins
- **Bad:** Some domains may need special handling — if an exception emerges, this ADR must be updated and the exception explicitly documented

### Critical Issues Requiring Follow-On Work (from danny & basher Review)

**BLOCKING for production CI/CD — MUST FIX in implementation:**

1. ✅ **`strata deploy destroy` Mutual Exclusion Notation** — **done**: `if force and dry_run: raise click.UsageError(...)` and `if not force and not dry_run: raise click.UsageError(...)` enforced in `cli_deploy.py`; epilog documents "exactly one of --dry-run or --force must be provided"

2. ✅ **Profile Defaults Not Documented** — **done**: all `get_active_profile()` call sites guard `if profile is None:` with the canonical message `"No active profile. Run 'strata profile activate <name>' first."` (or `"...first, or pass --profile NAME."` for commands with `--profile`); ref commands now use separate error guards for "no solution" vs "no active profile"; latent crash in `_load_configuration_service_for_overlap` fixed

3. ✅ **Output Format Choices Inconsistent** — **done**: `click_output_format` decorator uses `type=click.Choice(OUTPUT_FORMATS)` where `OUTPUT_FORMATS = ["console", "text", "json", "ndjson"]`; applied via `@click_output_format` to all commands; `ndjson` is genuinely supported via `emit_ndjson()` in `base_command._finalize`; domain-specific formats (`cyclonedx`, `inventory`, `sarif`, `vex`) are correctly on separate `--report` / `--audit-report` options, not `--output`

4. **Exit Codes Undocumented in CLI Help** — **partial**: 3 commands have epilog (`validate run`, `deploy run`, `deploy destroy`); ~92 remaining commands need epilog added → **targeted for v1.2.2**
   - Bonus fix landed: `click.UsageError` raised inside `_before_execute`/`_execute` was being caught by `base_command.execute()`'s generic `except Exception` handlers, logging a traceback and returning exit code 1 instead of 2; fixed by adding `except click.UsageError: raise` before each `except Exception` in all four phase handlers

5. **Idempotency & Retry Safety Not Declared** — **not done**: documentation-only task; add idempotency table to `docs/platform/commands.md`

**HIGH priority for production automation:**

- ~~Add `--timeout SECONDS` to long-running commands~~ → **delegated to ADR-0027** (command timeout for long-running operations)
- ~~Add `--stream` flag to long-running commands~~ → **delegated to ADR-0029** (realtime progress streaming / ndjson)
- ~~Document signal handling (`SIGTERM` = graceful shutdown + release lock)~~ → **delegated to ADR-0028** (SIGTERM graceful shutdown and lock release)
- ✅ ~~Mark `--verbose` ↔ `--quiet` as mutually exclusive~~ — **done**: `validate_verbose_quiet_exclusive` callback enforced via `click_output_verbose` / `click_output_quiet` decorators in `cli_common.py`
- ~~Specify exact timestamp format~~ → **delegated to ADR-0045** (date/time format and handling standard)

## More Information

- **Squad Review Summary:** danny (DevOps, YELLOW verdict) identified 5 critical contradictions/gaps; basher (Automation, B grade) identified 5 must-fix CI/CD gaps and 3 pain points
- **Click Documentation:** https://click.palletsprojects.com/parameters/
- **Related ADRs:** ADR-0002 (Python Click CLI choice), ADR-0004 (Exit Code Convention)
- **Implementation Checklist:** TODO — create issue with per-command refactoring tasks

## Implementation Roadmap

1. **Phase 1 (Now):** Accept this ADR; establish this as the binding standard for all CLI commands
2. **Phase 2 (Next Sprint):** Address 5 blocking issues + create Click decorator template in code
3. **Phase 3 (Next 2-3 Sprints):** Refactor high-frequency commands (sln, deploy, build, validate); test with operators
4. **Phase 4 (Ongoing):** Refactor remaining commands as they're touched during feature work; update reference doc
5. **Phase 5 (Stability):** Automated tests for parameter ordering and exit codes; reference doc becomes generated from Click introspection

## Standard Conventions (Applied to All Commands)

### Parameter Ordering:
1. **Input/Required flags** first: `-f, --file FILE`
2. **Domain-specific flags** second: `--stage NAME`, `--scope LABEL`, etc.
3. **Action modifiers** third: `--dry-run`, `--force`, `--audit`, etc.
4. **Standard flags** always last (in fixed order):
   - `--work-path PATH` (workspace root)
   - `-o, --output FORMAT` (output format)
   - `--verbose` (verbose output)
   - `--quiet` (suppress non-error output)

### Type Annotations (Standardized):
- `FILE` — file path (for -f, --file)
- `PATH` — directory path
- `PATTERN` — glob pattern
- `NAME` — identifier/name
- `KEY` — configuration key
- `VALUE` — configuration value
- `INT` — integer number
- `SECONDS` — duration in seconds (integer); conveys unit explicitly — used for `--timeout`
- `FORMAT` — output format (console, text, json, ndjson)
- `REPORT` — report output mode (cyclonedx, inventory) — used for `--report` on `build sbom`
- `LEVEL` — log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `SEVERITY` — severity level (CRITICAL, HIGH, MEDIUM, LOW)
- `SHELL` — shell type (bash, zsh, fish, powershell)
- `KIND` — configuration kind (deployment, environment, namespace, etc.)

### Required Marking:
- Use `(required)` for required flags
- Leave blank (optional) for optional flags

### Default Values:
- Always shown as `(default: VALUE)` at end of description
- For choices, show as `(choices: val1, val2; default: val1)`

### Environment Variables:
- Reference external to each command; see [Standard Environment Variables](#standard-environment-variables-reference)

---

## Top-Level Commands (Corrected Format)

### `strata help`
Show help topics and workflow guidance.
- `--topic NAME` — Display content for a specific help topic
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

### `strata version`
Show CLI version.
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output

### `strata console`
Interactive workspace session with guided onboarding.
- `--work-path PATH` — Workspace root (default: current directory)
- `--no-color` — Disable color output

### `strata guide`
Show setup progress and suggest the next action for this workspace.
- `-f, --file FILE` (optional) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

### `strata completion`
Generate shell completion scripts.
- `SHELL` (required) — Shell type (choices: bash, zsh, fish, powershell)

### `strata new`
Create a new platform configuration file from a template.
- `NAME` (optional) — Name for the resource
- `--template TEMPLATE` (optional) — Template name (e.g., namespace, provider, workspace)
- `--output-file FILE` (optional) — Output file path or directory (no `-o` shorthand — `-o` is reserved for `--output FORMAT`)
- `--overwrite` — Overwrite if the output file already exists
- `--set KEY=VALUE` (optional, repeatable) — Override a template variable
- `--list` — List available templates and exit
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

## Command Groups (Corrected Format)

### `strata sln` — Solution Workspace Lifecycle

#### `strata sln init` (required)
Initialize a new Strata solution workspace.
- `NAME` (required) — Name of the solution workspace
- `--template TEMPLATE` (optional) — Scaffold template to apply (built-in name or local path)
- `--list` — List available scaffold templates and exit
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln update`
Update workspace configuration and integrations.
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln clean`
Clean solution artifacts (logs, temp files).
- `--dry-run` — Report what would be deleted without making changes
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln status`
Show workspace health: solution, profile, repositories, and integrations.
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln export`
Export solution configuration as a template.
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln deployment add`
Register a deployment YAML file in the solution.
- `FILE` (required) — Path to deployment file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln deployment remove`
Remove a registered deployment from the solution.
- `NAME` (required) — Deployment name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln deployment list`
List deployment files registered in the solution.
- `--name NAME` (optional) — Filter to a single deployment by name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata sln deployment scan`
Scan a directory tree and register discovered deployment files.
- `PATH` (required) — Directory to scan
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata config` — Workspace CLI Preferences

#### `strata config set`
Set a workspace default (e.g., `strata config set output json`).
- `KEY` (required) — Configuration key
- `VALUE` (required) — Configuration value
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata config unset`
Remove a workspace default (e.g., `strata config unset output`).
- `KEY` (required) — Configuration key
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata config list`
Show current workspace defaults.
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata config log list`
Show the current logging configuration (logging.yaml).
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata config log set`
Set logging configuration.
- `KEY` (required) — Configuration key
- `VALUE` (required) — Configuration value
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata log` — Execution Logs

#### `strata log list`
List execution log entries for the current workspace.
- `--lines INT` (optional) — Maximum number of entries to display (default: 50)
- `--minutes INT` (optional) — Show only entries from the last N minutes
- `--level LEVEL` (optional) — Filter by minimum log level (choices: DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `--execution-id ID` (optional) — Filter to a specific execution ID
- `--last` — Show logs for the most recent command execution
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata validate` — YAML Validation

#### `strata validate run`
Validate a platform YAML file against its kind-specific schema.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `-p, --pattern PATTERN` (optional) — Glob pattern for multiple manifests (cannot use with: -f)
- `--deep` — Enable Phase 2 (cross-reference) validation against profile sources
- `--explain` — Emit plain-English summary of what the file describes
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

**Exit codes (Phase 5a — epilog added):** 0 (success), 1 (system error), 2 (usage error), 3 (validation error). Note: exit code 4 is only returned by `deploy run` and `deploy destroy`.

---

### `strata build` — Build Platform and Terraform Artifacts

#### `strata build run`
Run platform + terraform build pipeline.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Limit to specific deployment stage
- `--dry-run` — Validate and plan build without writing output files
- `--audit` — Run CVE vulnerability scan after SBOM generation (requires trivy or grype)
- `--severity SEVERITY` (optional) — Minimum severity to report (choices: CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN; default: MEDIUM)
- `--fail-on SEVERITY` (optional) — Exit non-zero if findings at this severity or above exist (choices: CRITICAL, HIGH, MEDIUM, LOW)
- `--audit-report FORMATS` (optional) — Write audit reports (choices: vex, sarif; repeatable, e.g., vex,sarif)
- `--timeout SECONDS` (optional) — Abort if command does not complete within N seconds (default: 3600)
- `--stream` — Stream ndjson progress events to stdout during execution **[TODO: not yet implemented]**
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata build clean`
Clean deployment build artifacts.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--dry-run` — Show which path would be cleaned without deleting files
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata build plan`
Show artifact diff + terraform plan without writing to real build path.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Limit terraform plan to specific deployment stage
- `--artifacts-only` — Show only artifact diff; skip terraform plan
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata build sbom`
(Re)generate SBOM from existing platform.json or scan a directory.
- `-f, --file FILE` (optional) — Path to deployment YAML file (cannot use with: --scan)
- `--scan PATH` (optional) — Scan directory for SBOM components (cannot use with: -f)
- `--report REPORT` (optional) — Output mode (choices: cyclonedx, inventory; default: cyclonedx)
- `--output-file FILE` (optional) — Write output to FILE instead of default location
- `--no-deps` — Skip lockfile scanning (faster for large repos)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata deploy` — Deploy Platform Using Provisioners

#### `strata deploy run`
Run the deploy pipeline for a deployment definition.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Limit execution to specific deployment stage
- `--scope LABEL` (optional) — Run only stages matching this scope label
- `--dry-run` — Validate and plan deploy without running provisioners
- `--force` — Skip confirmation prompts and approval gates (cannot use with: --dry-run)
- `--force-lock` — Force-release held lock before acquiring (recover from crash)
- `--timeout SECONDS` (optional) — Abort if command does not complete within N seconds (default: 3600)
- `--stream` — Stream ndjson progress events to stdout during execution **[TODO: not yet implemented]**
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

**Exit codes (Phase 5a — epilog added):** 0 (success), 1 (system error), 2 (usage error), 3 (validation error), 4 (lock conflict — another deployment in progress; safe to retry).

#### `strata deploy destroy`
Tear down provisioned infrastructure for a deployment definition.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Limit destruction to specific deployment stage
- `--scope LABEL` (optional) — Destroy only stages matching this scope label
- `--dry-run` — Plan destruction (terraform plan -destroy) without removing anything (cannot use with: --force)
- `--force` (required) — Auto-approve terraform destroy non-interactively (cannot use with: --dry-run)
  > One of `--dry-run` or `--force` must be provided; neither is not accepted. (Phase 3 — mutual exclusion enforced)
- `--force-lock` — Force-release held lock before acquiring (recover from crash)
- `--timeout SECONDS` (optional) — Abort if command does not complete within N seconds (default: 3600)
- `--stream` — Stream ndjson progress events to stdout during execution **[TODO: not yet implemented]**
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

**Exit codes (Phase 5a — epilog added):** 0 (success), 1 (system error), 2 (usage error), 3 (validation error), 4 (lock conflict — another deployment in progress; safe to retry).

#### `strata deploy show`
Show resolved deployment configuration: remote versions, workspace, and environment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy plan`
Show the resource change summary from the last saved .tfplan file.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Limit display to specific deployment stage
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy list`
List deployment manifests with metadata for CI matrix generation.
- `--path PATH` (optional) — Directory to scan for manifests (default: current directory)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy history`
Show deployment execution history from workspace logs.
- `--lines INT` (optional) — Maximum entries to display (default: 50)
- `--operation OP` (optional) — Filter to specific operation type (choices: run, destroy)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy status`
Show the live infrastructure status for a deployment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy health`
Show health metrics and recent status for all infrastructure in a deployment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy drift`
Show infrastructure drift for a deployment (actual vs. desired state).
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy drift-history`
Show the history of drift detection results.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy acknowledge-drift`
Acknowledge infrastructure drift and reset drift history.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy output`
Show live Terraform outputs for a deployment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--name NAME` (optional) — Print single output value only
- `--provisioner NAME` (optional) — Limit to stages using specific provisioner (default: all)
- `--raw` — Print bare value with no formatting (requires: --name)
- `--json` — Emit raw Terraform JSON output directly, bypassing the strata command envelope (exception to the envelope standard; use when piping output to tools that consume native `terraform output -json` format)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy lock status`
Show the current deployment lock status.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy lock release`
Force-release a deployment lock.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata deploy lock history`
Show deployment lock history.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata repo` — Manage Repositories

#### `strata repo add`
Register a repository in the current solution.
- `NAME` (required) — Repository name
- `URL` (required) — Repository URL
- `--branch NAME` (optional) — Default branch to track (default: main)
- `--local-path PATH` (optional) — Local path relative to work-path (default: repos/<name>)
- `--clone` — Clone repository immediately after registering
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata repo list`
List repositories registered in the current solution.
- `--name NAME` (optional) — Filter to single repository (default: show all)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata repo remove`
Remove a repository from the current solution.
- `NAME` (required) — Repository name
- `--purge` — Also delete the local clone directory from disk
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata repo sync`
Clone or pull repositories registered in the solution.
- `--name NAME` (optional) — Sync only this repository (default: sync all)
- `--force` — Hard-reset dirty working trees instead of skipping them
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata repo status`
Show the status of registered repositories.
- `--name NAME` (optional) — Show only this repository (default: show all)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata profile` — Manage Profiles

#### `strata profile add`
Add a new profile to the current solution.
- `NAME` (required) — Profile name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata profile remove`
Remove a profile from the current solution.
- `NAME` (required) — Profile name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata profile list`
List profiles registered in the current solution.
- `--name NAME` (optional) — Filter to single profile (default: show all)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata profile activate`
Activate a profile in the current solution.
- `NAME` (required) — Profile name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata profile show`
Show all registered ref paths for a profile, grouped by type.
- `NAME` (required) — Profile name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata ref` — Manage File References (env, config, data, secret)

#### `strata ref env add`
Register an environment file path entry in a profile.
- `NAME` (required) — Reference name
- `FILE` (required) — File path
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref env remove`
Remove an environment file reference from a profile.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref env list`
List environment file references in a profile.
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref env show`
Show details of an environment file reference.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref config add`
Register a configuration file path entry in a profile.
- `NAME` (required) — Reference name
- `FILE` (required) — File path
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref config remove`
Remove a configuration file reference from a profile.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref config list`
List configuration file references in a profile.
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref config show`
Show details of a configuration file reference.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref data add`
Register a data file path entry in a profile.
- `NAME` (required) — Reference name
- `FILE` (required) — File path
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref data remove`
Remove a data file reference from a profile.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref data list`
List data file references in a profile.
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref data show`
Show details of a data file reference.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref secret add`
Register a secret file reference in a profile.
- `NAME` (required) — Reference name
- `FILE` (required) — File path
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref secret remove`
Remove a secret file reference from a profile.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref secret list`
List secret file references in a profile.
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata ref secret show`
Show details of a secret file reference.
- `NAME` (required) — Reference name
- `--profile NAME` (optional) — Profile name (default: active profile)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata values` — Inspect and Manage Deployment Values

#### `strata values list`
List all variables, secrets, and feature flags for a deployment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--stage NAME` (optional) — Use environment from specific stage (default: first stage)
- `--type TYPE` (optional) — Show only this type (choices: variables, secrets, features)
- `--show-store` — Include store reference (env var name, key path) in output
- `--unresolved` — Show only entries that failed to resolve
- `--trace` — Show which environment file each value originates from (provenance)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata values get`
Retrieve the full resolved value for one or more keys.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `KEY` (required, repeatable) — One or more key names
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata values set`
Set or update a deployment value.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `KEY` (required) — Value key
- `VALUE` (required) — Value to set
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata values resolve`
Resolve and show all values for a deployment.
- `-f, --file FILE` (required) — Path to deployment YAML file
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

### `strata schema` — Inspect JSON Schemas

#### `strata schema list`
List all supported platform document kinds.
- `--output FORMAT` — Output format (choices: console, text, json; default: console)

#### `strata schema get`
Emit the JSON Schema for a platform document kind.
- `KIND` (required) — Platform document kind (e.g., deployment, environment)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)

---

### `strata audit` — Deployment Audit Trail

#### `strata audit changes`
List recent deployment executions from the deploy-log.
- `--last INT` (optional) — Maximum entries to show (default: 10)
- `--since TIMESTAMP` (optional) — Show entries since ISO 8601 timestamp
- `--stage NAME` (optional) — Filter to entries executing specific stage name
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json, ndjson; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata audit resend`
Re-forward deploy-log entries to configured audit sinks.
- `--last INT` (optional) — Resend only the last N entries
- `--since TIMESTAMP` (optional) — Resend entries since ISO 8601 timestamp
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

#### `strata audit export`
Export deploy-log entries to a file.
- `--last INT` (optional) — Export only last N entries
- `--since TIMESTAMP` (optional) — Export entries since ISO 8601 timestamp
- `--format FORMAT` (optional) — Export format (choices: json, ndjson; default: json)
- `--work-path PATH` — Workspace root (default: current directory)
- `--output FORMAT` — Output format (choices: console, text, json; default: console)
- `--verbose` — Enable verbose output
- `--quiet` — Suppress non-error output

---

## Standard Environment Variables Reference

These environment variables are supported by all commands:

| Env Var            | Type     | Default           | Purpose                                             |
| ------------------ | -------- | ----------------- | --------------------------------------------------- |
| `STRATA_FILE`      | `FILE`   | —                 | Path to deployment YAML file (same as `-f, --file`) |
| `STRATA_WORK_PATH` | `PATH`   | current directory | Workspace root (same as `--work-path`)              |
| `STRATA_OUTPUT`    | `FORMAT` | console           | Output format (same as `-o, --output`)              |
| `STRATA_VERBOSE`   | boolean  | false             | Enable verbose output (same as `--verbose`)         |
| `STRATA_QUIET`     | boolean  | false             | Suppress non-error output (same as `--quiet`)       |

---

## Key Changes From Current Version

### What Changed:
1. ✅ **File paths standardized:** `-f, --file` for inputs, `--output-file` for outputs (no `-o` shorthand — `-o` is reserved for `--output`), `-p, --pattern` for globs
2. ✅ **Standard flags consistent order:** Always last in fixed order: work-path, output, verbose, quiet
3. ✅ **Required parameters marked:** All required flags/args clearly marked `(required)`
4. ✅ **Type annotations standardized:** Using consistent NAME, FILE, PATH, PATTERN, KEY, VALUE, INT, FORMAT, LEVEL, etc.
5. ✅ **Defaults shown consistently:** All defaults shown as `(default: VALUE)` at end
6. ✅ **Choices documented:** Enum options shown as `(choices: val1, val2, val3; default: val1)`
7. ✅ **Mutual exclusivity documented:** Conflicting flags marked as `(cannot use with: --other-flag)`
8. ✅ **Environment variables documented:** Separate reference table (cleaner than inline)

### What Stayed the Same:
- Command structure and functionality (no behavior changes)
- All subcommands and their purposes
- Parameter semantics and behavior
- Positional argument handling

## Implementation Plan

### Phase 1 — Parameter Renames & Documentation (no logic changes)

Purely mechanical: rename flags, add markers, add annotations. No behaviour changes. Safe to do in one PR per command group.

- [ ] Rename any `-f` / `--file` / `--path` / `--output-file` variants to canonical forms (`-f, --file FILE`, `--output-file FILE`, `-p, --pattern PATTERN`)
- [ ] Add `(required)` suffix to every required flag and positional argument
- [ ] Add `(default: VALUE)` to every optional flag that has a default
- [ ] Add `(choices: val1, val2; default: val1)` to every enum parameter
- [ ] Add `(cannot use with: --other-flag)` to every flag that has a documented conflict
- [ ] Standardize all type annotations (`NAME`, `FILE`, `PATH`, `PATTERN`, `KEY`, `VALUE`, `INT`, `FORMAT`, `LEVEL`, `SEVERITY`, `SHELL`, `KIND`, `ID`, `TIMESTAMP`)
- [ ] Normalize parameter ordering in every command to the fixed sequence: input/output flags → domain-specific → action modifiers → standard flags (work-path, output, verbose, quiet)

Priority order within this phase: `deploy`, `build`, `sln`, `validate` (highest operator traffic first).

---

### Phase 2 — `--output` Choice Enforcement

Add `type=click.Choice([...])` to every command's `--output` decorator. Click then rejects invalid values at parse time (exit 2) and lists valid choices in `--help` automatically — no runtime format-checking code needed.

Each command gets only the formats it actually supports:
- Base set (`console, text, json`): all commands
- Add `ndjson` only to streaming-capable commands (e.g., `audit changes`)
- Add `cyclonedx` only to `build sbom`
- Add `sarif` / `vex` only to audit-emitting commands
- Add `inventory` only to `build sbom --report`

**Scope:** Every Click `@click.option('--output', ...)` decorator across all command files.

---

### Phase 3 — `deploy destroy` Mutual Exclusion Gate

Enforce that exactly one of `--dry-run` or `--force` is provided; both or neither is exit 2.

- Add a Click callback (or `cls=MutuallyExclusiveOption`) to the `deploy destroy` command that:
  - Raises `click.UsageError` if both flags are set
  - Raises `click.UsageError` if neither flag is set
- Same pattern for `--force` / `--dry-run` on `deploy run` (both already present; currently no rejection if both are passed)

**Files:** `src/strata/cli/deploy/destroy.py`, `src/strata/cli/deploy/run.py` (or equivalent paths)

---

### Phase 4 — Profile Resolution Error Handling

Replace any silent failure or cryptic internal error with an actionable `click.UsageError` (exit 2) when `--profile` is omitted and no active profile exists.

- Locate the shared profile-resolution helper (`resolve_profile()` or equivalent)
- When `--profile` is not passed and no active profile is recorded, raise:
  ```python
  raise click.UsageError(
      "No active profile. Run 'strata profile activate NAME' first, or pass --profile NAME."
  )
  ```
- Add a test: call any `--profile`-accepting command with no active profile and no `--profile` flag; assert exit 2 and the exact message

**Scope:** One shared helper; all `--profile NAME (default: active profile)` commands inherit the fix.

---

### Phase 5 — Exit Code Epilogs + `LockConflictError`

> ~~**Prerequisite:** ADR-0004 must be updated to add exit code 4 before this phase is implemented. Phase 5b is blocked until `docs/decisions/0004-exit-code-convention.md` reflects exit codes 0–4.~~ ✅ **RESOLVED 2026-07-17** — ADR-0004 updated; `LockConflictError` implementation complete with 10/10 tests passing. Phase 5b is unblocked.

Two sub-tasks in one phase:

**5a — Epilog on every command:**
Add Click `epilog=` to every `@click.command()` decorator containing the exit code table:
```
Exit codes:
  0  success
  1  system error (infrastructure unavailable, timeout, permissions) — alert
  2  usage error (bad arguments, file not found) — fix script
  3  validation error (schema, cross-ref) — fix config
  4  lock conflict (another deployment in progress) — retry after delay
```
Note in the epilog for all non-deploy commands: "Exit code 4 is only returned by `deploy run` and `deploy destroy`."

**5b — `LockConflictError` → `sys.exit(4)`:**
- Define a custom exception `LockConflictError` (or reuse existing lock error type if one exists)
- In the top-level Click error handler, catch `LockConflictError` and call `sys.exit(4)`
- Ensure the lock-acquisition path raises `LockConflictError` when a lock is already held (not a generic exception)
- Update ADR-0004 to add exit code 4

---

### Phase 6 — `--timeout` Implementation

`--timeout SECONDS` is already documented in the command specs above (Phase 1 adds the decorator; this phase wires in the logic). Implementation decisions — timeout mechanism, cross-platform strategy, interaction with lock release — are scoped in a dedicated ADR.

**→ See ADR-0027** _(to be written)_

---

### Phase 7 — Signal Handling (`SIGTERM` Graceful Shutdown)

`SIGTERM` handling requires decisions about shutdown sequencing, lock-release ordering, subprocess management, and Windows compatibility. Scoped in a dedicated ADR.

**→ See ADR-0028** _(to be written)_

---

### Phase 8 — `--stream` ndjson Progress Events

`--stream` is marked `[TODO: not yet implemented]` in the command specs above. The event schema, buffering strategy, and back-pressure behaviour warrant a dedicated ADR before implementation.

**→ See ADR-0029** _(to be written)_

---

### Phase 9 — Idempotency Declarations & Re-Run Warnings

Document and enforce idempotency behaviour for all commands.

**Documentation (part of Phase 1 or standalone):**
Add an idempotency table to the CLI reference. Categories:

| Category                                                                                    | Commands                                                                                                                              |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Fully idempotent — always safe to re-run                                                    | `validate`, `build plan`, `build sbom`, all `list` / `status` / `show` / `history` / `schema` / `values list` / `audit` read commands |
| Idempotent with caveats — re-run reruns Terraform; may change infra if state drifted        | `build run`, `deploy run`                                                                                                             |
| Requires manual intervention — cannot re-run after partial failure without inspecting state | `deploy destroy`                                                                                                                      |

**Implementation:**
- For `deploy destroy`: if a previous `deploy destroy` execution for the same deployment is detected in the deploy-log with a non-zero exit code, emit a warning before proceeding:
  ```
  Warning: last destroy execution for 'mydeployment' exited non-zero. Inspect state before retrying.
  ```
- No behaviour block — just a warning; operator decides whether to proceed.

---

## Open Issues (Review 2026-07-17)

### 🔴 Must Fix

#### Issue 1 — `--path` vs `--pattern` contradiction
- **Status:** ✅ **RESOLVED 2026-07-17** — Renamed `--path` → `--pattern` in `cli_validate.py` and updated all related documentation (guides, workflow examples, ADRs)
- **Previous issue:** The Standard Conventions section documented `-p, --pattern PATTERN` but the implementation used `--path` with `-p` shorthand
- **Resolution:** Implementation now conforms to standard; senior DevOps engineers expect `-p` to be a glob pattern (grep/ripgrep convention)

#### Issue 2 — `-o` shorthand collision
- **Status:** ✅ **RESOLVED 2026-07-17** — `--output-file` implemented without shorthand to reserve `-o` for `--output FORMAT`
- **Previous issue:** Standard Conventions defined `-o, --output-file FILE` which collides with `-o` for `--output FORMAT`
- **Resolution:** `strata new` uses `--output-file FILE` (no shorthand); `-o` is reserved exclusively for `--output FORMAT`; documentation updated

#### ~~Issue 3 — ADR-0004 not updated for exit code 4~~ ✅ RESOLVED 2026-07-17
- ADR-0004 updated: title, considered options, decision outcome table, consequences, and More Information section now reflect exit codes 0–4
- Cross-reference added in both ADRs

### 🟡 Should Fix

#### Issue 4 — `FORMAT` metavar overloaded for `--report`
- **Where:** Type Annotations table defines `FORMAT` as output format (`console, text, json`). But `strata build sbom` uses `--report FORMAT` where `FORMAT` means `cyclonedx, inventory` — a different domain
- **Fix:** Add `REPORT` to the Type Annotations table for report-mode options. Rename `--report FORMAT` to `--report REPORT` in the `build sbom` spec
- **Scope:** Type Annotations table, `strata build sbom` spec

#### Issue 5 — Implementation checklist not created
- **Where:** Implementation Plan section ends with `TODO — create issue with per-command refactoring tasks`
- **Impact:** No trackable work items exist for the per-command refactoring across 80+ commands. Progress cannot be measured
- **Fix:** Create a tracking issue (GitHub issue or ADR appendix checklist) with one entry per command group. Mark status as the phases are completed

#### Issue 6 — Missing command groups in reference table
- **Where:** The following command groups are not documented in the Standard Conventions (Corrected Format) section: `strata audit`, `strata tools`, `strata diff`, `strata vars`, `strata manifest`, `strata mcp`, and the `strata log` group is incomplete
- **Impact:** The standard is incomplete as an operator reference; new contributors cannot verify conformance for these groups
- **Fix:** Add a corrected-format entry for each missing command group in a follow-on PR

### 🟢 Minor

#### Issue 7 — `--timeout SECONDS` metavar
- `SECONDS` is used as metavar but is not defined in the Type Annotations table. Either add `SECONDS` as a type annotation alias for `INT`, or change to `--timeout INT` and rely on the description ("Abort if command does not complete within N seconds") to convey the unit
- Affects: `strata build run`, `strata deploy run`, `strata deploy destroy`

#### Issue 8 — `strata deploy output --json` bypasses envelope
- `--json` on `strata deploy output` "bypasses strata envelope" but `--output json` already exists on every command. These are different semantics (raw Terraform JSON vs. strata-wrapped JSON) but are not explicitly distinguished
- Fix: Add a note clarifying that `--json` on `deploy output` emits raw Terraform JSON output, not the strata command envelope, and is a deliberate exception to the envelope standard

#### Issue 9 — `strata deploy list -d, --dir`
- Uses `-d` and `--dir` rather than following the `PATH` convention for directory arguments. The standard reserves `-p` for patterns and uses `PATH` as the type for directories
- Fix: Rename to `-p, --path PATH` or add an explicit exception note explaining why `-d, --dir` is kept