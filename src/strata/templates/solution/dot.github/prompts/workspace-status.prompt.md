---
agent: agent
description: "Show workspace status, readiness, and next steps"
---

Show the user the current state of the strata workspace.

## Step 1: Get workspace status

```bash
strata sln status
```

Report:
- Whether `.strata/solution.json` exists
- Workspace name and solution ID
- Number of registered repositories
- Number of profiles

## Step 2: Show readiness checklist

```bash
strata guide show
```

Parse the output and report:
- Current phase (e.g., "Phase 5/8: Environments registered")
- Which phases are complete ✅
- Which phases are pending ⏳
- What phase is blocked and why

## Step 3: Show active profile

```bash
strata profile list
```

Report which profile is active and how many config/environment files are registered with it.

## Step 4: List pending actions

For each incomplete phase, suggest the exact next action. Example:

- **Phase 6 pending:** Run `strata build run -f deploy/main.yaml` to generate build artifacts
- **Phase 7 pending:** Create a deployment manifest with `strata new deployment`
- **Phase 8 pending:** Run `strata deploy run -f deploy/main.yaml` to deploy

## Step 5: Check for validation errors

```bash
strata validate --all
```

If there are any errors, report them and ask if the user wants to fix them now.

## Final report

Summarize:
- Workspace is at phase X/8
- X files registered, Y validation errors
- Next recommended action with the exact command to run
