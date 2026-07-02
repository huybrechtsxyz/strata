---
agent: agent
description: "Run the full strata deploy pipeline: validate → build → deploy"
---

Help the user deploy using strata. Follow the complete pipeline.

## Step 0: Ask which deployment

Ask the user: **Which deployment file?** (e.g., `deploy/main.yaml`, `deploy/production.yaml`)

If unsure, run:
```bash
strata validate --all
```

And list all `kind: deployment` files that are valid.

## Step 1: Validate the file

```bash
strata validate <file>
```

If there are errors (exit code 3):
- Show each error with the field path
- Ask if you should fix them now or proceed anyway
- If fixing: open the file, correct the issues, re-validate

## Step 2: Show what will change (dry-run)

```bash
strata build run -f <file> --dry-run
```

Report:
- How many artifacts will be generated
- What provisioners will run
- How many stages
- What the output structure looks like

Ask: **Ready to proceed?**

## Step 3: Build

```bash
strata build run -f <file>
```

Report:
- Build succeeded ✅ or failed ❌
- Number of artifacts generated
- List artifact paths (in `.strata/build/`)

## Step 4: Show deploy preview

```bash
strata deploy run -f <file> --dry-run
```

Report:
- What will be deployed
- Which provisioners will execute
- Estimated resources that will be created/updated/deleted
- Any breaking changes or warnings

Ask: **Ready to deploy?** (Require explicit confirmation)

## Step 5: Deploy

```bash
strata deploy run -f <file>
```

Report:
- Deployment succeeded ✅ or failed ❌
- Summary: X resources created, Y updated, Z deleted
- Deployment state (locked/active)
- SBOM written location

## Step 6: Post-deploy check

```bash
strata sln status
```

Show the workspace is now at a later phase (closer to phase 8).

## Final report

> ✅ **Deployment complete!**
> - Deployed to: [stages]
> - Resources: X created, Y updated
> - Next: Verify resources in the infrastructure, or make changes and re-deploy
