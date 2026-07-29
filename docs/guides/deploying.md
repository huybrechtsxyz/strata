# Deploying Infrastructure

> **Audience:** DevOps engineers running `strata deploy run` in production. You understand the
> workspace/deployment/environment model and want to know how to execute deployments safely,
> understand output, and handle partial failures.

`strata deploy run` is the command that applies infrastructure changes: provisioning resources,
running configuration management, and enforcing state consistency. This guide covers the practical
operational aspects.
> **Tip:** Deployments can be paused at **approval gates** (cost review, security review, manual verification). See [Deployment Approval Gates](./deployment-approval-gates.md) for the full workflow.
---

## Before You Deploy

### 1. Validate the deployment file

Always validate before deploying:

```bash
strata validate -f deployments/deploy-prd.yaml
strata validate -f deployments/deploy-prd.yaml --deep
```

`--deep` checks cross-file references and secret store connectivity. Fix any validation errors
before proceeding.

### 2. Check the dry-run plan

```bash
strata deploy run -f deployments/deploy-prd.yaml --dry-run
```

This:
- Validates all YAML
- Resolves all variables, secrets, and features
- Runs `terraform plan` (if Terraform is the provisioner)
- Prints what **will** change
- Writes **nothing** to the backend or infrastructure

Read the plan carefully. If the proposed changes are not what you expect, debug before proceeding.

### 3. Verify state lock status

If locking is enabled (`spec.locking.enabled: true` in the deployment):

```bash
strata deploy lock status -f deployments/deploy-prd.yaml
```

If a lock is held, find out who holds it and why. Do not force-release unless the lock is
stale (the owning process crashed).

---

## Running a Deployment

### Basic deployment

```bash
strata deploy run -f deployments/deploy-prd.yaml
```

strata will:
1. Acquire the deployment lock (if enabled)
2. Run `before_deploy` lifecycle hooks
3. Execute stages sequentially (by default)
4. Run `after_deploy` lifecycle hooks
5. Release the lock
6. Write an audit record to `.strata/deploy-log/`

### Non-interactive mode (CI/CD)

In CI/CD pipelines, add `--force` to skip interactive prompts:

```bash
strata deploy run -f deployments/deploy-prd.yaml --force
```

### Deploy a single stage

To deploy only a specific stage (and skip others):

```bash
strata deploy run -f deployments/deploy-prd.yaml --stage networking
```

Useful when:
- A previous deployment failed and you're retrying a specific stage
- You're testing one stage in isolation
- You want to defer later stages (e.g., Ansible configuration) to a separate run

### Dry-run: verify without applying

```bash
strata deploy run -f deployments/deploy-prd.yaml --dry-run
```

Runs the full workflow (validation, planning, hooks) but does not apply any changes:
- Terraform: `plan` mode only (no `apply`)
- Ansible: playbooks not executed
- Infrastructure: no resources created/modified
- Files: nothing written

---

## Understanding Deployment Output

### Console output structure

```
=== strata deploy run ===
Deployment:     xyz-deploy-prd
Workspace:      xyz-platform
Environment:    prd
Stages:         3
Lock:           enabled (acquired)

Stage: infrastructure (1/3)
─────────────────────────
Provisioner:    terraform
Status:         running...

  Terraform plan summary:
    Add:        12
    Modify:     3
    Delete:     0

  Resources:
    + azurerm_resource_group.platform
    + azurerm_virtual_network.core
    + azurerm_network_security_group.allow_http
    ~ azurerm_storage_account.logs (modify tags)
    ...

  Apply: ✅ success (24s)

Stage: configuration (2/3)
──────────────────────────
Provisioner:    ansible
Status:         running...

  Playbooks:
    - site.yml                      ✅ ok

  Tasks run:      42
  Handlers run:    5
  Duration:       1m 23s

Stage: health-check (3/3)
─────────────────────────
Provisioner:    shell-script
Status:         running...

  Script: health-check.sh
  Status: ✅ ok
  Duration: 8s

Summary
───────
Total duration: 3m 15s
All stages:     ✅ success

Audit record written to: .strata/deploy-log/2026-07-21T10-30-45Z-deploy-prd.json
```

### Key fields to watch

| Section           | What to look for                                         | Action if wrong                                                                                   |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| **Lock**          | Should show `acquired` or `skipped`                      | If it shows `failed`, another deployment is already running — wait or investigate the lock holder |
| **Plan summary**  | Check Add/Modify/Delete counts against your expectations | If surprised by proposed changes, stop and debug                                                  |
| **Resource list** | Scan for resources you didn't intend to modify           | If you see unexpected `~` (modify) or `+` (add), stop and revalidate                              |
| **Apply status**  | Should show `✅ success` per stage                        | See Handling Failures section if any stage fails                                                  |
| **Duration**      | Reasonable? Terraform usually 10-30s, Ansible 30s-5m     | Unusually long runtimes might indicate hanging connections                                        |
| **Audit record**  | Path printed at end                                      | Verify it exists: `ls -la .strata/deploy-log/`                                                    |

### Structured output (JSON/NDJSON)

For CI/CD and log aggregation:

```bash
strata deploy run -f deployments/deploy-prd.yaml --output json
```

Returns a JSON envelope after completion:

```text
{
  "success": true,
  "data": {
    "deployment": "xyz-deploy-prd",
    "environment": "prd",
    "stages": [
      {
        "name": "infrastructure",
        "provisioner": "terraform",
        "status": "success",
        "duration_seconds": 24,
        "resources": {
          "add": 12,
          "modify": 3,
          "delete": 0
        }
      },
      ...
    ],
    "total_duration_seconds": 195,
    "audit_record_path": ".strata/deploy-log/2026-07-21T10-30-45Z-deploy-prd.json"
  },
  "messages": []
}
```

---

## Handling Deployment Failures

### Exit codes and meanings

| Exit Code | Meaning                                             | What to do                                                                            |
| --------- | --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `0`       | ✅ Success — all stages completed                    | Nothing. Deployment is complete.                                                      |
| `1`       | ❌ System error — strata crashed                     | Check `strata log list --last` for error details. Report if reproducible.             |
| `2`       | ❌ Invalid arguments                                 | Check your command syntax. Run `strata deploy run --help` to verify.                  |
| `3`       | ❌ Validation error — YAML invalid or policy failed  | Fix the YAML and re-validate. If policy failed, adjust config or policy.              |
| `4`       | 🔄 Lock conflict — another deployment holds the lock | Wait for the other deployment to complete, then retry. Do NOT force-release the lock. |

### Partial failure (a stage failed mid-deployment)

If a stage fails, the lock is held and subsequent stages are skipped. Example:

```
Stage: infrastructure (1/3)
──────────────────────────
Status: ✅ success

Stage: configuration (2/3)
──────────────────────────
Status: ❌ FAILED
Error:  Ansible playbook site.yml exited with code 1

  Task: restart-services
  Error: SSH connection timeout on web-02

  [Ansible output saved to: .strata/deploy-log/2026-07-21T10-30-45Z-ansible-output.log]

Stage: health-check (3/3)
─────────────────────────
Status: ⏭️  SKIPPED (infrastructure stage failed)

Summary
───────
All stages: ❌ FAILED
Lock: still held (to prevent concurrent corrections)
```

**To recover from a partial failure:**

1. **Investigate the error.** Read the full error output and any auxiliary logs (Ansible stdout,
   Terraform log, etc.).

2. **Fix the root cause.** This might be:
   - Network connectivity issue → fix the network, wait for recovery
   - Bad SSH key → update credentials and re-validate
   - Insufficient resources → scale up or wait for resource availability
   - Code bug in playbook/script → fix and re-deploy

3. **Retry the failed stage:**
   ```bash
   strata deploy run -f deployments/deploy-prd.yaml --stage configuration --force
   ```
   Runs only the `configuration` stage (not the earlier `infrastructure` stage which already passed).

4. **Or retry the full deployment** (if the earlier stages are idempotent):
   ```bash
   strata deploy run -f deployments/deploy-prd.yaml --force
   ```
   The earlier stages will re-run (Terraform/Ansible will skip no-ops if the state is consistent).

### State inspection after failure

Check current infrastructure state without re-deploying:

```bash
strata env status -f deployments/deploy-prd.yaml
```

This shows:
- Resource count per stage
- Last-modified timestamp
- Terraform state serial (version) — if it didn't increase, nothing actually changed
- Any resource drift since the last successful deploy

### Stale locks

If a lock is held but you know the owning process crashed:

```bash
strata deploy lock release -f deployments/deploy-prd.yaml --force
```

Use sparingly. If the original process is still running, forcing the lock release can cause
concurrent modifications to the same infrastructure, which is dangerous.

---

## Deployment Stages and Execution

### Sequential vs. parallel stages

By default, stages run sequentially (one at a time). This is safe — later stages can depend on
outputs from earlier stages.

**Typical stage order:**

```yaml
spec:
  stages:
    - name: foundation       # VNets, subnets, security groups
    - name: infrastructure   # Compute, databases, storage
    - name: configuration    # Ansible playbooks for OS setup
    - name: health-check     # Integration tests
```

### Stage dependencies

Later stages can reference outputs from earlier stages. Example:

```yaml
# Stage 1: terraform provisions infrastructure
provisioners:
  - name: terraform
    provisioner: terraform
    source: ...

stages:
  - name: infrastructure
    provisioner: terraform

  # Stage 2: Ansible receives IPs from Stage 1 outputs
  - name: configure
    provisioner: ansible
    configuration:
      extra_vars:
        web_servers: "{{ env.output.web_server_ips }}"

  # Stage 3: health-check can reference both Stage 1 and 2 outputs
  - name: health-check
    provisioner: shell-script
    script: |
      #!/bin/bash
      API_ENDPOINT="{{ env.output.api_endpoint }}"
      curl -f "$API_ENDPOINT/health" || exit 1
```

### Conditional stages

You can conditionally skip stages using environment overrides:

```yaml
# deployments/deploy-prd.yaml
spec:
  environment:
    - env-prd                        # loads env-prd.yaml
  stages:
    - name: infrastructure
      provisioner: terraform
    - name: configure
      provisioner: ansible
      skip: "{{ env.variables.skip_ansible }}"   # can be true/false
```

In `env-prd.yaml`:
```yaml
spec:
  variables:
    - key: skip_ansible
      store: constant
      value: "false"
```

---

## Lifecycle Hooks and Customization

### Before/After hooks

Run custom scripts before/after each stage:

```yaml
stages:
  - name: infrastructure
    provisioner: terraform
    lifecycle_events:
      before_stage:
        - script: |
            echo "About to provision infrastructure..."
            terraform validate
        - script: |
            notify-slack "Starting infrastructure deploy"
      after_stage:
        - script: |
            echo "Infrastructure provisioned. Running smoke tests..."
            ./scripts/smoke-test.sh
```

Scripts have access to environment variables injected by strata:

| Variable                | Value                                                     |
| ----------------------- | --------------------------------------------------------- |
| `STRATA_PHASE`          | Current lifecycle phase (e.g., `deploy_provision_before`) |
| `STRATA_WORKSPACE_PATH` | Path to workspace root                                    |
| `STRATA_CONFIG_PATH`    | Path to `.strata/`                                        |
| `STRATA_BUILD_PATH`     | Path to build artifacts                                   |

### Failure handling

By default, if a hook fails, the stage fails and subsequent stages are skipped. You can continue
despite hook failures:

```yaml
lifecycle_events:
  after_stage:
    - script: ./optional-healthcheck.sh
      on_failure: ignore   # continue even if this script exits non-zero
```

---

## Deployment Validation and Safety

### Pre-deployment checklist

```bash
# 1. Validate YAML syntax
strata validate -f deployments/deploy-prd.yaml

# 2. Cross-file validation (secret stores, references, etc.)
strata validate -f deployments/deploy-prd.yaml --deep

# 3. Check if all required secrets exist
strata env show -f deployments/deploy-prd.yaml | grep "status: required"

# 4. Dry-run to see what will change
strata deploy run -f deployments/deploy-prd.yaml --dry-run

# 5. Review the audit trail to see who deployed what last time
strata audit changes --last 5

# 6. Check if lock is free
strata deploy lock status -f deployments/deploy-prd.yaml
```

### Approval gates (in CI/CD)

For high-risk environments, require approval before deploying:

```yaml
# Azure Pipelines
- stage: Deploy
  jobs:
    - job: waitForValidation
      displayName: Waiting for manual approval
      pool: server
      timeoutInMinutes: 1440
      steps:
        - task: ManualValidation@0
          timeoutInMinutes: 1440

    - job: Deploy
      dependsOn: waitForValidation
      steps:
        - script: strata deploy run -f deployments/deploy-prd.yaml --force
```

---

## Audit Trail and Compliance

Every successful deploy writes an audit record:

```bash
# List recent deployments
strata audit changes

# Show detailed record for one deployment
strata audit changes --last 1 --output json

# Export records for compliance tools (Splunk, SIEM, etc.)
strata audit export --since 2026-06-01T00:00:00Z --format json
```

Each record captures:
- Who ran the deployment (commit author + git user)
- When it ran (ISO 8601 timestamp)
- Which stages ran and their status
- Infrastructure changes (resources added/modified/deleted)
- Any PR metadata linked to the commit

---

## Common Scenarios

### Scenario 1: Rollback a deployment

strata doesn't have a built-in rollback. Instead:

1. Revert the commit(s) that introduced the bad change
2. Merge the revert
3. Run `strata deploy run -f deployments/deploy-prd.yaml --force`
4. Terraform will detect the drift and correct it

For faster manual rollback (if needed):

```bash
# Restore from a previous Terraform state backup
strata deploy run -f deployments/deploy-prd.yaml --stage infrastructure --force
```

### Scenario 2: Deploy just one module's changes

If you modified only one module and want to redeploy it:

```bash
# Edit your module, then re-run Terraform for that module only
strata deploy run -f deployments/deploy-prd.yaml --stage infrastructure --force
```

Terraform will detect what changed and apply only the diff.

### Scenario 3: Deployment takes too long

If `strata deploy run` times out or hangs:

1. Check CI/CD logs for the last output
2. SSH into the infrastructure or check provisioner logs (Terraform state, Ansible output)
3. Determine if it's stuck or just slow
4. **If truly stuck:** Manually stop the process, release the lock, and investigate

```bash
strata deploy lock release -f deployments/deploy-prd.yaml --force
```

### Scenario 4: Re-run a deployment exactly as it was before

```bash
# Show the exact command that was run before (from audit)
strata audit changes --last 1 --output json | jq '.data.stages[] | .provisioner'

# Re-run with the same deployment file
git checkout deploy-prd.yaml  # if you've made changes
strata deploy run -f deployments/deploy-prd.yaml --force
```

---

## Best Practices

1. **Always dry-run before deploying to production.**

2. **Use `--force` in CI/CD, but never interactively** — you won't see warnings.

3. **Stage your deployments:** dev → staging → production. Never deploy directly to production.

4. **Lock deployments in production** — set `spec.locking.enabled: true` to prevent concurrent runs.

5. **Monitor the audit trail** — integrate `strata audit export` into your SIEM or dashboard for compliance.

6. **Keep stages small and focused** — one provisioner per stage when possible. Easier to debug failures.

7. **Use lifecycle hooks for notifications** — Slack, PagerDuty, etc. — to alert the team during deployments.

8. **Test rollback procedures** — know how to recover before you need to.

---

## See Also

- [How Deployments Work](./how-deployments-work.md) — conceptual model
- [Environment Composition](./environment-composition.md) — layering variables and secrets
- [Deployment Locking](./how-deployment-locking-works.md) — concurrent deployment prevention
- [Troubleshooting](./troubleshooting-what-changed.md) — diagnosing issues
- [Commands Reference — deploy](../platform/commands.md#deploy) — full command documentation
