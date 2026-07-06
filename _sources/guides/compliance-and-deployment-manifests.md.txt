# Deployment Manifests for Compliance & Audit

Guide to using deployment manifests for regulatory compliance, audit evidence, and governance workflows.

---

## Compliance Frameworks

### NIS2 Directive (EU)

**Requirement:** Immutable records of infrastructure changes for incident response and audits.

**How manifests help:**

1. **Change control** — `artifacts.repositories[].commit` proves exact source code version deployed
2. **Authorization trail** — `user` field shows who deployed
3. **Timestamp evidence** — `timestamp` field is immutable in manifest
4. **Artifact snapshots** — `artifacts.platform.content` is complete configuration record

**Example query:**

```bash
# What was deployed by whom and when?
strata manifest list --deployment prod --output json | \
  jq '.[] | select(.action == "deploy") | {timestamp, user, repositories: .artifacts.repositories}'
```

### ISAE 3402 Type 2 (SOC 2)

**Requirement:** Evidence of controls over infrastructure changes, access, and monitoring.

**How manifests help:**

1. **Control evidence** — Policy results in build manifest prove pre-deploy review
2. **Change log** — Deploy manifest shows what actually happened vs. what was intended
3. **User accountability** — `user` field for audit trail
4. **Configuration snapshots** — `artifacts.platform.content` for forensic reconstruction

**Example workflow:**

```bash
# Gather evidence for auditor
strata manifest export \
  --output-dir ./soc2-evidence-2024 \
  --include-sbom \
  --include-platform

# Auditor reviews manifests to verify:
# - Each deployment had policy approval (build manifest)
# - Deployments succeeded (deploy manifest status)
# - Configuration was reviewed (platform.json snapshot)
# - Supply chain was scanned (SBOM)
```

### PCI DSS (Payment Card Industry)

**Requirement:** Immutable change logs, access controls, and vulnerability scanning.

**How manifests help:**

1. **Change control** — Build + deploy manifests create audit trail
2. **Segregation of duties** — Separate builders (dev) from deployers (ops)
3. **Vulnerability scanning** — SBOM referenced in manifest
4. **Access logging** — `user` field captured for all changes

**Example compliance check:**

```bash
# Ensure all deployments used SBOM scanning
strata manifest list --deployment prod --output json | \
  jq '.[] | select(.action == "deploy") | {timestamp, user, sbom: .artifacts.sbom}'

# Verify no manual changes bypassed scanning
# (Expectation: all manifests have SBOM reference)
```

---

## Evidence Collection

### Build-Time Evidence

**Build manifests capture:**

| Evidence | Field | Use Case |
|----------|-------|----------|
| Who initiated build | `user` | Authorization trail |
| When build ran | `timestamp` | Timeline of changes |
| Source code version | `artifacts.repositories[].commit` | Change control |
| Build configuration | `artifacts.platform.content` | Configuration audit |
| Policy review | `policy_results` | Approval evidence |
| Supply chain snapshot | `artifacts.sbom` | Vulnerability evidence |

**Access:** `.strata/build/{deployment}/manifest.json` (available immediately after `strata build run`)

### Deploy-Time Evidence

**Deploy manifests extend build evidence with:**

| Evidence | Field | Use Case |
|----------|-------|----------|
| Deployment status | `status` | Execution proof |
| Stage results | `stages[].status` | Detailed outcome |
| Infrastructure state | `stages[].outputs` | Resource tracking |
| Stage durations | `stages[].duration_seconds` | Performance audit |
| Provisioner details | `artifacts.providers` | Infrastructure-as-code proof |

**Access:** `.strata/deployments/{deployment}/{version}/{timestamp}.json`

---

## Audit Workflows

### Internal Audit: "What Changed?"

**Question:** What infrastructure changes happened in the last 30 days?

**Answer:**

```bash
# Export all manifests from the past 30 days
strata manifest list --output json | \
  jq --arg cutoff "$(date -d '30 days ago' --iso-8601)" \
  '.[] | select(.timestamp > $cutoff)' | \
  jq 'group_by(.deployment) | .[] | {
    deployment: .[0].deployment,
    total_changes: length,
    builds: [.[] | select(.action == "build")] | length,
    deploys: [.[] | select(.action == "deploy")] | length
  }'

# Result:
# {
#   "deployment": "prod",
#   "total_changes": 12,
#   "builds": 7,
#   "deploys": 5
# }
```

### External Audit: "Prove Compliance"

**Question:** Provide evidence that infrastructure deployments were reviewed and approved.

**Answer:**

```bash
# 1. Collect manifests
strata manifest export \
  --output-dir ./audit-evidence \
  --include-sbom \
  --include-platform

# 2. Generate audit report
cat > audit_report.md << EOF
# Infrastructure Audit Report

## Evidence Collection
- Exported: $(date)
- Deployment: production
- Coverage: Last 90 days

## Build Manifests (Pre-Deploy Reviews)
EOF

# 3. For each build manifest, verify policy approval
strata manifest list --deployment prod --output json | \
  jq '.[] | select(.action == "build") | 
  "Build \(.timestamp): \(.user) - Policy Status: \(.policy_results.status)"' \
  >> audit_report.md

# 4. For each deploy manifest, verify execution success
strata manifest list --deployment prod --output json | \
  jq '.[] | select(.action == "deploy") | 
  "Deploy \(.timestamp): \(.user) - Status: \(.status)"' \
  >> audit_report.md

# 5. Package for auditor
zip -r audit-evidence-$(date +%Y%m%d).zip \
  audit-evidence/ \
  audit_report.md

# Send to auditor
```

### Post-Incident: "What Exactly Happened?"

**Question:** A production outage occurred at 10:45 AM on June 17. What was deployed?

**Answer:**

```bash
# Find deployments around that time
strata manifest list --deployment prod --output json | \
  jq '.[] | select(.timestamp > "2024-06-17T10:00:00Z" and .timestamp < "2024-06-17T11:00:00Z") | {
    timestamp,
    action,
    user,
    status
  }'

# Result:
# {
#   "timestamp": "2024-06-17T10:35:20Z",
#   "action": "build",
#   "user": "alice@acme.com"
# }
# {
#   "timestamp": "2024-06-17T10:45:33Z",
#   "action": "deploy",
#   "user": "bob@acme.com",
#   "status": "success"
# }

# Get full deploy manifest
strata manifest show .strata/deployments/prod/v2.3.0/2024-06-17T10:45:33Z.json

# Extract what changed
strata manifest show .strata/deployments/prod/v2.3.0/2024-06-17T10:45:33Z.json --output json | \
  jq '.spec.artifacts.repositories'

# Compare to previous deployment
# (look for changes in commits, configurations, or stage failures)
```

---

## Policy & Governance

### Build-Time Policy Enforcement

**Gate deployments based on build manifest policy results:**

```yaml
# config/policies/pre-deploy-gate.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: policy
meta:
  name: pre_deploy_gate
spec:
  rule: |
    # Only deploy if:
    # 1. Build manifest exists
    # 2. Policy results show "passed"
    # 3. Build ran within last 24 hours
    
    build_manifest=$(ls -t .strata/build/prod/manifest.json | head -1)
    policy_status=$(jq '.spec.policy_results.status' $build_manifest)
    build_age=$(jq '.spec.timestamp' $build_manifest)
    
    if [[ "$policy_status" != "passed" ]]; then
      echo "ERROR: Build did not pass policy review"
      exit 1
    fi
    
    echo "OK: Build approved for deployment"
    exit 0
```

**Invoke before deployment:**

```bash
# Build
strata build run -f deploy/prod.yaml

# Check policy gate
strata policy apply config/policies/pre-deploy-gate.yaml

# Deploy only if policy passed
if [ $? -eq 0 ]; then
  strata deploy run -f deploy/prod.yaml
fi
```

### Change Windows

**Use manifests to enforce change windows:**

```bash
#!/bin/bash
# Ensure deploys only happen during maintenance windows

manifest=$(strata manifest list --deployment prod --last 1 --output json | jq -r '.[0].path')
deploy_time=$(strata manifest show $manifest --output json | jq -r '.spec.timestamp')

hour=$(date -d "$deploy_time" +%H)

if [ "$hour" -lt 22 ] || [ "$hour" -ge 6 ]; then
  echo "ERROR: Deployment outside maintenance window (10 PM - 6 AM)"
  strata deploy destroy -f deploy.yaml --force
  exit 1
fi

echo "OK: Deployment within maintenance window"
```

### Approval Workflows

**Require explicit approval before deploying:**

```bash
#!/bin/bash
# Workflow: Build → Review → Approve → Deploy

deployment=$1

# Step 1: Build
echo "Building..."
strata build run -f deploy/$deployment.yaml

# Step 2: Show build manifest for review
echo "Review the build manifest:"
strata manifest show .strata/build/$deployment/manifest.json

# Step 3: Require approval
read -p "Approve deployment (y/n)? " approve
if [ "$approve" != "y" ]; then
  echo "Deployment cancelled"
  exit 1
fi

# Step 4: Deploy
echo "Deploying..."
strata deploy run -f deploy/$deployment.yaml

# Step 5: Report
echo "Deployment complete. Manifest:"
strata manifest list --deployment $deployment --last 1
```

---

## Regulatory Reporting

### Quarterly Audit Report

**Generate compliance report from manifests:**

```bash
#!/bin/bash
# Generate quarterly compliance report

quarter=$1  # "Q2" or similar
year=$(date +%Y)

report="audit-report-$year-$quarter.md"

cat > $report << EOF
# Compliance Audit Report — $quarter $year

## Executive Summary

This report provides evidence of infrastructure change control,
access logging, and vulnerability scanning for the $quarter $year period.

---

## Infrastructure Changes
EOF

# Count by deployment
strata manifest list --output json | \
  jq --arg cutoff_start "$(date -d "90 days ago" --iso-8601)" \
     --arg cutoff_end "$(date --iso-8601)" \
  '[.[] | select(.timestamp > $cutoff_start and .timestamp < $cutoff_end)] | 
   group_by(.deployment) | 
   .[] | 
   {deployment: .[0].deployment, builds: [.[] | select(.action=="build")] | length, deploys: [.[] | select(.action=="deploy")] | length}' | \
  jq -r '.[] | "- \(.deployment): \(.builds) builds, \(.deploys) deployments"' >> $report

cat >> $report << 'EOF'

---

## Deployment History

| Timestamp | Deployment | Action | Status | User |
|-----------|-----------|--------|--------|------|
EOF

# List all recent manifests
strata manifest list --output json | \
  jq --arg cutoff "$(date -d '90 days ago' --iso-8601)" \
  '.[] | select(.timestamp > $cutoff) | "| \(.timestamp) | \(.deployment) | \(.action) | \(.status) | \(.user) |"' \
  >> $report

cat >> $report << 'EOF'

---

## Vulnerability Scanning

All deployments included SBOM (Software Bill of Materials) for supply chain visibility.

Scanning tools used:
- Trivy (image scanning)
- Safety (Python dependencies)
- npm audit (Node.js dependencies)

---

## Access Control

All changes require:
- ✅ User authentication (captured in manifest `user` field)
- ✅ Build-time approval (manifest `policy_results`)
- ✅ Change tracking (manifest `timestamp`)

---

## Recommendations

1. Review build policy results for any failures
2. Monitor SBOM scan results for critical vulnerabilities
3. Implement automated policy gates (see ADR 0021)
4. Archive manifests to long-term storage per retention policy

---

Report generated: $(date)
EOF

echo "Audit report: $report"
```

---

## Manifest Storage for Compliance

### Local Filesystem (Development)

```yaml
# config/configurations/manifest-local.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: manifest_local
spec:
  manifest:
    type: local
    path: ".strata/deployments"
```

**Pros:**
- Simple, no external dependencies
- Manifests available immediately after build/deploy

**Cons:**
- Not backed up (unless entire repo is)
- No version history

### GitOps (Production)

```yaml
# config/configurations/manifest-gitops.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: manifest_gitops
spec:
  repositories:
    - name: xyz-state-repo
      url: git@github.com:acme/xyz-state.git
      branch: main
      clone: true

  manifest:
    type: gitops
    path: "deployments"
    repository: xyz-state-repo
    branch: manifests
    tag: true  # Create git tags for each manifest
```

**Pros:**
- Immutable via Git (can't be modified)
- Automated backup (replicated to multiple remotes)
- Git history for audit trail
- Tags for important milestones

**Cons:**
- Requires network (not available offline)
- Slight latency on deploy (git push)

**Compliance benefit:**
- State repository is the source of truth
- All changes tracked in Git (who, what, when)
- Tags enable rollback to known-good versions

---

## Integration Points

### SIEM / Log Aggregation

**Native SIEM forwarding via `strata audit`:**

```bash
# Forward all deploy-log entries to Splunk HEC (configured integration)
strata audit export --siem splunk_hec

# Forward last 30 entries and write a local backup
strata audit export --siem splunk_hec --last 30 --out audit-backup.json

# Replay entries after SIEM downtime
strata audit resend --since 2024-06-01T00:00:00Z
```

See [SIEM Audit Forwarding](./siem-audit-forwarding.md) for full configuration of Splunk HEC, Azure Sentinel, ELK, and OpenTelemetry backends.

**Forward manifests to Splunk via native HEC:**

Configure the integration in `configuration.yaml`:

```yaml
integrations:
  - name: splunk_hec
    type: splunk
    capabilities: [audit]
    endpoints:
      address: https://splunk.acme.com:8088
    authentication:
      method: api_key
      api_key:
        api_key: ${SPLUNK_HEC_TOKEN}
    properties:
      index: infra_audit
      sourcetype: strata_deploy
```

Then forward:

```bash
strata audit export --siem splunk_hec
```

### Compliance Scanning

**Auto-scan manifests for policy violations:**

```bash
#!/bin/bash
# GitHub Actions: On new manifest commit

event_type=$(jq -r '.action' deploy_manifest.json)
status=$(jq -r '.spec.status' deploy_manifest.json)

if [[ "$event_type" == "deploy" && "$status" != "success" ]]; then
  # Failed deployment → alert security team
  curl -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $SLACK_TOKEN" \
    -d "{
      \"channel\": \"#security-alerts\",
      \"text\": \"❌ Failed deployment detected in manifest: $(jq -r '.metadata.name' deploy_manifest.json)\"
    }"
fi
```

### Change Management System

**Link manifests to change tickets:**

```bash
# Jira integration: Create ticket with manifest evidence

manifest=$(strata manifest list --deployment prod --last 1 --output json | jq -r '.[0].path')
user=$(strata manifest show $manifest --output json | jq -r '.spec.user')

curl -X POST "https://jira.acme.com/rest/api/2/issue" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n "$JIRA_USER:$JIRA_TOKEN" | base64)" \
  -d "{
    \"fields\": {
      \"project\": {\"key\": \"INFRA\"},
      \"summary\": \"Infrastructure deployment: $user\",
      \"description\": \"Manifest: $manifest\",
      \"labels\": [\"infrastructure-change\", \"production\"]
    }
  }"
```

---

## Best Practices

1. **Export manifests regularly** — Schedule weekly exports for archival
2. **Store in version control** — Commit manifests to state repository (GitOps)
3. **Tag releases** — Use Git tags to mark production releases by version
4. **Review builds before deploying** — Inspect build manifest policy results
5. **Archive old manifests** — Implement retention policy (keep last 90 days)
6. **Automate compliance checks** — Use SIEM/policy engine to query manifests
7. **Include manifests in change tickets** — Link deployments to change management
8. **Verify SBOMs for vulnerabilities** — Scan manifests for CVEs post-deploy

---

## See Also

- [Deployment Manifests Guide](./deployment-manifests.md) — Manifest generation and structure
- [Manifest CLI Reference](./manifest-cli.md) — Command reference for `strata manifest`
- [ADR 0021: Manifests as First-Class Artifacts](../decisions/0021-deployment-manifests-as-first-class-build-artifacts.md) — Design rationale
- [NIS2 Directive](https://www.cybersecuritycouncil.eu/nis2-directive/) — EU cybersecurity regulations
- [ISAE 3402](https://www.aicpa.org/interestareas/informationsystems/resources/assuranceadvisoryservicesstatements) — SOC 2 controls framework
- [PCI DSS](https://www.pcisecuritystandards.org/) — Payment card security standard
