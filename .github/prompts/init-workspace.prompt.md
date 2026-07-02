---
agent: agent
description: "Initialize a new strata workspace from scratch"
---

Guide the user through initializing a new strata workspace.

## Phase 1: Initialize the workspace

```bash
strata sln init
```

Confirm the workspace name and solution ID were created. Check `.strata/solution.json` was created.

## Phase 2: Add configuration repositories

Ask the user:
- How many configuration repos do they have? (git repos containing YAML configs)
- For each repo:
  - Repo name (e.g., `infra`, `platform`, `modules`)
  - Git URL or local path
  - Is it a `config` repository or `infrastructure` repository?
  - Which branch to use? (default: `main`)

Add each one:
```bash
strata repo add <name> <path> --type config --branch main
```

After adding, verify:
```bash
strata repo list
```

## Phase 3: Create profiles

Ask the user:
- What environments do they need? (e.g., `dev`, `stg`, `prd`)

For each profile, create it:
```bash
strata profile create <name>
```

Activate one as the default:
```bash
strata profile activate <name>
```

## Phase 4: Check readiness

```bash
strata guide show
```

This shows an 8-phase checklist. Tell the user:
- Current phase (how many steps complete)
- What's the next step to get to the next phase
- Suggest creating their first configuration file with `strata new configuration`

## Final step

Say: *"Workspace initialized! You're at phase X/8. Next: Create your first configuration file or deploy manifest."*
