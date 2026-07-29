# Audit Command Group — Deployment History and Compliance

> **Audience:** DevOps engineers, security teams, and compliance officers who need to track
> deployment changes, generate compliance reports, and troubleshoot what changed when.
>
> 📐 **Design rationale:** [ADR-0018 — Deployment audit and traceability for compliance](../decisions/0018-deployment-audit-traceability.md)

The `strata audit` command group provides a complete audit trail of all deployments, changes,
and infrastructure updates.

---

## What is an Audit Trail?

An **audit trail** is a record of all deployment and infrastructure changes. Each time you run
a deployment, a new audit record is created with:

- Who deployed (user/CI/CD service account)
- What changed (stages executed, resources modified)
- When it happened (timestamp)
- Why it happened (commit SHA, deployment ID, user message)
- Success or failure status
- Complete deployment manifest (for rollback/reference)

Audit trails are stored locally and can be exported to external systems (Splunk, ELK, Azure
Sentinel, etc.) for compliance and monitoring.

---

## audit changes — List Recent Deployments

Show a chronological list of recent deployments and their changes.

```bash
strata audit changes -f deployments/deploy-prd.yaml
```

Output:
```
Deployment Changes: xyz-deploy-prd
──────────────────────────────────

2026-07-21 10:45:32  ✅  Deploy v2.3.0 to production
  User:         sarah@example.com (via GitHub Actions)
  Changes:
    • Stage: infrastructure    COMPLETED    45 resources modified
    • Stage: configuration     COMPLETED    12 files updated
    • Stage: health-check      COMPLETED    all endpoints responding

2026-07-20 16:22:15  ✅  Hotfix: Update API service
  User:         devops-ci
  Changes:
    • Service: api-service (image: v2.2.5 → v2.3.0)
    • Health check: passed

2026-07-18 08:10:42  ⚠️   Partial deployment (1 stage failed)
  User:         alex@example.com
  Changes:
    • Stage: infrastructure    COMPLETED    31 resources created
    • Stage: configuration     FAILED       database migration timeout
  Error:     Database migration exceeded time limit (300s)
  Recovery:  Manually ran migration, re-ran configuration stage

2026-07-15 14:55:03  ✅  Full deployment to staging
  User:         ci-system
  Changes:
    • Stage: infrastructure    COMPLETED    31 resources created
    • Stage: configuration     COMPLETED    12 files deployed
    • Stage: health-check      COMPLETED    all endpoints responding
```

### Filter by time range

```bash
# Last 7 days
strata audit changes -f deploy-prd.yaml --since 7d

# Specific date range
strata audit changes -f deploy-prd.yaml --since 2026-07-01 --until 2026-07-21

# Last 10 deployments
strata audit changes -f deploy-prd.yaml --limit 10
```

### Machine-readable output

```bash
strata audit changes -f deploy-prd.yaml --output json
```

Returns:
```json
{
  "deployment": "xyz-deploy-prd",
  "changes": [
    {
      "timestamp": "2026-07-21T10:45:32Z",
      "user": "sarah@example.com",
      "status": "success",
      "stages": [
        {"name": "infrastructure", "status": "completed", "changes": 45},
        {"name": "configuration", "status": "completed", "changes": 12}
      ],
      "commit_sha": "abc123def456"
    }
  ]
}
```

### Use cases

- Compliance: show who deployed what and when
- Troubleshooting: find when a change was introduced
- Incident response: determine what changed before an outage
- Release notes: see all changes between releases

---

## audit diff — Compare Two Deployments

Show what changed between two specific deployments.

```bash
# What changed between last two deployments?
strata audit diff -f deployments/deploy-prd.yaml --since 1

# Compare specific deployments by timestamp
strata audit diff -f deploy-prd.yaml --deployment-id abc123def456 --previous
```

Output:
```
Infrastructure Changes: xyz-deploy-prd
───────────────────────────────────────

Deployment A (2026-07-20 16:22:15)  →  Deployment B (2026-07-21 10:45:32)

Terraform State Diff:
  Resources added:     2
  Resources modified:  12
  Resources deleted:   0

Detailed changes:
  + azurerm_container_registry (myrepo-prod) — new registry
  ~ azurerm_kubernetes_cluster.aks
      - enable_auto_scaling: false → true
      - min_count: 1 → 2
      - max_count: 5 → 10
  ~ azurerm_container_registry_webhook
      - webhook_url: https://old-endpoint → https://new-endpoint

Configuration Changes:
  Modified files:
    • kubernetes/deployments/api.yaml
      - replicas: 3 → 4
      - image: myrepo/api:v2.2.5 → myrepo/api:v2.3.0
    • ansible/playbooks/security-hardening.yml
      - new rules: 3 added

Cost Impact (estimated):
  Infrastructure: -$120/month (scale optimization)
  Total monthly cost: $4,200 → $4,080
```

### Compare to production

```bash
strata audit diff -f deploy-prd.yaml \
  --deployment-id <staging-deployment> \
  --compare-to <prod-deployment>
```

Shows differences between staging and production deployments (useful for promotion gates).

### Machine-readable output

```bash
strata audit diff -f deploy-prd.yaml --output json --since 1 --previous
```

Returns detailed diff in JSON format for automation.

### Use cases

- Change management: document what changed between versions
- Troubleshooting: identify the exact change that caused an issue
- Promotion gates: verify staging changes before promoting to production
- Cost tracking: see infrastructure changes and their cost impact

---

## audit export — Generate Compliance Reports

Export audit trail to external compliance systems (SIEM, audit logs, etc.).

### Export to file

```bash
# Export last 30 days to a file
strata audit export -f deployments/deploy-prd.yaml \
  --since 30d \
  --format json \
  --output-file audit-export-2026-07-21.json
```

### Export to SIEM (Splunk, ELK, Azure Sentinel)

**Splunk:**
```bash
strata audit export -f deploy-prd.yaml \
  --sink splunk \
  --splunk-hec-url https://splunk.example.com:8088 \
  --splunk-hec-token <token>
```

**Azure Sentinel:**
```bash
strata audit export -f deploy-prd.yaml \
  --sink azure-sentinel \
  --workspace-id <workspace-id> \
  --shared-key <workspace-key> \
  --log-type StratAudit
```

**ELK Stack:**
```bash
strata audit export -f deploy-prd.yaml \
  --sink elasticsearch \
  --elasticsearch-url https://elasticsearch.example.com:9200 \
  --elasticsearch-index strata-audit
```

### Machine-readable output

```bash
strata audit export -f deploy-prd.yaml --output json
```

Returns audit events in JSON format for custom integrations.

### Use cases

- **SOC2 / ISO 27001 compliance:** Export audit trail for compliance audits
- **SIEM integration:** Send all strata deployment events to your security system
- **Incident response:** Quickly find all changes before/after an incident
- **Regulatory reporting:** Generate deployment history for auditors

---

## audit resend — Retry Failed Exports

If an export to a SIEM failed, resend the events without redeploying.

```bash
# Resend last export attempt
strata audit resend -f deployments/deploy-prd.yaml --sink splunk

# Resend specific date range
strata audit resend -f deploy-prd.yaml \
  --sink azure-sentinel \
  --since 2026-07-20 \
  --until 2026-07-21

# Resend with verbose output (see what's being sent)
strata audit resend -f deploy-prd.yaml --sink splunk --verbose
```

Output:
```
Resending audit events to Splunk
─────────────────────────────────

Events to resend: 12
Time range: 2026-07-20 to 2026-07-21

Sending...
  ✅ Event 1: 2026-07-20 16:22:15 deployment created
  ✅ Event 2: 2026-07-20 16:22:45 stage infrastructure completed
  ✅ Event 3: 2026-07-20 16:55:30 stage configuration completed
  ...

✅ All 12 events sent successfully to Splunk
```

### Use cases

- Retry failed SIEM exports (network issues, auth failures)
- Ensure compliance events are delivered even if external system was down
- Re-export after changing SIEM credentials or endpoint

---

## Common Workflows

### Workflow 1: Compliance audit preparation

Prepare a report of all deployments for a compliance audit (SOC2, ISO 27001, etc.).

```bash
# Export all deployments from the past 12 months
strata audit export -f deployments/deploy-prd.yaml \
  --since 365d \
  --format json \
  --output-file compliance-report-2026.json

# Generate summary
jq -r '.events[] | "\(.timestamp) | \(.user) | \(.action) | \(.status)"' \
  compliance-report-2026.json | \
  sort > deployment-summary-2026.csv

# Manual review and sign-off
echo "Reviewed by: [signature]" >> deployment-summary-2026.csv
```

### Workflow 2: Troubleshoot "what changed?"

A service is failing. Find out what changed recently.

```bash
# See recent deployments
strata audit changes -f deploy-prd.yaml --limit 5

# Compare last two deployments
strata audit diff -f deploy-prd.yaml --since 1 --previous

# Export details for deep analysis
strata audit export -f deploy-prd.yaml --output-file audit-details.json
jq '.events[] | select(.status != "success")' audit-details.json
```

### Workflow 3: Enable continuous audit logging to Splunk

Set up automatic export of all deployments to Splunk (in your CI/CD pipeline).

```yaml
# azure-pipelines.yml
trigger:
  - main

jobs:
  - job: Deploy
    steps:
      - script: strata deploy run -f deployments/deploy-prd.yaml --force
        displayName: Deploy to Production

      - script: |
          strata audit export -f deployments/deploy-prd.yaml \
            --sink splunk \
            --splunk-hec-url $(SPLUNK_HEC_URL) \
            --splunk-hec-token $(SPLUNK_HEC_TOKEN) \
            --since 10m
        displayName: Export audit trail to Splunk
        condition: succeeded()

      - script: |
          # If export failed, alert on-call engineer
          strata audit resend -f deployments/deploy-prd.yaml --sink splunk
        displayName: Retry export if failed
        condition: failed()
```

### Workflow 4: Incident response — find root cause

An incident occurred at 2026-07-21 14:30. What changed?

```bash
# Find deployments around that time
strata audit changes -f deploy-prd.yaml \
  --since 2026-07-21T12:00:00Z \
  --until 2026-07-21T16:00:00Z

# Export detailed events
strata audit export -f deploy-prd.yaml \
  --since 2026-07-21T12:00:00Z \
  --until 2026-07-21T16:00:00Z \
  --output-file incident-analysis.json

# Analyze
jq -r '.events[] | select(.timestamp > "2026-07-21T14:00:00Z") | 
  "\(.timestamp) | User: \(.user) | Action: \(.action) | Status: \(.status)"' \
  incident-analysis.json
```

### Workflow 5: Cost tracking and optimization

Monitor infrastructure changes and their cost impact.

```bash
# Weekly cost report
strata audit diff -f deploy-prd.yaml \
  --since 7d \
  --output json | \
  jq '.cost_impact'

# Export to spreadsheet
strata audit export -f deploy-prd.yaml \
  --since 30d \
  --format csv \
  --output-file monthly-deployments.csv
```

---

## Audit Trail Storage

### Where are audit records stored?

Audit records are stored in `.strata/audit/`:

```
.strata/
  audit/
    deployments/
      abc123def456.json       # one file per deployment
      xyz789abc123.json
      ...
    exports/
      2026-07-21-splunk.log   # export attempt logs
```

### Accessing audit records locally

```bash
# Read a deployment record
cat .strata/audit/deployments/abc123def456.json | jq .

# Count total deployments
ls .strata/audit/deployments | wc -l
```

### Backup and retention

**Backup audit trail:**
```bash
# Daily backup to Azure Blob Storage
az storage blob upload \
  --account-name myaccountname \
  --container-name audit-backups \
  --name audit-$(date +%Y%m%d).tar.gz \
  --file <(tar -czf - .strata/audit/)
```

**Retention policy (optional):**

Keep audit records for compliance duration (typically 7 years for financial, 3 years for others).
Records older than retention period can be archived to cold storage.

---

## Troubleshooting

### "No audit records found"

```
Error: No audit records for deployment: xyz-deploy-prd
```

**Check:**
1. Deployment hasn't been run yet: `strata deploy run` creates the first audit record
2. Audit records were deleted or moved: verify `.strata/audit/` exists

### "Export to Splunk failed"

```
Error: Failed to send events to Splunk HEC endpoint
  Reason: Connection refused (https://splunk.example.com:8088)
```

**Troubleshoot:**
1. Verify Splunk endpoint is accessible: `curl -k https://splunk.example.com:8088/`
2. Verify HEC token is valid: check Splunk > Settings > Data Inputs > HTTP Event Collector
3. Verify network connectivity and firewall rules
4. Retry with `strata audit resend`

### "Export to Azure Sentinel failed"

```
Error: Failed to authenticate with Azure Sentinel
  Reason: Unauthorized (401)
```

**Check:**
1. Workspace ID is correct: `az monitor log-analytics workspace list`
2. Shared key is correct and not expired
3. Verify permissions: Log Analytics > Access Control (IAM)

---

## Best Practices

1. **Enable audit export automatically:** Send all deployments to your SIEM (set in CI/CD)
2. **Monitor export failures:** Alert if exports fail for >24 hours (compliance risk)
3. **Archive old records:** Move audit records >1 year to cold storage (cost optimization)
4. **Review deployments regularly:** Weekly review of `strata audit changes` in team meetings
5. **Use audit trail for incident response:** Audit records are first place to look in incidents
6. **Retain audit logs long-term:** Required for compliance (SOC2: 1 year, ISO 27001: 3 years)
7. **Include deployment context:** Add deployment reason / ticket number for traceability
8. **Test audit export regularly:** Verify SIEM exports are working before audit time
9. **Automate compliance reports:** Scheduled weekly/monthly exports instead of manual pulls
10. **Document all changes:** Include why in deployment messages for better auditing

---

## See Also

- [Commands Reference — audit](../platform/commands.md#audit) — full command documentation
- [Deploying Infrastructure](./deploying.md) — deployment lifecycle
- [Compliance and Security](../guides/compliance.md) — audit trail security
