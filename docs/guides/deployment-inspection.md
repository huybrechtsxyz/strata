# Inspecting Deployments — status, output, show, drift, and doctor

> **Audience:** DevOps engineers and operators who need to inspect the runtime state of
> deployments, read Terraform outputs, check infrastructure health, and troubleshoot issues
> without deploying.

Deployment inspection is split across three command groups, each with a single,
unambiguous scope:

| Group     | Scope                                  | Commands used in this guide                                         |
| --------- | -------------------------------------- | ------------------------------------------------------------------- |
| `deploy`  | One deployment's execution/state       | `deploy show`, `deploy output`, `deploy status`, `deploy drift run` |
| `rollout` | The whole fleet (multiple deployments) | `rollout status`                                                    |
| `sln`     | The workspace itself                   | `sln status`, `sln doctor`                                          |

All of the commands below are read-only — none of them modify anything.

---

## sln status — Workspace Context

Show workspace health: solution identity, active profile, strata version, repositories,
and integration availability. Works with or without an initialized workspace.

```bash
strata sln status
```

### Scripting

```bash
PROFILE=$(strata sln status --output json | jq -r '.data.profiles.active')
VERSION=$(strata sln status --output json | jq -r '.data.version')
```

See [Commands Reference — sln](../platform/commands.md#sln) for the full field list.

---

## deploy output — Terraform Outputs

Read Terraform outputs from deployed infrastructure. These are values provisioned by your
Terraform configurations (e.g., IP addresses, DNS names, resource IDs).

### List all outputs (from cache)

```bash
strata deploy output -f deployments/deploy-prd.yaml
```

Output:
```
Terraform outputs (reading from cache)…

  Stage: infrastructure
       • api_endpoint: https://api.example.com:8080
       • db_host: db.postgres.example.com
       • db_port: 5432
       • load_balancer_ip: 203.0.113.42
       • vpc_id: vpc-12345678
```

### Refresh from the live backend

```bash
strata deploy output -f deployments/deploy-prd.yaml --refresh
```

### Get a single output value

```bash
strata deploy output -f deployments/deploy-prd.yaml --key api_endpoint
```

### Get output for a specific provisioner

```bash
strata deploy output -f deployments/deploy-prd.yaml --provisioner infrastructure
```

### Raw output (for scripts)

Get just the value, no formatting — requires `--key`:

```bash
IP=$(strata deploy output -f deploy.yaml --key load_balancer_ip --raw)
echo "Registering load balancer at: $IP"
```

### Machine-readable output

For automation, bypass the strata envelope entirely:

```bash
strata deploy output -f deploy.yaml --json
```

Or request the standard envelope with `--output json`:

```bash
strata deploy output -f deploy.yaml --output json
```

### Use cases

- Extract infrastructure values for monitoring/alerting setup
- Update DNS records after deploying new endpoints
- Populate configuration files for applications
- Integration with third-party tools (Terraform outputs → Consul, Vault, etc.)

---

## deploy show — Full Resolved Configuration

Display the resolved deployment configuration: effective remote versions, the
workspace/environment files in use, the deployment's stage list, and the full resolved
environment — variables, secrets (masked), features, and overrides.

```bash
strata deploy show -f deployments/deploy-prd.yaml
```

Output (truncated):
```
📋  Deployment:   xyz-deploy-prd
    File:         deployments/deploy-prd.yaml
    Workspace:    xyz-platform
    Environment:  prd (environments/env-prd.yaml)

    Remote Versions:
    ...

    Stages (3):
      • infrastructure  (terraform)
      • configuration   (ansible)
      • health-check    (shell)

  ────────────────────────────────────────────────────────────────────
  🌍  Resolved environment — prd

  Variables (5):
    REPLICA_COUNT  = 3
    DATACENTER     = us-east-1
    ...

  Secrets (3):
    DB_PASSWORD  = sk_l****
    API_TOKEN    = toke****

  Features (2):
    enable_caching      ✓ enabled
    enable_autoscaling  ✗ disabled
```

### Filter by stage

Filter secrets visibility to a specific stage's allowlist:

```bash
strata deploy show -f deployments/deploy-prd.yaml --stage infrastructure
```

### Machine-readable output

```bash
strata deploy show -f deploy.yaml --output json
```

Returns the full resolved configuration as JSON (useful for validation tools, compliance
audits, or automation).

### Use cases

- Audit: verify all variables/secrets are properly resolved before deploying
- Debugging: confirm which values are being used (especially overrides)
- Compliance: export environment configuration for security audits
- Validation: ensure required secrets exist before deploying

---

## deploy status — Live Infrastructure Status

Show the live status of a single deployment: per-stage resource count, output
count/keys, last apply serial number, and cache freshness.

```bash
strata deploy status -f deployments/deploy-prd.yaml
```

Output:
```
📊  Deployment status (live) — xyz-deploy-prd
    2 stage(s)

  ✅ infrastructure  (terraform)
       Resources: 47
       Outputs: 5
       State serial: 1247
       Cache: refreshed 2026-07-20T16:45:32Z

  ✅ configuration  (ansible)
       Cache: refreshed 2026-07-20T16:50:15Z
```

### Filter by stage

```bash
strata deploy status -f deploy.yaml --stage infrastructure
```

### Offline mode (no backend queries)

```bash
strata deploy status -f deploy.yaml --offline
```

Shows cached information only (no backend calls). Useful in air-gapped environments or
when backends are temporarily unreachable.

### Use cases

- Monitor infrastructure health for one deployment
- Verify state consistency before making changes
- Detect if resources have been modified outside of strata

> For plan-diffing (what *would* change) use `strata deploy plan` instead. For
> fleet-wide, multi-deployment scanning use `strata rollout status` (below).

---

## rollout status — Fleet-Wide Summary

Scan a directory or the entire workspace for deployment manifests and print a one-line
status summary per deployment. Always offline — reads only the local build cache.

```bash
# Status for all deployments in a directory
strata rollout status --path deployments/

# Status for all deployments in the workspace
strata rollout status --all
```

Shows a summary per deployment, with a cached/stage breakdown:
```
📊  Deployment Status — 3 deployment(s) [--all]

  ✅ deploy-prd  (2/2 stages cached)
      ✓ infrastructure  2026-07-20T16:45:32Z  5 output(s)
      ✓ configuration    2026-07-20T16:50:15Z  0 output(s)

  ✅ deploy-stg  (1/1 stages cached)
      ✓ infrastructure  2026-07-18T10:22:00Z  5 output(s)
```

### Use cases

- Daily CI/CD health check across every deployment in the workspace
- Spot deployments that have never been applied (0 cached stages)
- Fleet-wide dashboards without querying every backend live

```bash
# Daily health check (in CI/CD pipeline)
strata rollout status --all --output json | \
  jq '.data.deployments[] | select(.cached_count < .stage_count) | {name, stage_count, cached_count}'
```

---

## deploy drift — Detect Infrastructure Drift

Compare desired infrastructure (from your Terraform configs) vs. actual infrastructure
(current state in the backend) by running `terraform plan` per stage.

```bash
strata deploy drift run -f deployments/deploy-prd.yaml
```

Output:
```
  ✅ infrastructure  — no drift
  ⚠️  configuration  — drift detected: ~2 update
```

`deploy drift` is a subgroup with `run`, `acknowledge`, and `history` — it supersets what
the old `env drift` command did (severity thresholds, baseline acknowledgement, AI
explanation, and run history). See
[Commands Reference — deploy drift](../platform/commands.md#deploy) for the full flag list.

### Machine-readable output

```bash
strata deploy drift run -f deploy.yaml --output json
```

### Use cases

- Regular compliance check (e.g., daily in CI/CD)
- Detect manual changes that need to be corrected
- Troubleshoot "works locally but fails on deploy" issues
- Feed drift alerts into incident management (PagerDuty, etc.)

---

## sln doctor — Diagnose Connectivity Issues

Verify that strata can connect to all backends and access required credentials. Runs
five check categories: `runtime`, `workspace`, `tools`, `config`, `auth`.

```bash
strata sln doctor
```

### Check a specific category

```bash
strata sln doctor --category tools
strata sln doctor --category auth --deep
```

### AI explanation of failures

```bash
strata sln doctor --ai
```

### Machine-readable output

```bash
strata sln doctor --output json
```

Returns detailed diagnostic info for automation.

### Use cases

- Troubleshoot "why did the deployment fail?" issues
- Verify new credentials before deploying
- CI/CD health check (ensure all integrations are ready)
- Onboarding: verify a developer's machine is properly configured

---

## Common Workflows

### Workflow 1: Pre-deployment checklist

```bash
# 1. Verify workspace is healthy
strata sln doctor

# 2. Check environment is correctly resolved
strata deploy show -f deploy-prd.yaml

# 3. Verify infrastructure status
strata deploy status -f deploy-prd.yaml

# 4. Detect any drift that might cause conflicts
strata deploy drift run -f deploy-prd.yaml

# 5. Extract outputs for monitoring setup
ENDPOINT=$(strata deploy output -f deploy-prd.yaml --key api_endpoint --raw)
echo "Ready to deploy. API endpoint will be: $ENDPOINT"
```

### Workflow 2: Post-deployment verification

```bash
# After deployment completes:

# 1. Check that outputs are available
strata deploy output -f deploy-prd.yaml

# 2. Verify infrastructure is healthy
strata deploy status -f deploy-prd.yaml

# 3. Update DNS / load balancers with new outputs
IP=$(strata deploy output -f deploy-prd.yaml --key load_balancer_ip --raw)
aws route53 change-resource-record-sets --hosted-zone-id XXXXX \
  --change-batch "{ResourceRecords: [{Value: '$IP'}]}"
```

### Workflow 3: Troubleshooting a stalled deployment

```bash
# Deployment seems stuck or failed:

# 1. Check connectivity
strata sln doctor

# 2. See what the infrastructure looks like now
strata deploy status -f deploy-prd.yaml --offline

# 3. Check for drift
strata deploy drift run -f deploy-prd.yaml

# 4. Read Terraform outputs to debug
strata deploy output -f deploy-prd.yaml
```

### Workflow 4: CI/CD fleet monitoring

```bash
# Daily health check across every deployment (in CI/CD pipeline)
strata rollout status --all --output json | \
  jq '.data.deployments[] | select(.cached_count < .stage_count) | {name, stage_count, cached_count}'
```

---

## Tips and Tricks

### Extract all outputs to environment variables

```bash
# For shell scripts that need all outputs as env vars
eval $(strata deploy output -f deploy.yaml --json | \
  jq -r 'to_entries | .[] | "export \(.key)=\(.value)"')

echo "API endpoint is: $api_endpoint"
```

### Compare environments

```bash
# Are dev and prd using the same variable values?
diff \
  <(strata deploy show -f deploy-dev.yaml --output json | jq .data.environment_detail.variables) \
  <(strata deploy show -f deploy-prd.yaml --output json | jq .data.environment_detail.variables)
```

---

## See Also

- [Commands Reference — deploy](../platform/commands.md#deploy) — full command documentation
- [Commands Reference — rollout](../platform/commands.md#rollout) — fleet-wide status
- [Commands Reference — sln](../platform/commands.md#sln) — workspace status and doctor
- [Deploying Infrastructure](./deploying.md) — running deployments
- [Environment Composition](./environment-composition.md) — understanding environment layers
