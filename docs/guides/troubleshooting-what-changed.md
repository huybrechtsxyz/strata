# Troubleshooting: What Changed?

> **Audience:** Operator responding to a production issue who needs to quickly determine what changed, when, and why.

---

## The 5-minute investigation

When something breaks, run through these steps in order:

### 1. Check current drift

```bash
strata diff --file deploy/deploy-prd.yaml
```

This compares the current configuration against the last-applied state. If there's drift, you'll see exactly which resources differ and how.

**No drift?** The issue isn't a configuration change — look at application logs, external dependencies, or upstream provider outages.

**Drift found?** Continue to step 2 to find out who/what caused it.

### 2. Check execution history

```bash
strata log list
```

This shows recent `build` and `deploy` operations with timestamps, exit codes, and the deployment file used. Look for recent operations that correlate with the start of the issue.

### 3. Check git history for the deployment file

```bash
# What changed in the deployment config?
git log --oneline -10 deploy/deploy-prd.yaml

# What changed in the environment?
git log --oneline -10 envs/env-prd.yaml

# What changed in the workspace or resources?
git log --oneline -10 stack/
```

### 4. See the exact diff for a suspicious commit

```bash
git show <commit-hash> -- deploy/ envs/ stack/
```

### 5. Rebuild and compare

If you suspect the current build output doesn't match what's deployed:

```bash
# See what build run WOULD produce now
strata build plan --file deploy/deploy-prd.yaml

# Compare with what's currently in the build output directory
ls .strata/build/haven_deploy_prd/infrastructure/
```

---

## Common scenarios

### "A variable value changed unexpectedly"

Variables layer: workspace → environment → deployment. Check each file:

```bash
# Which file sets this variable?
grep -r "MY_VARIABLE" stack/ envs/ deploy/
```

The last file in the merge order wins. If `envs/env-prd.yaml` was recently edited, that's likely the source.

### "A new resource appeared / disappeared"

Resources are defined in workspace files. Check:

```bash
git log --oneline -5 stack/ws-platform.yaml
git diff HEAD~1 stack/ws-platform.yaml
```

### "Secrets stopped resolving"

Secrets are fetched at build time from the configured store (Bitwarden, Key Vault, etc.).

```bash
# Check which secrets are defined
grep -A2 "store:" envs/env-prd.yaml

# Rebuild with verbose to see secret resolution
strata build run --file deploy/deploy-prd.yaml --verbose
```

Common causes: expired credentials, rotated secret IDs, vault access policy changes.

### "Terraform apply failed but config looks right"

The build output is plain Terraform. Inspect it directly:

```bash
# Look at what strata generated
cat .strata/build/haven_deploy_prd/infrastructure/terraform.tfvars.json

# Run terraform plan manually for more detail
cd .strata/build/haven_deploy_prd/infrastructure/
terraform init
terraform plan
```

---

## Timeline reconstruction

To build a full timeline of what happened:

```bash
# 1. When was the last successful deploy?
strata log list | head -5

# 2. What config commits happened since then?
git log --oneline --since="2025-01-15" -- deploy/ envs/ stack/ config/

# 3. What's the current state vs. desired state?
strata diff --file deploy/deploy-prd.yaml
```

---

## Prevention

| Practice                                  | Why                                                           |
| ----------------------------------------- | ------------------------------------------------------------- |
| Run `strata diff` on a schedule (CI cron) | Catch drift before users notice                               |
| Commit before deploying                   | Git history = audit trail                                     |
| Use `strata build plan` in PRs            | Review infrastructure changes like code                       |
| Tag deployments in git                    | `git tag deploy-prd-2025-01-15` makes rollback points obvious |
| Use separate secrets per environment      | Rotating one environment's secrets doesn't break another      |
