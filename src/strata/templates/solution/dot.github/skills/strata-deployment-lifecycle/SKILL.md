---
name: strata-deployment-lifecycle
description: 'Strata deployment lifecycle: validate/build/dry-run/deploy phases, stages, provisioners, stage-scoped secrets, health checks, and rollback patterns. Use when running or debugging a strata deploy.'
---

# Strata Deployment Lifecycle

## The Four-Phase Flow

Every strata operation follows this pattern:

```
VALIDATE → BUILD → DRY-RUN → DEPLOY
   (1)      (2)      (3)       (4)
```

**Phase 1: VALIDATE**
- Structural validation (schema check)
- Cross-reference validation (files exist, names match)
- Exit 0 = safe to proceed; Exit 3 = schema error

**Phase 2: BUILD**
- Generate Terraform/Ansible artifacts
- Write platform.json (artifact manifest)
- Does NOT resolve secrets and does NOT provision — only creates artifacts

**Phase 3: DRY-RUN**
- Terraform plan (show what would change)
- Ansible play dry-run (show what would run)
- No actual infrastructure changes
- Uses `--dry-run` flag

**Phase 4: DEPLOY**
- Pre-flight: every referenced secret store AND every stage's provisioner tool is checked for availability BEFORE anything runs or the deployment lock is acquired
- Terraform apply (provision infrastructure)
- Ansible run (configure infrastructure)
- Stages execute in order, each scoped to only the secrets it explicitly allowlists
- Exit 0 = success; Exit 1 = failure; Exit 3 = validation failure; Exit 4 = lock conflict

---

## CLI Pattern for Each Phase

```bash
# Phase 1: Validate
strata validate -f deploy/my-deploy.yaml --output json

# Phase 2: Build
strata build run -f deploy/my-deploy.yaml --output json

# Phase 3: Dry-run (build-time dry-run)
strata build run -f deploy/my-deploy.yaml --dry-run --output json

# Phase 4a: Deploy dry-run (provisioner plan)
strata deploy run -f deploy/my-deploy.yaml --dry-run --force --output json

# Phase 4b: Deploy (actual provisioning)
strata deploy run -f deploy/my-deploy.yaml --force --output json
```

**Agent rule:** Always run phases 1-3 BEFORE phase 4. Never skip dry-run.

---

## Stages & Provisioners

A deployment defines **stages** — sequential steps that execute in order. Each stage uses a **provisioner** (the tool that does the work) and an explicit **secrets allowlist**.

```yaml
kind: deployment
meta:
  name: my-deploy
spec:
  stages:
    - name: infrastructure
      provisioner: terraform          # Provisions cloud resources
      scope: all
      on_failure: stop
      secrets: [db_password]          # only this key reaches the terraform subprocess
    - name: configuration
      provisioner: ansible            # Configures provisioned resources
      scope: all
      on_failure: stop
      secrets: ['*']                  # escape hatch: every resolved secret
```

### Supported Provisioners

| Provisioner | Tool             | Purpose                                                 | When to Use                                  |
| ----------- | ---------------- | ---------------------------------------------------------- | ----------------------------------------------- |
| `terraform` | Terraform CLI    | Provision cloud infrastructure (VMs, networks, storage) | Always for infrastructure                    |
| `ansible`   | Ansible playbook | Configure servers after provisioning                    | Post-provision setup, application deployment |

### Stage Execution Rules

- Stages execute **sequentially in the order listed**
- Each stage can **succeed or fail independently**
- `on_failure: stop` (default) — if this stage fails, subsequent stages don't run, and the whole deploy exits non-zero
- `on_failure: rollback` — same halt behavior as `stop`; reserved for stages where you also want to signal a rollback should happen
- `on_failure: continue` — even if this stage fails, continue to the next stage
- `scope: all` — apply to all resources (most common)
- `scope: <name>` — apply to specific resource/module only
- `secrets:` — allowlist of secret keys this stage's provisioner subprocess receives; `[]` or omitted = none, `['*']` = all resolved secrets. **A secret resolving successfully does not mean every stage gets it** — this is scoped per stage. See the `strata-secret-resolution-patterns` skill.

---

## Terraform Provisioner Pattern

```yaml
kind: deployment
meta:
  name: platform-aks-deploy
spec:
  workspace: platform-east
  environments:
    - prod
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop
      secrets: [HETZNER_ROOT_PASSWORD]
```

**Agent workflow:**

```bash
# Build: generates terraform/ directory with .tf files
strata build run -f deploy.yaml --output json
# → creates build/terraform/, platform.json

# Dry-run: terraform plan
strata deploy run -f deploy.yaml --dry-run --force --output json
# → shows what terraform apply would do

# Deploy: terraform apply (provisions cloud resources)
strata deploy run -f deploy.yaml --force --output json
# → creates AKS clusters, storage accounts, networks, etc.
```

**Common Terraform patterns in strata:**

```hcl
# Generated strata artifact example
resource "azurerm_kubernetes_cluster" "main" {
  name                = "platform-aks"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  default_node_pool {
    name       = "default"
    node_count = 3
    vm_size    = "Standard_D4s_v3"
  }
}
```

---

## Ansible Provisioner Pattern

```yaml
kind: deployment
meta:
  name: platform-configure-deploy
spec:
  workspace: platform-east
  environments:
    - prod
  stages:
    - name: infrastructure
      provisioner: terraform
      on_failure: stop
    - name: configuration
      provisioner: ansible
      on_failure: stop
      secrets: [platform_ssh_key]
```

**Agent workflow:**

```bash
# Build: generates ansible/ directory with playbooks
strata build run -f deploy.yaml --output json
# → creates build/ansible/, playbooks

# Dry-run: ansible play (check mode)
strata deploy run -f deploy.yaml --dry-run --force --output json
# → shows what ansible would change (--check mode)

# Deploy: ansible-playbook (configures provisioned resources)
strata deploy run -f deploy.yaml --force --output json
# → installs packages, starts services, deploys apps
```

**SSH key handling (critical for Ansible):**

```yaml
provisioners:
  - name: configure
    provisioner: ansible
    source:
      repository: config
      source_path: ansible
    configuration:
      playbook: site.yml
      ssh_private_key_secret: platform_ssh_key  # Key name, not value
```

**Strata does this automatically:**
1. Reads `ssh_private_key_secret` from the secret store
2. Writes to temp file with `chmod 600`
3. Passes to `ansible-playbook` via `--private-key`
4. Deletes temp file after ansible completes

**Agent rule:** Never put SSH private keys in YAML — always use the secret reference pattern, and make sure the stage's `secrets:` allowlist includes that key (see `strata-secret-resolution-patterns`).

---

## Health Checks & State Verification

After a deployment completes, verify the state:

```bash
# Check deployment status
strata deploy status -f deploy.yaml --output json
# → returns: provisioning_state, resource_health, errors (if any)

# Health check
strata deploy health -f deploy.yaml --output json
# → returns: HEALTHY, DEGRADED, or BROKEN + specific issues

# View deployment history
strata deploy history -f deploy.yaml --output json
# → returns: all past deployments, versions, timestamps
```

**Agent workflow:**
1. Deploy completes (exit 0)
2. Immediately check health: `strata deploy health -f deploy.yaml --output json`
3. If DEGRADED or BROKEN, read the specific issue descriptions
4. If health OK, deployment succeeded

---

## Failure Handling

### Dry-Run Fails → Production Build Will Fail

If dry-run exits with error 1 or 3, the actual deploy will fail for the same reason:

```bash
# Dry-run failed
strata deploy run -f deploy.yaml --dry-run --force --output json
# Exit 3: Validation error — read errors array

# Don't try to deploy
strata deploy run -f deploy.yaml --force --output json  # ← Will fail
```

**Agent rule:** If dry-run fails, fix the issue BEFORE attempting production deploy.

### Single Stage Failure (on_failure: stop / rollback)

If a stage fails and `on_failure` is `stop` or `rollback`:
- That stage is marked FAILED
- Subsequent stages do NOT run
- Deployment exits with a non-zero code

**Recovery:**
```bash
# Check what failed
strata audit list --last --output json

# Fix the issue (usually in provisioner scripts/config, or a missing stage.secrets entry)

# Retry from that stage
strata deploy run -f deploy.yaml --force --output json
# (all stages will re-run; idempotency matters here)
```

### Multi-Stage Deployment Partial Success

If stage 1 (Terraform) succeeds but stage 2 (Ansible) fails:
- Infrastructure exists (not torn down)
- Configuration is incomplete
- Ansible scripts must be idempotent so retry works

### "Resolved but missing" is usually a stage.secrets gap, not a store outage

If a provisioner reports a variable/value has no value, but strata's own logs never showed a "not found" or "store unavailable" warning for that key — the value almost certainly resolved fine at the environment level but was never added to the failing stage's `secrets:` allowlist. Check that YAML before investigating the secret store itself.

---

## Rollback Patterns

Strata does NOT auto-rollback. Recovery depends on the provisioner:

### Terraform Rollback
```bash
# Terraform state is stored in backend
# To rollback: manually destroy and redeploy, or use terraform destroy

strata deploy destroy -f deploy.yaml --force --output json
# This runs: terraform destroy

# Then redeploy
strata deploy run -f deploy.yaml --force --output json
```

**Agent rule:** Terraform is stateful — be careful with destroy. Always verify what will be destroyed with `terraform plan`.

### Ansible Rollback
```bash
# Ansible has no native rollback
# Options:
# 1. Re-run with different playbook (rollback playbook)
# 2. Manually undo on servers
# 3. Rebuild infrastructure via Terraform

# Create rollback.yml in ansible/:
strata deploy run -f deploy-rollback.yaml --force --output json
```

---

## Audit Trail

Every deployment is logged for compliance:

```bash
# Last deployment
strata audit list --last --output json
# → {timestamp, execution_id, stages, exit_code, errors}

# Filter by execution ID
strata audit list --execution-id <id> --output json
# → full log of that deployment

# All errors in last 30 days
strata audit list --level ERROR --minutes $((30*24*60)) --output json
```

**Agent rule:** Log deployment IDs for audit. Never delete audit logs.

---

## Workspace State Locking

During a deployment, state is locked to prevent concurrent modifications:

```bash
# While deploy is running:
# .strata/deployment.lock exists

# If deploy is interrupted (Ctrl+C, network failure):
# Remove the lock manually
rm .strata/deployment.lock

# Then retry
strata deploy run -f deploy.yaml --force --output json
```

**Agent rule:** Never force-remove locks if a deploy is actively running in another terminal. A lock conflict returns exit code 4.

---

## Agent Checklist Before Deploying

- [ ] Validate passed: `strata validate -f deploy.yaml --output json` (exit 0)
- [ ] Build succeeded: `strata build run -f deploy.yaml --output json` (exit 0)
- [ ] Dry-run succeeded: `strata deploy run -f deploy.yaml --dry-run --force --output json` (exit 0)
- [ ] Stages are in logical order (infrastructure before configuration)
- [ ] `on_failure` policy is appropriate (stop, rollback, or continue?)
- [ ] Every stage's `secrets:` allowlist includes every key its provisioner actually needs
- [ ] SSH keys are secret references, not plain values
- [ ] All provisioners exist in the configuration
- [ ] Profile is activated (if deep validation needed)

---

## Common Deployment Issues

| Issue                                     | Cause                                                | Fix                                                     |
| -------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------- |
| "Provisioner not found"                    | Provisioner referenced in stage not in configuration | Add to configuration's `spec.provisioners`             |
| "State locked" (exit 4)                    | Previous deploy didn't complete                       | `rm .strata/deployment.lock` and retry                  |
| "Dry-run succeeds, deploy fails"           | Environment difference (secrets, profiles)            | Check active profile, verify secrets exist              |
| "Ansible fails but Terraform succeeded"    | SSH key not readable, playbook error                   | Check SSH key secret reference, review ansible logs     |
| "Health check returns DEGRADED"            | Resource didn't stabilize, network issues             | Wait and re-check, or investigate specific resource      |
| "No value for required variable" (Terraform) | Value resolved but stage's `secrets:` allowlist omits it | Add the key to that stage's `secrets:` list           |
