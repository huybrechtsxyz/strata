<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Absorbs: z-strata-whoami.md
  Status: Ready to implement (step 1 in implementation order)
-->

# Feature: `strata env info` — Workspace Context Summary

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env info` as a lightweight, instant command that displays the current workspace context — solution identity, active profile, config file, strata version, and work path. No external calls, no health checks.

**Goal:** Answer "Where am I and what's active?" in under a second.

---

## Motivation

- `strata whoami` was proposed (z-strata-whoami.md) but overlaps with `strata doctor` on integration/auth checks
- `strata profile list` / `strata profile show` covers profile info but not solution identity, version, or work path
- Operators switching between workspaces need a fast context check before running deploy commands
- Decision: split whoami into `env info` (context only) and `env doctor` (health checks) to eliminate overlap

---

## Command Interface

```bash
strata env info                # Default: human-readable summary
strata env info --output json  # Machine-readable for scripting
strata env                     # Alias → strata env info
```

No flags beyond `--output`. This command is intentionally minimal.

---

## Output Format (Console)

```
Solution:   haven (7de99a3b)
Profile:    prd ✔ (active)
Config:     config/cfg-haven.yaml
Version:    strata v2.4.0
Work path:  /home/user/haven
```

## Output Format (JSON)

```json
{
  "solution": {
    "name": "haven",
    "id": "7de99a3b"
  },
  "profile": {
    "name": "prd",
    "active": true,
    "config": "config/cfg-haven.yaml"
  },
  "version": "2.4.0",
  "work_path": "/home/user/haven"
}
```

---

## Architecture

```
commands/
  cli_env.py                   ← Click group registration (shared by all env subcommands)
  env/
    __init__.py
    info_command.py             ← EnvInfoCommand extends BaseCommand
controllers/
  env_info_controller.py        ← Gathers solution + profile + version context
```

### Layer Rules

- `EnvInfoCommand` → `EnvInfoController` → `SolutionService` + `ProfileService`
- `INIT_REQUIRED = True` — needs `solution.json` to display solution info
- No integration calls — reads only local state files
- No subprocess invocations

### Data Sources

| Field              | Source                                                   |
| ------------------ | -------------------------------------------------------- |
| Solution name + ID | `SolutionService` → `solution.json`                      |
| Active profile     | `ProfileService` or `solution.json` active profile field |
| Config file path   | Active profile's `configfile_paths`                      |
| Version            | `strata.__version__` or `VERSION.txt`                    |
| Work path          | `ctx.obj["work_path"]`                                   |

---

## Scope Boundary

`env info` does **NOT** include:

- Integration/tool status → use `strata env doctor`
- Authentication checks → use `strata env doctor`
- Backend connection info → future: could add `--verbose` that shows backends
- Deployment state → use `strata env status`

---

## Exit Codes

| Code | Meaning                                         |
| ---- | ----------------------------------------------- |
| 0    | Info displayed successfully                     |
| 1    | Not in a strata workspace (no `.strata/` found) |

---

## Acceptance Criteria

- [ ] `strata env` Click group registered in `cli.py`
- [ ] `strata env info` shows solution, profile, version, work path
- [ ] `strata env` (no subcommand) aliases to `strata env info`
- [ ] `--output json` emits valid JSON with documented schema
- [ ] Command completes in < 1 second (no external calls)
- [ ] `strata env --help` lists all env subcommands

## Relationships

- **Absorbs:** `z-strata-whoami.md` (context-only subset; health checks moved to doctor)
- **Prerequisite for:** All other `strata env *` subcommands (establishes the Click group)
