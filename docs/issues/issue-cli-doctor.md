<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Absorbs: z-strata-doctor.md
  Status: Ready to implement (step 2 in implementation order)
-->

# Feature: `strata env doctor` — Environment Health Check

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env doctor` as a comprehensive health check that validates the local development environment — Python runtime, required tools, authentication, workspace state, and configuration — providing clear ✅/❌ output with actionable fix hints on every failure.

**Goal:** Eliminate "it doesn't work and I don't know why" support calls. A new team member runs `strata env doctor` and immediately knows what's missing and how to fix it.

---

## Motivation

- Onboarding friction: new engineers waste 30+ minutes diagnosing missing tools, wrong Python versions, or expired auth tokens
- `strata tools status` checks tool availability but NOT: Python version, workspace state, auth status, secret store access, or config validity
- Operators need one command to answer: "Is my local environment ready to deploy?"
- Overlap with `strata whoami` resolved: info = context (who/where), doctor = health (is everything working)

---

## Command Interface

```bash
strata env doctor                       # Full check (all categories)
strata env doctor --category runtime    # Check only one category
strata env doctor --fix                 # Attempt auto-fix where possible (future)
strata env doctor --output json         # Machine-readable for CI
```

---

## Architecture

```
commands/
  env/
    doctor_command.py           ← DoctorCommand extends BaseCommand
controllers/
  doctor_controller.py          ← Orchestrates check categories
```

### Layer Rules

- `DoctorCommand` → `DoctorController` → existing integrations/services
- `INIT_REQUIRED = False` — doctor must work even in a broken/uninitialized workspace
- Each check returns a `DoctorCheckResult` (name, status, message, fix_hint)
- Categories are independent; a failing workspace check does not skip config checks

---

## Check Categories

### 1. Runtime (`runtime`)

| Check                       | How                                                              | Fix hint                                             |
| --------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------- |
| Python version ≥ 3.13       | `sys.version_info`                                               | "Install Python 3.13+: https://python.org/downloads" |
| strata version (up to date) | Compare installed vs. latest on PyPI (optional, skip if offline) | "Run: pipx upgrade strata"                           |
| `uv` available              | `shutil.which("uv")`                                             | "Install uv: https://docs.astral.sh/uv/"             |

### 2. Tools (`tools`)

Uses existing `IntegrationFactory.create_by_type()` + `is_available()`:

| Check                            | How                   | Fix hint                                                     |
| -------------------------------- | --------------------- | ------------------------------------------------------------ |
| Terraform installed              | `terraform --version` | "Install: https://developer.hashicorp.com/terraform/install" |
| Terraform version ≥ 1.6          | `get_version()`       | "Upgrade Terraform to 1.6+"                                  |
| Git installed                    | `git --version`       | "Install Git"                                                |
| Docker installed (if configured) | `docker --version`    | "Install Docker Desktop"                                     |
| Bitwarden CLI (if configured)    | `bw --version`        | "Install: https://bitwarden.com/help/cli/"                   |

### 3. Authentication (`auth`)

| Check                                  | How                                          | Fix hint                                  |
| -------------------------------------- | -------------------------------------------- | ----------------------------------------- |
| Azure CLI logged in                    | `az account show` (exit code)                | "Run: az login"                           |
| Azure subscription set                 | `az account show --query name`               | "Run: az account set --subscription NAME" |
| Bitwarden unlocked (if configured)     | `bw status` → check `status` field           | "Run: bw unlock"                          |
| Terraform Cloud token (if TFC backend) | Check `~/.terraform.d/credentials.tfrc.json` | "Run: terraform login"                    |

### 4. Workspace (`workspace`)

| Check                       | How                         | Fix hint                                                         |
| --------------------------- | --------------------------- | ---------------------------------------------------------------- |
| `.strata/` directory exists | `Path(".strata").exists()`  | "Run: strata sln init"                                           |
| `solution.json` valid       | Load with `SolutionService` | "Run: strata sln init"                                           |
| `cli.yaml` parseable        | Load with yaml              | "Delete .strata/cli.yaml and reconfigure with strata config set" |
| Work path resolves          | `resolve_work_path()`       | "Run from inside a strata workspace, or set STRATA_WORK_PATH"    |

### 5. Configuration (`config`) — only if workspace exists

| Check                       | How                                       | Fix hint                                             |
| --------------------------- | ----------------------------------------- | ---------------------------------------------------- |
| Deployment file(s) validate | Run validation on found deploy files      | "Run: strata validate --file <path>"                 |
| `@repo/` references resolve | Check solution.json repo map              | "Missing repo. Run: strata repo add <name> <path>"   |
| Secret store reachable      | Attempt connection to configured store(s) | "Check credentials / network access to secret store" |

---

## Output Format (Console)

```
🏥 strata env doctor
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

## Output Format (JSON)

```json
{
  "success": false,
  "summary": {"passed": 12, "warnings": 1, "failed": 1},
  "categories": {
    "runtime": {
      "status": "passed",
      "checks": [
        {"name": "python", "status": "passed", "detail": "3.13.2"}
      ]
    },
    "tools": {
      "status": "failed",
      "checks": [
        {"name": "bitwarden", "status": "failed", "message": "not found", "fix_hint": "Install: https://bitwarden.com/help/cli/"}
      ]
    }
  }
}
```

---

## Model

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

## Exit Codes

| Code | Meaning                                                     |
| ---- | ----------------------------------------------------------- |
| 0    | All checks passed (warnings OK)                             |
| 1    | One or more checks failed                                   |
| 3    | Configuration validation failed (matches strata convention) |

---

## Scope Boundary

`env doctor` does **NOT** include:

- Deployment state (deployed/drifted) → use `strata env status`
- Terraform outputs → use `strata env output`
- Solution/profile context display → use `strata env info`

---

## Implementation Notes

- Reuse existing `IntegrationFactory.create_by_type()` + `is_available()` for tool checks
- Reuse `SolutionService` for workspace validation
- Auth checks use subprocess calls to `az`, `bw` etc. — wrap via existing integration layer
- `--category` accepts: `runtime`, `tools`, `auth`, `workspace`, `config`
- `--fix` is future scope — stub the flag, print "auto-fix not yet implemented" if used

---

## Acceptance Criteria

- [ ] `strata env doctor` runs all 5 check categories
- [ ] Each failed check shows a fix hint
- [ ] `--category runtime` runs only runtime checks
- [ ] `--output json` emits valid JSON with documented schema
- [ ] `INIT_REQUIRED = False` — works outside a strata workspace (runtime + tools + auth still run)
- [ ] Exit code 0 when all pass, 1 when any fail, 3 for config validation failures
- [ ] `strata tools status` continues to work (not removed, may deprecate later)

## Relationships

- **Absorbs:** `z-strata-doctor.md` (full design carried forward, command renamed)
- **Depends on:** `x-strata-cli-info.md` (env group must exist first)
- **Related:** `z-strata-auditlog.md` (doctor config category should check audit log config)
