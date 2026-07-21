# Environment Command Group — Inspecting Deployments

> **Audience:** DevOps engineers and operators who need to inspect the runtime state of
> deployments, read Terraform outputs, check infrastructure health, and troubleshoot issues
> without deploying.

The `strata env` command group lets you inspect deployment state, read Terraform outputs,
and check infrastructure health — all without modifying anything.

---

## What is `strata env`?

The environment command group has five subcommands, all read-only:

| Command      | Purpose                                                  |
| ------------ | -------------------------------------------------------- |
| `env info`   | Workspace context (solution name, profile, version)      |
| `env output` | Terraform outputs from deployed infrastructure           |
| `env show`   | Full resolved environment (variables, secrets, features) |
| `env status` | Current infrastructure status (resources, drift)         |
| `env drift`  | Compare desired vs. actual infrastructure state          |
| `env doctor` | Diagnose connectivity and credential issues              |

---

## env info — Workspace Context

Show instant workspace information without any backend calls.

```bash
strata env info
```

Output:
```
Workspace:        my-platform
Solution ID:      f47ac10b-58cc-4372
Active Profile:   prd
strata Version:   0.16.1
Work Path:        C:\repos\xyz-config\.strata
```

### Use cases

- Verify which workspace and profile are active
- Get the workspace solution ID (for cross-team reference)
- Check strata version compatibility

### Scripting

For shell scripts:
```bash
PROFILE=$(strata env info --output json | jq -r '.active_profile')
VERSION=$(strata env info --output json | jq -r '.version')
```

---

## env output — Terraform Outputs

Read Terraform outputs from deployed infrastructure. These are values provisioned by your
Terraform configurations (e.g., IP addresses, DNS names, resource IDs).

### List all outputs

```bash
strata env output -f deployments/deploy-prd.yaml
```

Output:
```
Outputs by Stage
────────────────

Stage: infrastructure (provisioner: terraform)
  api_endpoint:        https://api.example.com:8080
  db_host:             db.postgres.example.com
  db_port:             5432
  load_balancer_ip:    203.0.113.42
  vpc_id:              vpc-12345678

Stage: helm (provisioner: terraform)
  helm_releases_deployed: 5
```

### Get a single output value

```bash
strata env output -f deployments/deploy-prd.yaml --name api_endpoint
```

Output:
```
api_endpoint: https://api.example.com:8080
```

### Get output for a specific provisioner

```bash
strata env output -f deployments/deploy-prd.yaml --provisioner infrastructure
```

### Raw output (for scripts)

Get just the value, no formatting:

```bash
IP=$(strata env output -f deploy.yaml --name load_balancer_ip --raw)
echo "Registering load balancer at: $IP"
```

### Machine-readable output

For automation:

```bash
strata env output -f deploy.yaml --json
```

Returns:
```json
{
  "file": "deployments/deploy-prd.yaml",
  "stages": [
    {
      "stage": "infrastructure",
      "provisioner": "terraform",
      "outputs": {
        "api_endpoint": "https://api.example.com:8080",
        "load_balancer_ip": "203.0.113.42"
      },
      "ok": true
    }
  ]
}
```

### Use cases

- Extract infrastructure values for monitoring/alerting setup
- Update DNS records after deploying new endpoints
- Populate configuration files for applications
- Integration with third-party tools (Terraform outputs → Consul, Vault, etc.)

---

## env show — Full Resolved Environment

Display the complete resolved environment for a deployment: variables, secrets (masked),
features, overrides, and stages.

```bash
strata env show -f deployments/deploy-prd.yaml
```

Output (truncated):
```
Deployment:      xyz-deploy-prd
Workspace:       xyz-platform
Environment:     prd

Variables (5 resolved)
──────────────────────
  REPLICA_COUNT:      3
  DATACENTER:         us-east-1
  REGION:             eastus
  DB_POOL_SIZE:       20
  LOG_LEVEL:          INFO

Secrets (3 resolved, masked)
────────────────────────────
  DB_PASSWORD:        sk_live_****
  API_TOKEN:          token_****
  SSH_KEY:            -----BEGIN****

Features (2 resolved)
─────────────────────
  enable_caching:     true
  enable_autoscaling: false

Overrides
─────────
  modules:
    api:
      image: myrepo/api:v2.3.0

Stages (3)
──────────
  - infrastructure    (terraform, provisioner: terraform_prod)
  - configuration     (ansible, provisioner: ansible_prod)
  - health-check      (shell, provisioner: shell)
```

### Filter by stage

Show only secrets visible to a specific stage:

```bash
strata env show -f deployments/deploy-prd.yaml --stage infrastructure
```

### Machine-readable output

```bash
strata env show -f deploy.yaml --output json
```

Returns the full resolved configuration as JSON (useful for validation tools, compliance audits,
or automation).

### Use cases

- Audit: verify all variables/secrets are properly resolved before deploying
- Debugging: confirm which values are being used (especially overrides)
- Compliance: export environment configuration for security audits
- Validation: ensure required secrets exist before deploying

---

## env status — Infrastructure Status

Show current infrastructure state from Terraform backend(s). Three modes supported.

### Single deployment status (live backend query)

```bash
strata env status -f deployments/deploy-prd.yaml
```

Runs `terraform show -json` per stage and displays:
- Resource count
- Last-modified timestamp
- Terraform state serial (version number)
- Cache freshness

Output:
```
Deployment Status
──────────────────

Stage: infrastructure (provisioner: terraform)
  Resources:          47
  Last modified:      2026-07-20T16:45:32Z (1 day ago)
  State serial:       1247
  Backend:            azurerm (storage account xyz-terraform-state)

Stage: configuration (provisioner: ansible)
  Status:             configured
  Last modified:      2026-07-20T16:50:15Z (1 day ago)

Summary
───────
Overall status:     ✅ healthy
Total resources:    47
Last change:        2026-07-20T16:45:32Z
```

### Multiple deployments status (path or all)

```bash
# Status for all deployments in a directory
strata env status --path deployments/

# Status for all deployments in the workspace
strata env status --all
```

Shows a table:
```
Deployment Status Summary
─────────────────────────

Deployment           Resources  Last Modified        Status
────────────────────────────────────────────────────────────
deploy-prd           47         2026-07-20 16:45     ✅ healthy
deploy-stg           31         2026-07-18 10:22     ✅ healthy
deploy-dev           15         2026-07-21 09:15     ✅ healthy
```

### Filter by stage

```bash
strata env status -f deploy.yaml --stage infrastructure
```

### Offline mode (no backend queries)

```bash
strata env status -f deploy.yaml --offline
```

Shows cached information only (no backend calls). Useful in air-gapped environments or when
backends are temporarily unreachable.

### Use cases

- Monitor infrastructure health
- Verify state consistency before making changes
- Compare state versions across environments
- Detect if resources have been modified outside of strata

---

## env drift — Detect Infrastructure Drift

Compare desired infrastructure (from your Terraform configs) vs. actual infrastructure
(current state in the backend).

```bash
strata env drift -f deployments/deploy-prd.yaml
```

Output:
```
Infrastructure Drift Detection
──────────────────────────────

Stage: infrastructure (provisioner: terraform)

  Desired vs. Actual:

    No drift detected ✅

    Last check: 2026-07-21 10:05 UTC
    Resources checked: 47
    Drift found: 0

Stage: configuration (provisioner: ansible)

  System drift (outside of strata control):

    ⚠️  File modified: /etc/app/config.yaml
        Desired:  owner=app, mode=0640
        Actual:   owner=root, mode=0644

    ✅ All packages installed
```

### Detect and report specific resources

```bash
strata env drift -f deploy.yaml --resource azurerm_virtual_machine
```

### Machine-readable output

```bash
strata env drift -f deploy.yaml --output json
```

Returns detailed drift report for automation and integration with monitoring tools.

### Use cases

- Regular compliance check (e.g., daily in CI/CD)
- Detect manual changes that need to be corrected
- Troubleshoot "works locally but fails on deploy" issues
- Feed drift alerts into incident management (PagerDuty, etc.)

---

## env doctor — Diagnose Connectivity Issues

Verify that strata can connect to all backends and access required credentials.

```bash
strata env doctor
```

Output:
```
System Diagnostics
──────────────────

✅ Workspace initialized
   Solution ID: f47ac10b-58cc-4372-a567-0e02b2c3d479
   Configuration loaded

✅ Terraform CLI available
   Version: 1.6.0

✅ Ansible CLI available
   Version: 2.15.0

✅ Git CLI available
   Version: 2.42.0

✅ Azure credentials configured
   Subscription: my-org-prod
   Authenticated as: service-principal

⚠️  Vault connectivity
   Status: unreachable
   Error: connection timeout (http://vault.example.com:8200)
   Impact: Deployment will fail if secrets need to be fetched from Vault

✅ Bitwarden CLI available
   Version: 2024.1.0
   Authenticated as: team@example.com

✅ All registrations valid
   Repositories: 3
   Profiles: 3
   Refs: 5
```

### Check specific integration

```bash
strata env doctor --target vault
strata env doctor --target azure
strata env doctor --target bitwarden
```

### Machine-readable output

```bash
strata env doctor --output json
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
strata env doctor

# 2. Check environment is correctly resolved
strata env show -f deploy-prd.yaml

# 3. Verify infrastructure status
strata env status -f deploy-prd.yaml

# 4. Detect any drift that might cause conflicts
strata env drift -f deploy-prd.yaml

# 5. Extract outputs for monitoring setup
ENDPOINT=$(strata env output -f deploy-prd.yaml --name api_endpoint --raw)
echo "Ready to deploy. API endpoint will be: $ENDPOINT"
```

### Workflow 2: Post-deployment verification

```bash
# After deployment completes:

# 1. Check that outputs are available
strata env output -f deploy-prd.yaml

# 2. Verify infrastructure is healthy
strata env status -f deploy-prd.yaml

# 3. Update DNS / load balancers with new outputs
IP=$(strata env output -f deploy-prd.yaml --name load_balancer_ip --raw)
aws route53 change-resource-record-sets --hosted-zone-id XXXXX \
  --change-batch "{ResourceRecords: [{Value: '$IP'}]}"
```

### Workflow 3: Troubleshooting a stalled deployment

```bash
# Deployment seems stuck or failed:

# 1. Check connectivity
strata env doctor

# 2. See what the infrastructure looks like now
strata env status -f deploy-prd.yaml --offline

# 3. Check for drift
strata env drift -f deploy-prd.yaml

# 4. Read Terraform outputs to debug
strata env output -f deploy-prd.yaml
```

### Workflow 4: CI/CD monitoring

```bash
# Daily health check (in CI/CD pipeline)
strata env status --all --output json | \
  jq '.data.deployments[] | select(.status != "healthy") | {name, status, last_modified}'
```

---

## Tips and Tricks

### Extract all outputs to environment variables

```bash
# For shell scripts that need all outputs as env vars
eval $(strata env output -f deploy.yaml --json | \
  jq -r '.stages[0].outputs | to_entries | .[] | "export \(.key)=\(.value)"')

echo "API endpoint is: $api_endpoint"
```

### Monitor infrastructure with Prometheus

```bash
# Collect output values and expose as metrics
strata env status -f deploy.yaml --output json | \
  jq '.data.deployment_metrics | @csv' >> /var/metrics/infrastructure.csv
```

### Compare environments

```bash
# Are dev and prd using the same variable values?
diff \
  <(strata env show -f deploy-dev.yaml --output json | jq .data.variables) \
  <(strata env show -f deploy-prd.yaml --output json | jq .data.variables)
```

### Automated health check in monitoring tools

```bash
# Splunk / ELK integration
strata env status --all --output json | \
  jq '.data | {timestamp: now | todate, deployments}' | \
  curl -X POST https://splunk.example.com/api/push -d @-
```

---

## See Also

- [Commands Reference — env](../platform/commands.md#env) — full command documentation
- [Deploying Infrastructure](./deploying.md) — running deployments
- [Environment Composition](./environment-composition.md) — understanding environment layers
