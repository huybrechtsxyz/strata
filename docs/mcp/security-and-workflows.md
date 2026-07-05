# Security & Workflows

Security considerations for using the strata MCP server in production, including API key management, audit logging, and approval gates.

## Security Model

### Core Principle: "AI Previews, Humans Execute"

The MCP server is designed with a **fundamental security boundary**:

| Operation Type            | Tool Availability       | Executor              |
| ------------------------- | ----------------------- | --------------------- |
| **Query** (read-only)     | ✅ Available via MCP     | AI tool               |
| **Preview** (dry-run)     | ✅ Available via MCP     | AI tool               |
| **Execute** (destructive) | ❌ NOT available via MCP | **User via CLI only** |

**Example:**
- `build_plan()` — Shows what _would_ be built ✅ Available via MCP
- `build_run()` — Actually builds artifacts ⚠️ See [note](#destructive-operations-note) below
- `deploy_plan()` — Shows what _would_ be deployed ✅ Available via MCP
- `deploy run` — Actually deploys resources ❌ **CLI only, not via MCP**

This design ensures **only explicitly confirmed CLI operations** actually provision infrastructure.

### Destructive Operations Note

`build_run()` and `build_sbom()` are available via MCP because they:
1. Write to the local `build/` directory (not cloud infrastructure)
2. Don't modify deployed resources
3. Can be safely retried or rolled back by deleting the build directory

True destructive operations (`strata deploy run`, `strata deploy destroy`) are **CLI-only**.

---

## API Keys & Secrets Management

### ✅ Secrets Are Never Exposed via MCP

Strata separates secrets from configuration:

**Configuration YAML** (`deployment.yaml`):
```yaml
spec:
  provisioners:
    - name: tf
      provisioner: terraform
      source:
        repository: infrastructure
        source_path: terraform
      configuration:
        variables:
          # Variables are in the YAML (visible)
          environment: staging
          region: eastus
          ssh_key_secret: my_ssh_key  # ← Reference only, not the actual key
```

**Secrets Storage** (`.strata/secrets/` or external secret manager):
- SSH keys stored in local secret store or Bitwarden, not YAML
- MCP tools can validate that a referenced secret exists, but never return its content
- Secrets are resolved _at deployment time_ (when running the CLI), not via MCP

### How MCP Tools Handle Secrets

When you call `validate_file()` via MCP:
- ✅ Validates that secret references are valid
- ❌ Does NOT fetch or return the actual secret content

Example:
```json
{
  "success": true,
  "data": {
    "valid": true,
    "issues": ["SSH key secret 'my_ssh_key' is referenced but not found"]
  }
}
```

The actual secret content is only accessed when you run `strata deploy run` via the CLI in your terminal.

### Best Practice: Never Paste Secrets in Prompts

❌ **Don't do this:**
```
You: "Validate this YAML. The SSH key is: -----BEGIN PRIVATE KEY-----..."
```

✅ **Do this instead:**
```
You: "Validate my deployment YAML."
Claude: [calls validate_file()] ✅
You: "I see the SSH key secret reference is valid. Ready to deploy."
```

---

## Audit Logging

### What's Logged?

Every deployment operation creates an audit log entry:

**Logged Information:**
- Timestamp
- Deployment file
- User (OS user running the CLI)
- Operation (build_run, deploy_run, deploy_destroy, etc.)
- Stages executed
- Duration
- Success/failure status
- Error messages (if failed)

**Location:** `.strata/logs/` directory

### Querying Audit Logs via MCP

Use `audit_query()` to analyze deployment history:

```python
# Get last 20 deployments
result = audit_query(work_path="/my/workspace", last=20)
# Returns: [
#   {timestamp, deployment, success, duration_seconds, stages},
#   ...
# ]

# Get deployments since a specific time
result = audit_query(since="2026-07-01T00:00:00")

# Get deployments for a specific stage
result = audit_query(stage="infrastructure")
```

### Example: Claude Auditing a Deployment

**You:** "Show me all deployments in the last 24 hours."

**Claude** calls `audit_query(since="2026-07-04T12:00:00")` and shows:

```
📊 Deployment History (Last 24h)

1. 2026-07-05 14:23:45 — deploy-staging.yaml
   ✅ Success | infrastructure stage | 8m 34s

2. 2026-07-05 09:15:22 — deploy-prod.yaml
   ✅ Success | infrastructure, services stages | 15m 12s

3. 2026-07-04 23:41:10 — deploy-staging.yaml
   ❌ Failed | infrastructure stage | 4m 15s
   Error: Resource group already exists

3 deployments total (2 successful, 1 failed).
```

### Integration with Compliance Tools

Export audit logs to SIEM systems (Splunk, ELK, etc.):

```bash
# View raw audit logs
cat .strata/logs/deploy-*.json

# Parse and export
jq '.[]' .strata/logs/deploy-*.json | grep "failure"
```

---

## Approval Workflows

### Pattern 1: AI Suggests, Human Approves, CLI Executes

**Workflow:**

```
1. You ask Claude: "Deploy staging?"
2. Claude calls deploy_plan()
3. Claude shows you the Terraform plan
4. You review and say: "Looks good, deploy it."
5. You run CLI manually: strata deploy run -f deploy-staging.yaml
6. Claude queries audit logs: "Deployment complete. Status: ✅"
```

**Why this is safe:**
- AI cannot execute the deploy
- Human reviews the plan before execution
- CLI operation is audited
- User has opportunity to abort

### Pattern 2: Slack/Teams Bot with Approval Gate

**Setup:**

```
1. Deployment request triggered (via webhook or polling)
2. Bot calls deploy_plan() to generate preview
3. Bot posts preview to Slack/Teams with "Approve" button
4. Reviewer clicks "Approve"
5. Approval triggering script runs CLI: strata deploy run -f ...
6. Bot queries audit logs and posts result
```

**Pseudocode (Python):**

```python
from slack_sdk import WebClient
import subprocess

slack = WebClient(token="xoxb-...")

def request_deployment(file_path):
    # 1. Get deployment preview via MCP
    preview = mcp_client.deploy_plan(file=file_path)
    
    # 2. Post to Slack for review
    slack.chat_postMessage(
        channel="#deployments",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Deploy `{file_path}`?\n\n{preview}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": f"approve_{file_path}"
                    }
                ]
            }
        ]
    )

def handle_approval(file_path):
    # 3. Execute deployment via CLI when approved
    result = subprocess.run(
        ["strata", "deploy", "run", "-f", file_path, "--force"],
        capture_output=True,
        text=True
    )
    
    # 4. Post result back to Slack
    status = "✅ Success" if result.returncode == 0 else "❌ Failed"
    slack.chat_postMessage(
        channel="#deployments",
        text=f"{status}: {file_path}"
    )
```

### Pattern 3: Role-Based Access Control (RBAC)

**Setup:**

```yaml
# .strata/rbac.yaml
roles:
  developer:
    permissions:
      - validate_file
      - build_plan
      - build_run
      - scaffold_file
  platform_engineer:
    permissions:
      - validate_file
      - build_plan
      - build_run
      - deploy_plan
      - deploy_status
  sre:
    permissions:
      - validate_file
      - build_plan
      - build_run
      - deploy_plan
      - deploy_status
      - deploy_health
      - audit_query
      - deploy_history
```

**Enforcement:**

MCP servers can validate the caller's role before granting access:

```python
def validate_file(file_path, work_path, deep, user_role):
    if "validate_file" not in ROLES[user_role]["permissions"]:
        raise PermissionError(f"Role '{user_role}' cannot validate files")
    # ... proceed with validation
```

---

## Network Security

### MCP Transport: Stdio (Local Only)

The MCP server uses **stdio transport**, which means:

✅ **Secure by default:**
- Runs in the same process as the caller (no network)
- No HTTP/TCP ports exposed
- No remote access
- Caller must have local shell access to the workspace

❌ **Not suitable for remote access:**
- No built-in authentication
- No encryption (local process only)
- Cannot expose server to the internet directly

### Remote MCP via SSH (Advanced)

If you need remote access, use SSH tunneling:

```bash
# On your workstation
ssh -L 9000:localhost:9000 user@server.example.com \
  "strata mcp serve"

# On server
strata mcp serve --listen 127.0.0.1:9000
```

This forwards the server over SSH (encrypted), but strata MCP doesn't natively support this. Use a wrapper like `mcp-tunnel` if needed.

---

## Secrets Management Integration

### Pattern 1: Local `.strata/secrets/` Directory

Store secrets locally:

```
.strata/
├── secrets/
│   ├── azure-client-secret.txt
│   ├── ssh-key.pem
│   └── docker-registry-token.txt
```

Configure deployment to use them:

```yaml
spec:
  provisioners:
    - name: tf
      provisioner: terraform
      configuration:
        secrets:
          - key: azure_client_secret
            source: file
            value: .strata/secrets/azure-client-secret.txt
```

### Pattern 2: Bitwarden Integration

Use Bitwarden as the secret store:

```yaml
spec:
  provisioners:
    - name: tf
      provisioner: terraform
      configuration:
        secrets:
          - key: docker_registry_token
            source: bitwarden
            value: item-uuid-12345
```

Strata resolves the secret from Bitwarden at deploy time.

### Pattern 3: AWS Secrets Manager

Use AWS Secrets Manager:

```yaml
spec:
  provisioners:
    - name: tf
      provisioner: terraform
      configuration:
        secrets:
          - key: database_password
            source: aws-secrets-manager
            value: "prod/database/password"
```

### Best Practice: Least Privilege

1. **MCP tools never fetch secrets** — they validate references only
2. **Secrets stored outside YAML** — not in deployment files
3. **Secrets resolved at CLI time** — when the actual deployment runs
4. **Audit logs don't record secret values** — only the operation

---

## Production Checklist

Before using strata MCP in production:

- ✅ **Install MCP dependency** — `pip install xyz-strata[mcp]`
- ✅ **Configure approval workflow** — Decide on human review process
- ✅ **Enable audit logging** — Logs go to `.strata/logs/`
- ✅ **Set up RBAC** — Define roles and permissions per team
- ✅ **Secrets strategy** — Use Bitwarden, AWS Secrets, or local store
- ✅ **Test end-to-end** — Validate → Build → Deploy → Monitor workflow
- ✅ **Document runbooks** — How to handle failures via MCP queries
- ✅ **Monitor audit logs** — Export to SIEM if required for compliance
- ✅ **Define escalation** — Who to contact if deployment fails

---

## Common Threats & Mitigations

| Threat                                                   | Mitigation                                                      |
| -------------------------------------------------------- | --------------------------------------------------------------- |
| AI tool executes destructive operations without approval | Only CLI can execute; MCP is preview/query only                 |
| Secrets leaked in AI chat                                | Secrets never exposed via MCP; resolved at CLI time             |
| Unauthorized user runs deployments                       | RBAC + file permissions; audit logs track who ran what          |
| Deployment fails silently                                | Audit logs capture success/failure; Claude can query history    |
| Invalid YAML deploys to production                       | `validate_file()` catches issues; always validate before deploy |
| Infrastructure drift undetected                          | `deploy_status()` and `deploy_health()` detect changes          |

---

## See Also

- [Claude Deployment Assistant](claude-deployment-assistant.md) — End-to-end workflow example
- [AI-Assisted Troubleshooting](ai-troubleshooting.md) — Using audit logs for diagnostics
- [Tools Reference](tools-reference.md) — Full MCP tools API
