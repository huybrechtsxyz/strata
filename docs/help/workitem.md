# Work Item Commands

Manage deployment workflow hand-off gates and approvals.

Work items are created automatically by `strata deploy run` when a deployment
reaches a gate declared in the deployment YAML (`spec.gates`). They represent
a decision point that pauses the pipeline until resolved by an operator, a
CI/CD approval step, or the clock.

---

## CLI Commands

```bash
strata workitem list [--status STATUS] [--type TYPE] [--deployment FILE]
strata workitem show <id>
strata workitem approve <id> [--note TEXT] [--as IDENTITY]
strata workitem reject <id> [--reason TEXT] [--as IDENTITY]
strata workitem complete <id> [--comment TEXT]
strata workitem cancel <id> [--reason TEXT]
strata workitem expire
```

---

## Gate Configuration

Gates are declared in the **deployment YAML** (`spec.gates`, next to `stages`):

```yaml
spec:
  gates:
    # Human approval required for all production deploys
    - name: prod-approval
      type: approval
      when: always
      approvers:
        ops-team:
          type: github-team
          value: "org/ops-team"
      timeout_minutes: 60

    # Finance approval when monthly cost rises by more than $1,000
    - name: cost-guard
      type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers:
        finance:
          type: user
          value: "finance@example.com"
      timeout_minutes: 240

    # Security team review when SBOM audit finds critical CVEs
    - name: sbom-audit
      type: security_review
      when:
        cve_critical: ">= 1"
      approvers:
        security-team:
          type: github-team
          value: "org/security-team"
      timeout_minutes: 480

    # Only deploy inside the maintenance window
    - name: maintenance-window
      type: scheduled
      when:
        time_utc: "02:00-04:00"
      auto_resolve: true

    # Manual verification after deploy
    - name: post-deploy-check
      type: verify
      when: always
      timeout_minutes: 30
```

Each gate also has a **mode: declare | enforce** (default `enforce`) and a
**scope** (stage name(s), or `"all"` — default). See `strata help --topic gates`
for the full field reference.

---

## Gate Types

| Type              | Triggers                                                   | Resolved by         |
| ----------------- | ---------------------------------------------------------- | ------------------- |
| `approval`        | Pre-deploy (always or on condition)                        | Human operator      |
| `cost_review`     | After plan, when cost delta exceeds threshold              | Finance / team lead |
| `security_review` | After plan, when CVE count exceeds threshold               | Security team       |
| `scheduled`       | When deploy is requested outside maintenance window        | Clock / CI retry    |
| `verify`          | After apply, awaiting human confirmation                   | Operator            |
| `cab`             | Production change requiring Change Advisory Board sign-off | CAB meeting         |
| `incident`        | Deploy failed, needs investigation                         | On-call engineer    |

---

## Deploy + Resume Workflow

When a gate triggers, `strata deploy run` prints a work item ID and exits with
**code 5** (hand-off required):

```
⏸️  Deployment paused — gate work item created:
   ID:   approval/deploy-prd-abc1234d-20260727T1430
   Type: approval
   Expires: 2026-07-27 15:30:00 UTC

   Resolve:  strata workitem approve 'approval/deploy-prd-abc1234d-20260727T1430'
   Resume:   strata deploy run -f deploy/deploy-prd.yaml --resume 'approval/...'
```

CI/CD pipeline pattern:

```bash
# Step 1: Deploy (may pause at gate — exit code 5)
strata deploy run -f deploy/deploy-prd.yaml
if [ $? -eq 5 ]; then
    echo "Gate pending — waiting for approval..."
    # Trigger external approval step, then re-run with --resume
fi

# Step 2: Resume after approval
strata deploy run -f deploy/deploy-prd.yaml --resume 'approval/...'
```

---

## Backend Storage

Work items are stored via a pluggable backend. Select with the
`STRATA_WORKITEM_BACKEND` environment variable:

| Backend           | Storage                                               | When to use                              |
| ----------------- | ----------------------------------------------------- | ---------------------------------------- |
| `local` (default) | `.strata/workitems/*.json`                            | Solo operator, dev, CI on single machine |
| `git_tag`         | Annotated git tags                                    | Small teams, distributed, tamper-evident |
| `s3`              | AWS S3                                                | AWS-hosted workspaces                    |
| `azblob`          | Azure Blob Storage                                    | Azure-hosted workspaces                  |
| `gcs`             | GCP Cloud Storage                                     | GCP-hosted workspaces                    |
| `cloud_native`    | AWS CodePipeline / Azure Pipelines / GCP Cloud Deploy | Cloud-managed pipelines                  |

```bash
STRATA_WORKITEM_BACKEND=s3 strata workitem list
```

---

## Exit Codes

| Code | Meaning                                                  |
| ---- | -------------------------------------------------------- |
| 0    | Success                                                  |
| 1    | System error                                             |
| 5    | Hand-off required — work item created, deployment paused |

---

## See Also

- `gates` — gate condition syntax reference
- `environments` — how gates are declared in environment YAML
- `deployment` — deployment lifecycle
- `audit` — work-item events in the audit trail
