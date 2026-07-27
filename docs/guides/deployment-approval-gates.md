# Deployment Approval Gates

Deployment gates are decision points in the pipeline that pause the deployment until a condition is met or manually resolved. Use gates to enforce approvals, cost reviews, security checks, scheduled maintenance windows, and post-deployment verification.

---

## Overview

Gates are declared in the **environment configuration** (`spec.gates`) and are evaluated at specific phases during `strata deploy run`:

1. **Pre-plan gates** (`approval`, `scheduled`) — block before Terraform plan
2. **Post-plan gates** (`cost_review`, `security_review`) — block after plan, with cost and CVE data available
3. **Post-apply gates** (`verify`, `cab`, `incident`) — block after infrastructure is deployed

When a gate blocks, `strata deploy run`:

- Creates a **work item** (stored locally or in a backend: S3, Azure Blob, GCS, etc.)
- **Pauses** the deployment
- **Prints** the work item ID and resume command
- **Exits with code 5** (`hand-off required`)

To resume, the operator approves/rejects the work item, then runs `strata deploy run --resume <work-item-id>`.

---

## Declaring Gates

Gates are defined in the environment YAML under `spec.gates`:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: production
spec:
  gates:
    # Human approval for all production deploys (pre-plan)
    - type: approval
      when: always
      approvers: [ops-team]
      timeout_minutes: 60

    # Cost review when monthly delta exceeds $1,000 (post-plan)
    - type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers: [finance]
      timeout_minutes: 240

    # Security team review when critical CVEs found (post-plan)
    - type: security_review
      when:
        cve_critical: ">= 1"
      approvers: [security-team]
      timeout_minutes: 480

    # Only allow deploys during maintenance window (pre-plan, auto-resolves)
    - type: scheduled
      when:
        time_utc: "02:00-04:00"
      auto_resolve: true

    # Manual verification after deploy applied (post-apply)
    - type: verify
      when: always
      timeout_minutes: 30
      description: "Verify application health and smoke tests pass in prod"
```

---

## Gate Types

| Type              | Phase      | Trigger                      | Resolved by            | Use case                           |
| ----------------- | ---------- | ---------------------------- | ---------------------- | ---------------------------------- |
| `approval`        | pre-plan   | always or condition          | Approver group         | Require human sign-off before plan |
| `cost_review`     | post-plan  | cost delta exceeds threshold | Finance / lead         | Cost control and governance        |
| `security_review` | post-plan  | CVE count exceeds threshold  | Security team          | Vulnerability gate                 |
| `scheduled`       | pre-plan   | outside time window          | Clock / auto or manual | Maintenance windows, trading halts |
| `verify`          | post-apply | always or condition          | Operator               | Smoke tests, health checks         |
| `cab`             | pre-plan   | conditional                  | CAB meeting            | Change Advisory Board approval     |
| `incident`        | any        | conditional                  | On-call engineer       | Incident investigation pause       |

---

## Conditions

Gates evaluate conditions at evaluation time. Available conditions:

| Condition            | Phase     | Data source               | Example                            |
| -------------------- | --------- | ------------------------- | ---------------------------------- |
| `cost_delta_monthly` | post-plan | `cost.json` artifact      | `">= 1000"` or `"< 500"`           |
| `cve_critical`       | post-plan | `cve-audit.json` artifact | `">= 1"`                           |
| `cve_high`           | post-plan | `cve-audit.json` artifact | `">= 5"`                           |
| `ai_risk`            | post-plan | `cve-audit.json` artifact | `">= high"` (high, medium, low)    |
| `time_utc`           | pre-plan  | system clock              | `"02:00-04:00"` or `"14:30-15:30"` |

Operators: `>`, `>=`, `<`, `<=`, `==`, `!=`

---

## Workflow: Deploy → Pause → Approve → Resume

### Step 1: Initial Deploy (may pause)

```bash
$ strata deploy run -f deploy/production.yaml

[*] Pre-plan gates: evaluating...
[*]   approval gate: blocked (requires ops-team approval)

⏸️  Deployment paused — work item created:
   ID:   approval/deploy-prd-abc1234d-20260727T1430
   Expires: 2026-07-27 15:30:00 UTC

   Resolve:  strata workitem approve 'approval/deploy-prd-abc1234d-20260727T1430'
   Resume:   strata deploy run -f deploy/production.yaml --resume 'approval/deploy-prd-abc1234d-20260727T1430'

$ echo $?
5
```

Exit code `5` tells CI/CD: "hand-off required — waiting for external decision."

### Step 2: Approve (or Reject)

```bash
# Approve the gate
$ strata workitem approve 'approval/deploy-prd-abc1234d-20260727T1430' \
  --note "Approved by on-call engineer"

✅ Approved: approval/deploy-prd-abc1234d-20260727T1430
   Resolved by: github.com/alice
   Note: Approved by on-call engineer

   Resume deploy with: strata deploy run -f deploy/production.yaml --resume 'approval/deploy-prd-abc1234d-20260727T1430'

# Or reject it
$ strata workitem reject 'approval/deploy-prd-abc1234d-20260727T1430' \
  --reason "Insufficient testing coverage — blocked by 3 failing tests"

❌ Rejected: approval/deploy-prd-abc1234d-20260727T1430
   Resolved by: github.com/bob
   Reason: Insufficient testing coverage — blocked by 3 failing tests
```

### Step 3: Resume (if approved)

```bash
$ strata deploy run -f deploy/production.yaml \
  --resume 'approval/deploy-prd-abc1234d-20260727T1430'

[*] Verifying work item: approval/deploy-prd-abc1234d-20260727T1430
[+] Gate cleared (approved by github.com/alice at 2026-07-27 14:32:00 UTC)

[*] Pre-plan gates: resumed (approval already resolved)
[*] Running Terraform plan...
...
```

The deployment continues from where it paused.

---

## CI/CD Integration

The standard pattern for unattended CI pipelines:

```bash
#!/bin/bash
set -e

# Step 1: Deploy (may pause at gate)
strata deploy run -f deploy/production.yaml
EXIT_CODE=$?

if [ $EXIT_CODE -eq 5 ]; then
    echo "Gate pending — deployment paused (hand-off required)"
    # Trigger an external approval workflow (Slack, PagerDuty, email, etc.)
    # Approver uses strata workitem approve/reject
    # Then CI re-runs this script with --resume flag
    exit 0  # Don't fail the pipeline; wait for human decision
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "Deployment succeeded"
    exit 0
else
    echo "Deployment failed"
    exit 1
fi
```

When the human approves (via `strata workitem approve`), a separate CI job resumes:

```bash
#!/bin/bash
# Called after approval is received
WORK_ITEM_ID="approval/deploy-prd-abc1234d-20260727T1430"
strata deploy run -f deploy/production.yaml --resume "$WORK_ITEM_ID"
```

---

## Backend Storage

Work items are stored in a pluggable backend. Select with `STRATA_WORKITEM_BACKEND`:

```bash
# Local file system (default — good for testing)
strata deploy run -f deploy/production.yaml

# AWS S3 (shared across all CI agents)
STRATA_WORKITEM_BACKEND=s3 strata deploy run -f deploy/production.yaml

# Azure Blob Storage
STRATA_WORKITEM_BACKEND=azblob strata deploy run -f deploy/production.yaml

# GCP Cloud Storage
STRATA_WORKITEM_BACKEND=gcs strata deploy run -f deploy/production.yaml
```

For CI environments, use a shared backend (S3, Blob, GCS) so all CI agents can read/write the same work items.

---

## Security Considerations

### Commit Mismatch Protection

When resuming with `--resume <work-item-id>`, strata verifies that the current commit matches the commit that created the work item. This prevents **replay attacks** where an old approval is used to deploy a different code version.

```bash
# Original deploy
$ git log --oneline -1
abc1234d Add feature X

$ strata deploy run -f deploy/production.yaml
# → creates work item with commit abc1234d

# Two hours later, another commit is pushed
$ git log --oneline -1
def5678e Add feature Y

$ strata deploy run -f deploy/production.yaml --resume approval/...
# ❌ REJECTED: Work item was created for commit abc1234d, but current commit is def5678e
```

### Asserted Identity (`--as`)

Approve or reject **as** a specific identity:

```bash
strata workitem approve <id> --as "automation-service"
# Tagged in audit log as "automation-service [asserted]"
```

Use for automated approvals (e.g., "auto-approve if tests pass"). The `[asserted]` tag marks that the approver was asserted by code, not a direct human action.

### Audit Trail

All work-item events are logged:

```bash
strata audit list --filter "workitem"
```

See `docs/guides/siem-audit-forwarding.md` for SIEM integration (Sentinel, Splunk, ELK).

---

## Examples

### Production Deployment with Triple Gate

```yaml
spec:
  gates:
    # 1. Pre-plan: Human approval
    - type: approval
      when: always
      approvers: [ops-lead]
      timeout_minutes: 30

    # 2. Post-plan: Cost review (if significant delta)
    - type: cost_review
      when:
        cost_delta_monthly: ">= 5000"
      approvers: [cfo, vp-engineering]
      timeout_minutes: 240

    # 3. Post-apply: Smoke test verification
    - type: verify
      when: always
      timeout_minutes: 15
      description: "Run prod smoke tests; confirm no alerts spike"
```

Deployment pauses at each gate:

1. Ops lead approves the change
2. (If cost > $5k) Finance reviews
3. (After apply) Ops runs smoke tests and confirms

### Maintenance Window Only

```yaml
spec:
  gates:
    - type: scheduled
      when:
        time_utc: "22:00-06:00"  # Only between 10 PM and 6 AM UTC
      auto_resolve: true         # Auto-resume outside window? Or block?
```

If `auto_resolve: true`, deployment auto-starts within the window. If `false`, operator must manually approve even during the window.

### Security-Gated Deploys

```yaml
spec:
  gates:
    # Block if any critical CVEs
    - type: security_review
      when:
        cve_critical: ">= 1"
      approvers: [security-team]
      timeout_minutes: 480
      description: "Critical CVE(s) detected — security team review required"

    # Block if AI risk is moderate or higher
    - type: security_review
      when:
        ai_risk: ">= medium"
      approvers: [security-team]
      timeout_minutes: 240
      description: "Elevated AI risk score — security review required"
```

---

## Troubleshooting

**Q: The gate is blocking but I think it shouldn't. Why?**

Check the gate condition:

```bash
strata workitem show <work-item-id>
# See: context → reason, cost_delta_monthly, cve_critical, ai_risk, etc.
```

If the condition is wrong (e.g., evaluates `cost_delta_monthly: ">= hgh"` due to a typo), check the environment YAML.

**Q: I lost the work item ID. How do I find it?**

```bash
strata workitem list --status pending --deployment production
```

Lists all pending items for that deployment.

**Q: Can I approve/reject from the VS Code extension?**

Yes. Open the Pending Work panel (`strata.workItems` view); inline ✅ / ❌ buttons appear on each item.

**Q: What if the work item expires?**

After `timeout_minutes`, the work item auto-expires:

```bash
strata workitem expire
```

Expired items can still be approved/rejected, but the deployment won't resume automatically — you must manually run `strata deploy run --resume <id>`.

---

## See Also

- `docs/help/workitem` — CLI command reference
- `docs/guides/siem-audit-forwarding.md` — audit log and SIEM integration
- `docs/platform/exit-codes.md` — exit code 5 reference
- ADR-0057 — deployment workflow orchestration design
