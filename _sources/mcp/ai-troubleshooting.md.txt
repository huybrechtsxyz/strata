# AI-Assisted Troubleshooting

Using MCP tools to detect infrastructure drift, analyze changes, and suggest fixes.

## Scenario: Infrastructure Drift Detection

Your monitoring system alerts you:
> "Production AKS cluster has drifted from expected state."

Instead of manually investigating Terraform outputs and deployment logs, you ask Claude to analyze the drift and suggest fixes.

---

## Use Case 1: Detect What Changed

### The Problem

You deployed infrastructure 3 days ago. Something changed (manually edited resource, scaling event, etc.). You need to find out what's different.

### The Claude Workflow

**You:** "Detect infrastructure drift. Show me what changed in production since the last deploy."

**Claude:**
1. Calls `deploy_history(lines=10)` → Finds the last deploy operation
2. Calls `deploy_status(file="deploy/deploy-prod.yaml")` → Gets current Terraform outputs
3. Compares outputs to the deployment file specification
4. Reports differences

**Claude responds:**

```
🔍 Infrastructure Drift Analysis

Last deployment: 2026-07-01 09:15:30 (4 days ago)
Current status: ✅ Resources provisioned

Comparing deployment spec vs. current state:

✅ MATCHING:
  - Kubernetes cluster version: 1.28.4
  - Virtual network CIDR: 10.0.0.0/16
  - Load balancer type: Standard

⚠️ DRIFTED:
  - AKS node pool size: 
    Expected: 3 nodes
    Actual: 5 nodes
    Status: Scaled up manually

  - Cluster tags:
    Expected: {"environment": "production", "team": "platform"}
    Actual: {"environment": "production", "team": "platform", "cost-center": "engineering"}
    Status: Tag added manually

  - Load balancer SKU:
    Expected: Standard
    Actual: Standard (unchanged, but idle)

Probable cause: Manual scaling event on 2026-07-03 + manual tag update.

Remediation options:
  1. Ignore manual changes (leave as-is, update YAML to match reality)
  2. Revert to spec (scale down to 3 nodes, remove cost-center tag)
  3. Update deployment spec (document the changes in YAML)
```

---

## Use Case 2: Analyze Recent Deployments

### The Problem

Your last 3 deployments had issues. You want to understand what happened.

### The Claude Workflow

**You:** "Show me the deployment history for the last week. Were any of them failures?"

**Claude:**
1. Calls `deploy_history(lines=50, operation="run")` → Gets recent deployments
2. Filters for failures
3. Calls `audit_query(since="2026-06-28T00:00:00")` → Gets detailed logs for failed deploys
4. Summarizes issues and outcomes

**Claude responds:**

```
📊 Deployment History (Last 7 Days)

Total: 12 deployments (10 successful, 2 failed)

✅ SUCCESSFUL:
  2026-07-05 14:23:45 | deploy-staging.yaml | infrastructure | 8m 34s
  2026-07-05 09:15:22 | deploy-prod.yaml | infrastructure, services | 15m 12s
  2026-07-04 22:41:10 | deploy-staging.yaml | infrastructure | 7m 58s
  ... (7 more)

❌ FAILED:
  1. 2026-07-04 23:41:10 | deploy-staging.yaml | infrastructure | 4m 15s
     Error: azurerm_resource_group.staging already exists
     Cause: Partial cleanup from previous attempt
     Resolution: Manually deleted resource group, retry succeeded

  2. 2026-07-02 10:33:22 | deploy-prod.yaml | services | 6m 02s
     Error: Kubernetes image pull failed
     Cause: Invalid Docker registry credentials
     Resolution: Updated secret in Bitwarden, redeployed successfully

Pattern: 2/12 failures (~17%) in the last week. Trend: improving (last 3 deploys all successful).
```

---

## Use Case 3: Diagnose a Failed Deployment

### The Problem

Your deployment failed mid-way. You need to understand why and fix it.

### The Claude Workflow

**You:** "The deploy failed 2 hours ago. What was the error?"

**Claude:**
1. Calls `deploy_history(lines=5)` → Finds the failed deployment
2. Calls `audit_query(since="2026-07-05T10:00:00", operation="run")` → Gets error details
3. Calls `validate_file(file=deployment_file, deep=True)` → Checks for configuration issues
4. Suggests fixes

**Claude responds:**

```
❌ Deployment Failure Analysis

Failed deployment:
  File: deploy/deploy-prod.yaml
  Time: 2026-07-05 12:34:56
  Duration: 8m 45s
  Stage: services (failed during Helm deploy)

Error Details:
  helm upgrade failed: timed out waiting for deployment

Root Cause Analysis:
  1. Kubernetes image could not pull
  2. ImagePullBackOff on 2 pods
  3. Docker registry returned 401 Unauthorized

Investigation:
  ✅ Deployment YAML is valid
  ❌ Docker registry secret is missing or expired
  
  Current secret: docker-registry-token
  Status: Exists in Bitwarden, but may have expired

Recommended Fixes:
  1. Quick fix (5 min):
     a. Rotate Docker registry credentials in Bitwarden
     b. Run: strata deploy run -f deploy/deploy-prod.yaml --stage services --force
  
  2. Full fix (prevent recurrence):
     a. Add monitoring alert for image pull failures
     b. Add pre-deploy validation for registry connectivity
     c. Update YAML with retry policy:
        ```yaml
        stages:
          - name: services
            timeout_minutes: 15
            on_failure: retry  # Automatically retry on failure
            max_retries: 3
        ```

Next steps: Update the secret and retry the deploy stage.
```

---

## Use Case 4: Compare Two Deployments

### The Problem

You want to understand the differences between staging and production deployments.

### The Claude Workflow

**You:** "Compare staging and production deployments. What's different?"

**Claude:**
1. Calls `validate_file()` on both files
2. Calls `get_schema()` to understand the structure
3. Compares specifications
4. Shows differences

**Claude responds:**

```
🔄 Deployment Comparison

Staging vs. Production

DIFFERENCES FOUND:

Stage: infrastructure
  ✅ Both use: terraform provisioner
  
  Cluster size:
    staging: 2 nodes (vm_size: Standard_B2s)
    prod: 5 nodes (vm_size: Standard_D2s_v3)
  
  High availability:
    staging: false (single AZ)
    prod: true (3 AZs)
  
  Backup enabled:
    staging: false
    prod: true (daily snapshots)

Stage: services
  ✅ Both use: helm provisioner
  
  Replica count:
    staging: 1 (cost optimization)
    prod: 3 (high availability)
  
  Ingress:
    staging: https://staging.example.com (self-signed cert)
    prod: https://api.example.com (Let's Encrypt)

Resource costs:
  staging: ~$500/month
  prod: ~$3,200/month (6.4x more)

This is expected for production (HA, backup, larger nodes).

Suggestion: Consider scaling staging resources for more realistic load testing.
```

---

## Use Case 5: Suggest Configuration Improvements

### The Problem

You want to improve your deployment configuration but aren't sure what best practices you're missing.

### The Claude Workflow

**You:** "Review my production deployment. Are there security or reliability improvements I should make?"

**Claude:**
1. Calls `validate_file(file="deploy/deploy-prod.yaml", deep=True)`
2. Calls `get_schema()` to understand available options
3. Reviews against best practices
4. Suggests improvements

**Claude responds:**

```
🔍 Configuration Review: deploy/deploy-prod.yaml

SECURITY RECOMMENDATIONS:

1. ❌ No RBAC configured
   Currently: Admin access unrestricted
   Recommendation:
     Add to meta.annotations:
       rbac_enabled: "true"
       default_role: "viewer"  # Require opt-in for elevated access

2. ⚠️  No network policies defined
   Currently: Pod-to-pod traffic unrestricted
   Recommendation:
     Add to spec:
       network_policies:
         - name: default-deny
           policy_type: Ingress
           selector: all
         - name: allow-monitoring
           policy_type: Ingress
           from: monitoring-namespace

3. ⚠️  No pod security policy
   Currently: Containers can run as root
   Recommendation:
     Add to stages[0]:
       security_context:
         run_as_non_root: true
         run_as_user: 1000
         fs_read_only: true

RELIABILITY RECOMMENDATIONS:

4. ❌ No backup configured
   Recommendation:
     backup_policy:
       enabled: true
       schedule: "0 2 * * *"  # 2 AM UTC daily
       retention_days: 30

5. ⚠️  No health checks for load balancer
   Recommendation:
     stages[1]:
       health_checks:
         - type: http
           target: "https://${lb_ip}/health"
           interval_seconds: 30
           timeout_seconds: 5
           healthy_threshold: 2

COST OPTIMIZATION:

6. ℹ️  Consider autoscaling
   Current: Fixed 5 nodes
   Peak usage: ~80% CPU
   Recommendation:
     autoscaling:
       min_nodes: 3
       max_nodes: 10
       target_cpu_utilization: 70%

ESTIMATED IMPACT:
  - Security score: 60% → 90%
  - Reliability: 75% → 95%
  - Cost savings: ~15% with autoscaling
```

---

## Use Case 6: Rollback After Failed Deployment

### The Problem

A deployment went wrong. You need to rollback to the previous state.

### The Claude Workflow

**You:** "The production deployment broke something. Rollback to the previous version."

**Claude:**
1. Calls `deploy_history()` → Finds the previous successful deployment
2. Calls `deploy_status()` → Shows current state
3. Provides rollback instructions

**Claude responds:**

```
⏮️ Rollback Analysis

Current (failed) deployment:
  Time: 2026-07-05 14:23:45
  File: deploy/deploy-prod.yaml (v1.2.3)
  Status: ❌ Failed at stage: services

Previous (successful) deployment:
  Time: 2026-07-04 09:15:22
  File: deploy/deploy-prod.yaml (v1.2.2)
  Status: ✅ Success

Rollback plan:
  1. Revert deployment file to v1.2.2 (from git):
     git checkout v1.2.2 -- deploy/deploy-prod.yaml

  2. Validate the reverted file:
     strata validate deploy/deploy-prod.yaml

  3. Run the deployment:
     strata deploy run -f deploy/deploy-prod.yaml --stage services

  4. Verify health:
     strata deploy health -f deploy/deploy-prod.yaml

Expected outcome:
  - All services back to previous version
  - Health checks passing
  - Logs preserved for post-mortem

Commands to execute (via CLI):
  git checkout v1.2.2 -- deploy/deploy-prod.yaml
  strata deploy run -f deploy/deploy-prod.yaml --force
  strata deploy health -f deploy/deploy-prod.yaml
```

---

## Use Case 7: Cross-Environment Consistency Check

### The Problem

You have multiple environments (staging, production, disaster-recovery). You want to ensure they're configured consistently.

### The Claude Workflow

**You:** "Are staging and DR environments configured the same way? Check consistency."

**Claude:**
1. Calls `validate_file()` on all deployment files
2. Compares specifications
3. Reports consistency issues

**Claude responds:**

```
✅ Cross-Environment Consistency Check

Environments checked:
  - staging (deploy/deploy-staging.yaml)
  - production (deploy/deploy-prod.yaml)
  - disaster-recovery (deploy/deploy-dr.yaml)

CONSISTENCY RESULTS:

✅ MATCHING ACROSS ENVIRONMENTS:
  - Kubernetes version: 1.28.4 (all)
  - Network CIDR: 10.0.0.0/16 (all)
  - Container registry: acr.example.com (all)
  - DNS: managed-dns.example.com (all)

⚠️ INTENTIONAL DIFFERENCES (expected):
  - Node count: staging=2, prod=5, dr=5 ✅ (DR mirrors prod)
  - Backup: staging=disabled, prod=enabled, dr=enabled ✅
  - HA zones: staging=1, prod=3, dr=3 ✅

❌ INCONSISTENCIES (potential issues):
  1. Kubernetes network policy:
     staging: enabled
     production: disabled ⚠️
     DR: enabled
     
     Recommendation: Enable in production for security parity

  2. RBAC configuration:
     staging: simplified roles (dev-friendly)
     production: full RBAC (security-focused)
     DR: simplified roles ⚠️
     
     Recommendation: Use production RBAC config in DR for testing

  3. Monitoring:
     staging: Prometheus only
     production: Prometheus + Splunk
     DR: Prometheus only ⚠️
     
     Recommendation: Add Splunk to DR for realistic monitoring tests

Summary: 2/20 consistency issues (91% aligned). DR should match production for realistic failover testing.
```

---

## Use Case 8: Generate Post-Mortem Report

### The Problem

After a production incident, you need to document what happened for the post-mortem.

### The Claude Workflow

**You:** "Generate a post-mortem report for the incident that happened 2 hours ago."

**Claude:**
1. Calls `audit_query(since="2 hours ago")`
2. Calls `deploy_history()` for context
3. Queries deployment status before/after
4. Generates a structured report

**Claude responds:**

```
📋 Post-Mortem Report: Production Incident 2026-07-05

INCIDENT SUMMARY:
  Time: 2026-07-05 12:34:56 UTC (Resolved: 13:15:22)
  Duration: 40 minutes
  Services affected: Payment API, User service
  Impact: 2,340 failed requests, 0.3% of traffic

TIMELINE:
  12:34:56 — Deployment started (deploy-prod.yaml v1.2.3)
  12:39:15 — Infrastructure stage completed
  12:42:10 — Services stage began (Helm deploy)
  12:43:45 — Image pull failures detected (ImagePullBackOff)
  12:45:22 — Services degraded, traffic errors spike
  12:52:10 — Team notified of incident
  13:01:33 — Root cause identified: Docker registry credentials expired
  13:05:15 — Credentials rotated in Bitwarden
  13:06:22 — Rollback to v1.2.2 initiated
  13:12:45 — Services healthy again
  13:15:22 — All health checks passing

ROOT CAUSE:
  Docker registry credentials expired on 2026-07-05 12:00:00
  No monitoring alert was configured for credential expiration
  Deployment proceeded without pre-deploy registry connectivity check

ACTIONS TAKEN:
  1. Rolled back to v1.2.2 (40 min MTTR)
  2. Rotated credentials in Bitwarden
  3. Updated deployment to include pre-deploy registry check

PREVENTION:
  1. Add monitoring alert for credential expiration (7 days before)
  2. Add deployment-time registry connectivity check
  3. Increase credential rotation frequency from 90 → 30 days

ARTIFACTS:
  - Deployment logs: .strata/logs/deploy-2026-07-05-12-34-56.json
  - Error messages: Saved in incident ticket
  - Customer communication: Posted to status page

Stakeholders notified: Yes
Follow-up meeting: Scheduled for 2026-07-06 09:00 UTC
```

---

## Best Practices for AI-Assisted Troubleshooting

### 1. Always Validate Before Deploying

```
You: "Before we deploy, validate the file."
Claude: [calls validate_file()] ✅
```

### 2. Query History Before Major Changes

```
You: "Show me the last 5 deployments, then suggest what we should update."
Claude: [calls deploy_history(lines=5)] → [suggests updates]
```

### 3. Use Dry-Runs for Safe Testing

```
You: "Show me what would happen if we deployed this."
Claude: [calls deploy_plan()] → Shows preview, no resources created
```

### 4. Compare Before Assuming Drift

```
You: "Is the infrastructure drifted?"
Claude: [calls deploy_status()] → Compares spec vs reality
```

### 5. Document Findings

```
You: "Generate a summary of what we found and what to do next."
Claude: [creates structured report] → Copy to incident ticket
```

---

## Next Steps

- [Security & Workflows](security-and-workflows.md) — Approval gates and audit trails
- [Claude Deployment Assistant](claude-deployment-assistant.md) — End-to-end workflow
- [Tools Reference](tools-reference.md) — Full MCP tools API
