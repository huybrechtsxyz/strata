# Pattern: Change a Value Across All Environments

> **Audience:** Operator who needs to change a configuration value (RAM, disk size, feature flag, variable) and apply it everywhere consistently.

---

## The layering model

strata merges configuration in this order (later wins):

```
workspace variables  →  environment variables  →  deployment variables
    (base)                   (override)                  (final)
```

Where you make a change depends on the scope you want:

| Scope                | Where to edit                                           | Example                                    |
| -------------------- | ------------------------------------------------------- | ------------------------------------------ |
| All environments     | Workspace (`ws-*.yaml`) or resource (`vm-*.yaml`)       | Bump RAM on all worker VMs                 |
| One environment only | Environment (`env-*.yaml`)                              | Enable debug logging in staging only       |
| One deployment only  | Deployment (`deploy-*.yaml`) — `spec.variables` section | Override a value for a specific deploy run |

---

## Recipe: Change a resource property everywhere

**Scenario:** Bump worker VM RAM from 2048 MB to 4096 MB across all environments.

Since RAM is defined in the resource file (shared by all deployments), edit it once:

```yaml
# stack/vm-worker.yaml
spec:
  configuration:
    cpu_cores: 1
    ram_mb: 4096          # ← changed from 2048
```

Then validate and plan ALL deployments:

```bash
strata validate --file deploy/deploy-prd.yaml
strata validate --file deploy/deploy-stg.yaml

strata build plan --file deploy/deploy-prd.yaml
strata build plan --file deploy/deploy-stg.yaml
```

The plan shows the RAM change propagating to every environment that uses this resource.

---

## Recipe: Change a variable in all environments

**Scenario:** Add a new variable `LOG_RETENTION_DAYS` with value `30` for all environments.

Add it to the workspace (the base layer everyone inherits):

```yaml
# stack/ws-platform.yaml
spec:
  variables:
    - key: LOG_RETENTION_DAYS
      store: constant
      value: "30"
```

If one environment needs a different value, override in that environment file:

```yaml
# envs/env-stg.yaml
spec:
  variables:
    - key: LOG_RETENTION_DAYS
      store: constant
      value: "7"            # staging keeps logs for less time
```

The variable resolution order ensures staging gets `7` while production gets the workspace default `30`.

---

## Recipe: Change a variable in only one environment

**Scenario:** Enable debug mode in staging but not production.

Only edit the staging environment file:

```yaml
# envs/env-stg.yaml
spec:
  variables:
    - key: DEBUG_MODE
      store: constant
      value: "true"
```

Production doesn't have this variable defined in its environment file, so it inherits the workspace default (or the variable simply doesn't exist in production — your Terraform can use a default).

---

## Verification workflow

After making cross-environment changes:

```bash
# 1. Validate all affected deployments
strata validate --file deploy/deploy-prd.yaml
strata validate --file deploy/deploy-stg.yaml

# 2. Build plan — see what would change in each environment
strata build plan --file deploy/deploy-prd.yaml
strata build plan --file deploy/deploy-stg.yaml

# 3. Commit the change
git add -A && git commit -m "Bump worker RAM to 4096 MB"

# 4. Deploy (staging first, then production)
strata deploy run --file deploy/deploy-stg.yaml
strata deploy run --file deploy/deploy-prd.yaml
```

---

## Tips

- **Use `strata build plan` before deploying** — it shows the exact infrastructure changes that would apply.
- **Commit config changes before deploying** — so the git history reflects what was deployed and when.
- **Deploy staging first** — catch issues before they hit production.
- **One change per commit** — makes `git log` useful for debugging later.
