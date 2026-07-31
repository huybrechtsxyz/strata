# Deployment Gates

Hand-off points where execution pauses for external approval or verification.

Gates are declared under `spec.gates` in the **deployment YAML** (next to
`stages`). A gate creates a work item when triggered, notifies interested
parties, and resumes deployment once resolved. Gates include approval, cost
review, manual verification, and scheduled deployment.

See ADR-0057 for the full orchestration design, and ADR-0059 for the unified
schema (`name`/`mode`/`scope`) that replaced the old, separate `spec.approvals`
metadata block.

---

## Gate Types

| Type              | Trigger                        | Resolver             | Example                                   |
| ----------------- | ------------------------------ | -------------------- | ----------------------------------------- |
| `approval`        | Always (or when condition met) | Human operator       | Require ops team sign-off before deploy   |
| `cost_review`     | Cost delta exceeds threshold   | Finance / team lead  | Require approval if monthly cost +$1000   |
| `security_review` | SBOM audit finds critical CVEs | Security team        | Require risk acceptance before deploy     |
| `verify`          | Post-deploy health checks      | Operator             | Manual confirmation service is healthy    |
| `scheduled`       | Deployment window opens        | Clock (auto-resolve) | Deploy only at 2am UTC maintenance window |

Each gate also has a **mode**:

| Mode      | Behavior                                                                                                                                                                   |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enforce` | (default) strata creates a real work item and pauses the deploy (exit code 5)                                                                                              |
| `declare` | strata only records the gate in the audit trail — never blocks. Use when enforcement already happens externally (Azure DevOps approvals, GitHub Actions protection rules). |

And a **scope** — which stage(s) the gate applies to: a list of stage names,
or `"all"` (default).

---

## Configuration in Deployment YAML

```yaml
# deploy.yaml
spec:
  gates:
    # Human approval required before the production stage
    - name: prod-approval
      type: approval
      mode: enforce
      scope: [production]
      when: always
      approvers:
        ops-team:
          type: github-team
          value: "org/ops-team"
        on-call:
          type: user
          value: "oncall@example.com"
      min_approvals: 1
      timeout_minutes: 60

    # Finance approval if cost rises > $1000/month (applies to all stages)
    - name: cost-guard
      type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers:
        finance:
          type: user
          value: "finance@example.com"

    # Security review if SBOM finds critical CVEs — audit only, external SOC gates it
    - name: sbom-audit
      type: security_review
      mode: declare
      when:
        cve_critical: ">= 1"

    # Scheduled deployment (auto-resolves)
    - name: maintenance-window
      type: scheduled
      when:
        time_utc: "02:00-04:00"    # maintenance window
      auto_resolve: true

  stages:
    - name: staging
    - name: production
```

---

## Common Patterns

### Cost Review with Multiple Thresholds

```yaml
gates:
  # Warn on moderate increases
  - name: cost-warning
    type: cost_review
    mode: declare
    scope: [staging]
    when:
      cost_delta_monthly: ">= 500"

  # Enforce on large increases
  - name: cost-approval-required
    type: cost_review
    mode: enforce
    scope: [production]
    when:
      cost_delta_monthly: ">= 2000"
    approvers:
      finance:
        type: user
        value: "finance@example.com"
    min_approvals: 1
    timeout_minutes: 480  # 8 hours for budget review
```

### Security Review with CVE Conditions

```yaml
gates:
  # High-risk: any critical CVEs found
  - name: cve-critical-block
    type: security_review
    mode: enforce
    when:
      cve_critical: ">= 1"
    approvers:
      security-team:
        type: github-team
        value: "org/security"
    description: "SBOM audit found critical CVEs — security team must approve risk acceptance"

  # Medium-risk: multiple high-severity CVEs
  - name: cve-high-review
    type: security_review
    when:
      cve_high: ">= 3"
    approvers:
      security-lead:
        type: user
        value: "security-lead@example.com"
      compliance:
        type: ado-group
        value: "Compliance-Team"
    min_approvals: 2  # Both must approve
    description: "SBOM audit found multiple high-severity CVEs"

  # Low-risk: audit-only, no blocking
  - name: cve-audit-trail
    type: security_review
    mode: declare
    when:
      cve_high: ">= 1"
    description: "Record SBOM findings for compliance"
```

### Scheduled Deployments

```yaml
gates:
  # Production deploys only during maintenance windows
  - name: maintenance-window
    type: scheduled
    scope: [production]
    when:
      time_utc: "02:00-04:00"  # Tuesday-Saturday 2am-4am UTC
    auto_resolve: true         # Automatically proceed when window opens
    description: "Deployments blocked until maintenance window opens"

  # Require manual approval outside business hours
  - name: weekend-override
    type: approval
    scope: [production]
    when:
      time_utc: "16:00-08:00"  # 4pm Friday to 8am Monday (wraps week boundary)
    approvers:
      on-call:
        type: user
        value: "oncall@example.com"
    min_approvals: 1
    timeout_minutes: 120
    description: "Weekend deployment — on-call must approve"

  # Multi-condition: only block during slow periods AND cost increase
  - name: off-hours-cost-gate
    type: cost_review
    when:
      # Both conditions must be true
      time_utc: "20:00-08:00"           # Outside business hours
      cost_delta_monthly: ">= 500"      # AND non-trivial cost
    approvers:
      ops-lead:
        type: user
        value: "ops-lead@example.com"
    description: "High cost change outside business hours — ops lead approval required"
```

### AI Risk Gates

```yaml
gates:
  # Block if AI risk detected
  - name: ai-risk-review
    type: security_review
    when:
      ai_risk: ">= high"
    approvers:
      ai-governance:
        type: github-team
        value: "org/ai-governance"
      legal:
        type: user
        value: "legal@example.com"
    min_approvals: 2
    description: "AI risk mitigation identified — governance and legal review required"
```

---

## Workflow

1. **Trigger**: Deployment reaches a gate condition
2. **Create**: Work item generated with unique ID
3. **Notify**: SIEM event sent (Sentinel, Splunk, ELK); alerts fire
4. **Pause**: Exit code 5 (hand-off required); CI picks this up
5. **Resolve**: External actor approves/rejects via `strata workitem approve <id>`
6. **Resume**: `strata deploy run ... --resume <id>` proceeds to apply

---

## CLI Commands

```
strata workitem list [--type TYPE] [--status STATUS]
strata workitem show <id>
strata workitem approve <id> [--note TEXT]
strata workitem reject <id> [--reason TEXT]
strata deploy run -f deploy.yaml --resume <id>
```

---

## Discovery

- `strata help --topic audit` — SIEM notifications and audit trail

---

## Troubleshooting

### Gate Timeout Issues

**Symptom:** `strata deploy run` exits with code 5, gate shows `timeout_minutes` warning, deployment is stuck.

**Diagnosis:**
```bash
# Check the work item
strata workitem show <id> --verbose

# Output shows: "Status: timeout_waiting"
```

**Solutions:**

1. **Timeout is too short for the review process**
   - Increase `timeout_minutes` on the gate (e.g., 60 → 120)
   - Coordinate with approvers to speed up turnaround
   ```yaml
   - name: prod-approval
     type: approval
     timeout_minutes: 240  # 4 hours instead of 1
   ```

2. **Approver never received notification**
   - Verify SIEM/audit forwarding is working: `strata tools status | grep siem`
   - Check approver's email/Slack is correctly configured
   - Manually notify approvers: `strata workitem show <id> --output json | jq .approvers`

3. **Approver cannot be reached (out of office, etc.)**
   - Designate a backup: add a second approver with `min_approvals: 1`
   ```yaml
   approvers:
     primary-lead:
       type: user
       value: "primary@example.com"
     backup-lead:
       type: user
       value: "backup@example.com"
   min_approvals: 1  # Either one can approve
   ```

4. **Gate condition was never actually met**
   - Run with `--verbose` to see gate evaluation: `strata deploy run -f deploy.yaml --verbose`
   - Check that cost/CVE/time conditions are being computed correctly
   - Gates with `mode: declare` never block (audit-only)

---

### Approver Format Issues

**Symptom:** `strata deploy run` fails validation: `Invalid approver format` or gate is not created.

**Diagnosis:**
```bash
# Validate the deployment file
strata validate -f deployments/deploy-prd.yaml --deep
```

**Common mistakes & fixes:**

| Problem                           | Wrong                                 | Correct                                                             |
| --------------------------------- | ------------------------------------- | ------------------------------------------------------------------- |
| **github-team missing org/repo**  | `value: "platform-team"`              | `value: "org/platform-team"`                                        |
| **ADO group not fully qualified** | `value: "Approvers"`                  | `value: "domain/Approvers"` or `value: "Project\\Approvers"`        |
| **User email typo**               | `value: "user@exampl.com"`            | `value: "user@example.com"`                                         |
| **Mixed-case type**               | `type: "GitHub-Team"`                 | `type: github-team"` (lowercase)                                    |
| **Missing approvers dict**        | `approvers: ["user1", "user2"]`       | `approvers: {key: {type: user, value: ...}}`                        |
| **Approver ID conflicts**         | `approvers: {ops: {...}, ops: {...}}` | `approvers: {ops_primary: {...}, ops_backup: {...}}` (unique names) |

**How to check your approver format:**

```bash
# Export the deployment manifest (resolves all refs)
strata manifest show -f deployments/deploy-prd.yaml --output json | jq '.spec.gates[0].approvers'

# Output should look like:
{
  "ops-team": {
    "type": "github-team",
    "value": "org/ops-team"
  },
  "oncall": {
    "type": "user",
    "value": "oncall@example.com"
  }
}
```

**Provider-specific validation:**

- **GitHub teams:** Test with `gh api orgs/ORG/teams/TEAM` — should return 200, not 404
- **Azure AD groups:** Test with `az ad group show --group "group-name"` — must exist in tenant
- **User emails:** No special validation — delivery happens at gate creation time

---

### Gate Not Triggering When Expected

**Symptom:** Gate is configured but never creates a work item; deployment proceeds without pausing.

**Causes:**

1. **Condition was not met**
   ```bash
   # Deploy with verbose to see gate evaluation
   strata deploy run -f deploy.yaml --verbose 2>&1 | grep "gate.*eval"
   ```
   - `cost_delta_monthly: ">= 1000"` — cost delta must be >= $1000 (not >)
   - `cve_critical: ">= 1"` — at least 1 critical CVE must be found
   - `time_utc: "02:00-04:00"` — deployment must start during this window

2. **Gate scope doesn't match current stage**
   ```yaml
   gates:
     - name: prod-only
       scope: [production]  # Only applies to production stage
   
   stages:
     - name: staging       # ← This stage is NOT in scope
     - name: production    # ← This stage IS in scope
   ```
   - Verify `scope` includes the stage being deployed

3. **Mode is `declare`, not `enforce`**
   ```yaml
   - name: audit-gate
     mode: declare  # ← This never blocks, only records
   ```
   - Change to `mode: enforce` to actually pause deployment

4. **`when: always` is missing or wrong**
   ```yaml
   - name: always-approve
     type: approval
     # Missing: when: always
     # Default when: is "always" if omitted, but be explicit
     when: always
   ```

---

## Discovery

- `strata help --topic audit` — SIEM notifications and audit trail
