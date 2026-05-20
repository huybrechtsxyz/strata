# Feature: `strata doctor` — Environment Health Check

## Summary

Implement `strata doctor` as a single command that validates the full local development environment — Python runtime, required tools, authentication, workspace state, and configuration health — providing clear ✅/❌ output with fix hints on every failure.

**Goal:** Eliminate "it doesn't work and I don't know why" support calls. A new team member runs `strata doctor` and immediately knows what's missing and how to fix it.

---

## Motivation

- Onboarding friction: new engineers waste 30+ minutes diagnosing missing tools, wrong Python versions, or expired auth tokens
- `strata tools status` checks tool availability but NOT: Python version, workspace state, auth status, secret store access, or config validity
- Operators need one command to answer: "Is my local environment ready to deploy?"

---

## Proposed Design

### Command Interface

```bash
strata doctor                      # Full check (all categories)
strata doctor --category runtime   # Check only one category
strata doctor --fix                # Attempt auto-fix where possible (future)
strata doctor --output json        # Machine-readable for CI
```

### Architecture

```
commands/
  cli_doctor.py              ← Click wiring (new group or top-level command)
  doctor/
    __init__.py
    doctor_command.py         ← DoctorCommand extends BaseCommand
controllers/
  doctor_controller.py       ← Orchestrates check categories
```

**Layer rules:**
- `DoctorCommand` → `DoctorController` → existing integrations/services
- `INIT_REQUIRED = False` — doctor must work even in a broken/uninitialized workspace
- Each check returns a `DoctorCheckResult` (name, status, message, fix_hint)

### Check Categories

#### 1. Runtime (`runtime`)

| Check | How | Fix hint |
|-------|-----|----------|
| Python version ≥ 3.13 | `sys.version_info` | "Install Python 3.13+: https://python.org/downloads" |
| strata version (up to date) | Compare installed vs. latest on PyPI (optional, skip if offline) | "Run: pipx upgrade strata" |
| `uv` available | `which uv` / shutil.which | "Install uv: https://docs.astral.sh/uv/" |

#### 2. Tools (`tools`)

Uses existing `IntegrationFactory.create_by_type()` + `is_available()`:

| Check | How | Fix hint |
|-------|-----|----------|
| Terraform installed | `terraform --version` | "Install: https://developer.hashicorp.com/terraform/install" |
| Terraform version ≥ 1.6 | `get_version()` | "Upgrade Terraform to 1.6+" |
| Git installed | `git --version` | "Install Git" |
| Docker installed (if docker integrations configured) | `docker --version` | "Install Docker Desktop" |
| Bitwarden CLI (if bitwarden secrets configured) | `bw --version` | "Install: https://bitwarden.com/help/cli/" |

#### 3. Authentication (`auth`)

| Check | How | Fix hint |
|-------|-----|----------|
| Azure CLI logged in | `az account show` (exit code) | "Run: az login" |
| Azure subscription set | `az account show --query name` | "Run: az account set --subscription NAME" |
| Bitwarden unlocked (if configured) | `bw status` → check `status` field | "Run: bw unlock" |
| Terraform Cloud token (if TFC backend) | Check `~/.terraform.d/credentials.tfrc.json` | "Run: terraform login" |

#### 4. Workspace (`workspace`)

| Check | How | Fix hint |
|-------|-----|----------|
| `.strata/` directory exists | `Path(".strata").exists()` | "Run: strata sln init" |
| `solution.json` valid | Load with `SolutionService` | "Run: strata sln init" |
| `cli.yaml` parseable | Load with yaml | "Delete .strata/cli.yaml and reconfigure with strata config set" |
| Work path resolves | `resolve_work_path()` | "Run from inside a strata workspace, or set STRATA_WORK_PATH" |

#### 5. Configuration (`config`) — only if workspace exists

| Check | How | Fix hint |
|-------|-----|----------|
| Deployment file(s) validate | Run validation on found deploy files | "Run: strata validate --file <path>" |
| `@repo/` references resolve | Check solution.json repo map | "Missing repo. Run: strata repo add <name> <path>" |
| Secret store reachable | Attempt connection to configured store(s) | "Check credentials / network access to secret store" |

---

### Output Format (Console)

```
🏥 strata doctor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Runtime
  ✅ Python 3.13.2
  ✅ strata 2.4.0
  ✅ uv 0.7.2

Tools
  ✅ terraform 1.12.2
  ✅ git 2.45.0
  ✅ docker 27.1.1
  ❌ bitwarden CLI — not found
     → Install: https://bitwarden.com/help/cli/

Authentication
  ✅ Azure CLI — logged in (subscription: MySubscription)
  ⚠️  Bitwarden — locked
     → Run: bw unlock

Workspace
  ✅ .strata/ found
  ✅ solution.json valid (3 repos registered)
  ✅ cli.yaml valid

Configuration
  ✅ deploy/deploy-prd.yaml — valid
  ✅ All @repo references resolve

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Result: 12 passed, 1 warning, 1 failed
```

### Output Format (JSON)

```json
{
  "success": false,
  "summary": {"passed": 12, "warnings": 1, "failed": 1},
  "categories": {
    "runtime": {
      "status": "passed",
      "checks": [
        {"name": "python", "status": "passed", "detail": "3.13.2"},
        ...
      ]
    },
    "tools": {
      "status": "failed",
      "checks": [
        {"name": "bitwarden", "status": "failed", "message": "not found", "fix_hint": "Install: ..."}
      ]
    }
  }
}
```

---

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed (warnings OK) |
| 1 | One or more checks failed |
| 3 | Configuration validation failed (matches strata convention) |

---

### Model

```python
class DoctorCheckResult:
    name: str
    category: str
    status: Literal["passed", "warning", "failed", "skipped"]
    detail: Optional[str]       # e.g., version number
    message: Optional[str]      # human description of issue
    fix_hint: Optional[str]     # actionable fix instruction
```

---

## Implementation Notes

### Reuse existing infrastructure

- **Integration checks:** Reuse `IntegrationFactory.create_by_type()` + `is_available()` + `get_version()` from `ToolsController`
- **Workspace checks:** Reuse `SolutionController` / `SolutionService`
- **Validation checks:** Reuse the validate command path (call controller directly)
- **Auth checks:** New logic, but pattern matches existing integration `_run_integration()` approach

### Conditional checks

Not all checks apply to all users. Skip gracefully:
- Docker checks: skip if no docker integration in configuration
- Bitwarden checks: skip if no bitwarden secrets in environment files
- Azure checks: skip if no Azure provider configured
- Config checks: skip entirely if workspace doesn't exist yet

### What NOT to do

- Don't require initialization — `INIT_REQUIRED = False`
- Don't fail on network errors (PyPI check, etc.) — degrade gracefully with "skipped"
- Don't store state — doctor is purely diagnostic
- Don't call `sys.exit()` — use `click.exceptions.Exit(code)` per CLI conventions

---

## Test Plan

- Unit tests: mock each integration's `is_available()` / `get_version()` to test pass/fail/skip paths
- CLI tests: `CliRunner.invoke(main, ["doctor"])` with mocked environment
- Test `--output json` produces valid JSON matching schema
- Test `--category` filtering
- Test exit codes: 0 when all pass, 1 when any fail
- Test graceful degradation: no network → skip version check, no workspace → skip config checks

---

## Acceptance Criteria

- [ ] `strata doctor` runs without `.strata/` existing (workspace checks show "not found" with fix hint)
- [ ] All check failures include an actionable fix hint (URL or command to run)
- [ ] `--output json` produces machine-parseable results for CI
- [ ] Exit code 0 only when all checks pass
- [ ] Conditional checks skip cleanly (no errors for unconfigured tools)
- [ ] Completes in < 5 seconds for a typical workspace (no slow network calls by default)
- [ ] Registered in CLI as `strata doctor` (top-level, no group)
- [ ] Documented in getting-started.md: "Run strata doctor first if anything isn't working"

---

## Related

- `strata tools status` — narrower (only tool availability, not auth/workspace/config)
- `strata tools check <name>` — deep-check one integration
- Issue #29 — Adoption Readiness Checklist (this is item #11)
- `docs/platform/getting-started.md` — should reference doctor after implementation
