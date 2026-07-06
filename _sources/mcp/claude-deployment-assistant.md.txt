# Claude Deployment Assistant: End-to-End Example

This guide walks through a complete scenario: using Claude with the strata MCP server to validate, build, and preview an infrastructure deployment.

## Scenario

You're deploying a new environment to Azure AKS. Instead of manually running CLI commands, you ask Claude to:
1. Generate a deployment YAML from a template
2. Validate the YAML
3. Show what will be built and deployed
4. Suggest improvements
5. Provide instructions for executing the deployment

## Prerequisites

- **strata workspace** initialized (`strata sln init`)
- **Claude Desktop** installed (or Claude API access)
- **MCP server running**: `strata mcp serve` in your workspace
- **Claude configured** with strata MCP server (see [Setup](setup-and-installation.md))

---

## Step 1: Configure Claude Desktop

Add strata MCP server to your Claude configuration:

**File:** `~/.claude/claude-config.json` (or `%APPDATA%\Roaming\Claude\claude-config.json` on Windows)

```json
{
  "mcpServers": {
    "strata": {
      "command": "strata",
      "args": ["mcp", "serve"],
      "cwd": "/Users/yourname/projects/my-strata-workspace"
    }
  }
}
```

Replace `/Users/yourname/projects/my-strata-workspace` with your actual workspace path.

**Restart Claude** to load the new configuration.

---

## Step 2: Verify MCP Connection

Start Claude Desktop and send:

> "What tools do you have access to?"

Claude should respond with a list mentioning strata tools:
- `workspace_status` — Check workspace state
- `validate_file` — Validate YAML
- `scaffold_file` — Generate YAML from template
- `build_plan` — Preview build
- `deploy_plan` — Preview deployment
- ... and more

If you don't see strata tools, check:
1. The configuration file path is correct
2. `cwd` points to your workspace root (should contain `.strata/`)
3. Claude Desktop was restarted after editing the config

---

## Step 3: Full Workflow — Claude Guides You

### 3a. Generate Deployment YAML

**You:** "I need to deploy a new Azure AKS environment called 'staging'. Generate a deployment YAML for me."

**Claude** will:
1. Call `workspace_status()` to understand your setup
2. Call `scaffold_file(kind="deployment", name="staging-aks")` to generate a template
3. Show you the generated YAML
4. Ask where you want to save it

**You:** "Save it as `deploy/deploy-staging.yaml`"

At this point, Claude has generated the file content (not written to disk yet). You copy the output and save it manually, or:

```bash
# Claude shows you the content, you save it
cat > deploy/deploy-staging.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: staging-aks
  annotations:
    description: "Staging environment on Azure AKS"
  labels:
    version: "1.0.0"
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop
EOF
```

### 3b. Validate the YAML

**You:** "Validate the deployment file I just created."

**Claude** will:
1. Call `validate_file(file_path="deploy/deploy-staging.yaml")`
2. Check structural validity (against JSON Schema)
3. If you request, call with `deep=True` to check cross-references

**Claude responds:**
- ✅ **All valid** — File passes structural validation
- ⚠️ **Warnings** — File is valid but has non-critical issues (e.g., missing annotations)
- ❌ **Errors** — File fails validation (Claude shows you the field errors and suggests fixes)

**Example Claude response:**
```
✅ Validation passed!

File: deploy/deploy-staging.yaml
Kind: deployment
Name: staging-aks

The YAML is structurally valid. All required fields are present and correctly typed.

Observations:
- provisioner "terraform" is defined but not configured in your workspace yet
- No repositories linked to this deployment
- Stage "infrastructure" has no timeout specified (will use default 30m)

Next steps:
1. Add terraform provisioner configuration to your workspace.yaml
2. Validate deeper cross-references with --deep flag
3. Run build_plan to see what Terraform will generate
```

### 3c. Preview the Build

**You:** "Show me what will be built if I run the build."

**Claude** will:
1. Call `build_plan(file="deploy/deploy-staging.yaml")`
2. Show you the expected artifacts (Terraform modules, configurations, etc.)

**Claude responds:**
```
🔨 Build Preview (Dry-Run)

Build would produce:
├── platform.json (platform configuration artifact)
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── versions.tf
├── configs/
│   └── azure-aks.yaml

Stage: infrastructure
  Provisioner: terraform
  Modules to instantiate:
    - azure/aks (v1.2.3)
    - azure/network (v1.1.0)

No actual resources would be provisioned (this is a dry-run).

Ready to commit this build? Run:
  strata build run -f deploy/deploy-staging.yaml
```

### 3d. Ask for Improvements

**You:** "Are there any improvements I should make to this deployment?"

**Claude** will:
1. Review the YAML structure
2. Call `get_schema(kind="deployment")` to check best practices
3. Provide recommendations

**Claude responds:**
```
Here are suggested improvements:

1. **Add health checks** — Currently, no health_checks defined for the infrastructure stage
   Add:
   ```yaml
   stages:
     - name: infrastructure
       health_checks:
         - type: http
           target: "https://${azure_lb_ip}/health"
           interval_seconds: 30
   ```

2. **Specify timeouts** — Add explicit timeout for infrastructure provisioning
   Add: `timeout_minutes: 45`

3. **Add labels** — Help with organization and filtering
   Add to meta.labels: `"environment": "staging", "team": "platform"`

4. **Document dependencies** — If this deployment depends on another, document it
   Consider stages order and on_failure policy

5. **Add approver role** — Deployment to staging should require approval
   Add to meta.annotations: `"require_approval": "true"`
```

You can ask Claude to regenerate the YAML with these improvements.

### 3e. Preview the Deployment

**You:** "Show me what will actually be deployed if I run `strata deploy run`."

**Claude** will:
1. Call `build_plan()` to ensure artifacts are up-to-date
2. Call `deploy_plan(file="deploy/deploy-staging.yaml")` to show the deployment preview
3. Display Terraform plan output (what resources will be created/modified)

**Claude responds:**
```
📋 Deployment Preview (Dry-Run)

Stage 1: infrastructure
  Provisioner: Terraform

Changes to be made:
  ✚ azurerm_resource_group.staging (new)
  ✚ azurerm_kubernetes_cluster.staging (new)
  ✚ azurerm_virtual_network.staging (new)
  + 8 more resources

Summary:
  Plan: Create 11 new resources
  Estimated cost: $2,340/month (compute + networking)
  Deployment time: ~15 minutes

⚠️ **CRITICAL**: This will create real Azure resources that cost money.

Next steps:
1. Review the resource list above
2. Confirm with your team
3. Run via CLI: strata deploy run -f deploy/deploy-staging.yaml --force
   (Use --force to skip interactive confirmation)
```

### 3f. Check Current Status

**You:** "What's the current status of my workspace? Is everything ready to deploy?"

**Claude** will:
1. Call `workspace_status()`
2. Display readiness phases, active profile, repositories, etc.

**Claude responds:**
```
📊 Workspace Status

Readiness:
  Phase 1: Initialized ✅
  Phase 2: Repositories configured ✅
  Phase 3: Profiles set up ✅ (active: production)
  Phase 4: Deployment files created ✅
  Phase 5: Validated ✅
  Phase 6: Built ⏳ (pending)
  Phase 7: Deployed 📋 (next step)
  Phase 8: Monitoring ⏳ (after deployment)

Next recommended action:
  1. Run: strata build run -f deploy/deploy-staging.yaml
  2. Then: strata deploy run -f deploy/deploy-staging.yaml

Active profile: production
Repositories: 2 configured
Tools available: terraform, ansible, docker
```

---

## Step 4: Execute the Deployment

At this point, Claude can only preview. **The actual deployment must happen via the CLI** with explicit user confirmation.

```bash
# 1. Build the deployment artifacts
strata build run -f deploy/deploy-staging.yaml

# 2. Deploy to staging (this actually creates resources)
strata deploy run -f deploy/deploy-staging.yaml
```

Claude cannot execute these commands. This is **intentional for safety**:
- MCP tools can query and preview
- Only CLI can execute destructive operations
- All operations are audited

---

## Step 5: Monitor & Verify

**After you've run the deployment via CLI:**

**You:** "Check the health of my deployed staging environment."

**Claude** will:
1. Call `deploy_status(file="deploy/deploy-staging.yaml")` to get current Terraform outputs
2. Call `deploy_health(file="deploy/deploy-staging.yaml")` to run health checks
3. Summarize the state

**Claude responds:**
```
✅ Deployment Status

Stage: infrastructure
Provisioner: Terraform

Infrastructure Outputs:
  kubernetes_cluster_fqdn: staging-aks.eastus.cloudapp.azure.com
  kubernetes_cluster_id: /subscriptions/.../Microsoft.ContainerService/managedClusters/staging-aks
  load_balancer_ip: 20.45.123.45

Health Checks:
  ✅ HTTP GET /health → 200 OK (2.3s)
  ✅ API endpoint responding
  ✅ Kubernetes nodes healthy (3/3 ready)

Recent deployments:
  2026-07-05 14:23:45 — Deploy RUN (success, 8m 34s)
  2026-07-05 14:15:10 — Build RUN (success, 2m 11s)

All systems operational.
```

---

## Step 6: Troubleshoot Issues

**If something goes wrong:**

**You:** "The deployment failed. What went wrong?"

**Claude** will:
1. Call `deploy_history()` to see recent deployment attempts
2. Call `audit_query()` to check the error logs
3. Suggest remediation steps

**Claude responds:**
```
❌ Deployment Failed

Last deployment:
  Time: 2026-07-05 14:52:30
  Status: FAILED
  Duration: 4m 15s
  Error stage: infrastructure

Error Details:
  terraform apply failed:
  Error: azurerm_resource_group.staging already exists
  
  This suggests a previous partial deployment left resources behind.

Suggested fixes:
  1. Check what resources exist in Azure (manual check needed)
  2. Import existing resources into Terraform state:
     terraform import azurerm_resource_group.staging /subscriptions/.../resourceGroups/staging
  3. Try deploy again

Or:
  1. Change the resource group name in your deployment YAML
  2. Run build_plan to preview
  3. Re-run deploy
```

---

## Summary: Claude Workflow Checklist

- ✅ **Generate** YAML template → `scaffold_file()`
- ✅ **Validate** YAML structure → `validate_file()`
- ✅ **Build Preview** what will be generated → `build_plan()`
- ✅ **Deploy Preview** what will be provisioned → `deploy_plan()`
- ✅ **Check Status** of workspace → `workspace_status()`
- ✅ **Get Schema** for validation rules → `get_schema()`
- ✅ **Monitor** deployed infrastructure → `deploy_status()`, `deploy_health()`
- ✅ **Query History** of deployments → `deploy_history()`, `audit_query()`
- ✅ **Troubleshoot** failures → audit logs + error analysis

**CLI remains the executor** — `strata build run`, `strata deploy run`, etc.

---

## Tips & Best Practices

### 1. Always Validate Before Building

```
You: "Validate the file before we build."
Claude: ✅ [validates] → Ready to build!
```

### 2. Always Preview Before Deploying

```
You: "Show me the deploy plan."
Claude: [runs deploy_plan()] → Lists all resources that will be created
You: "That looks good, I'll deploy via CLI."
```

### 3. Use Claude for Analysis, CLI for Action

```
Claude (via MCP):       CLI:
- Validate             - Build
- Preview              - Deploy
- Query status         - Destroy
- Suggest fixes        - Execute operations
```

### 4. Ask for Improvements

```
You: "Are there any best-practice improvements I should make?"
Claude: [reviews YAML] → Lists recommendations
```

### 5. Multi-Step Workflows

```
You: "Generate a namespace and deployment for staging. Validate both. Show me what will be built."
Claude: [calls scaffold_file(), validate_file(), build_plan()] → Complete preview
```

---

## Next Steps

- [Review Security & Workflows](security-and-workflows.md) for production use
- [Troubleshooting Use Cases](ai-troubleshooting.md) for operational patterns
- [Tools Reference](tools-reference.md) for all available MCP tools
