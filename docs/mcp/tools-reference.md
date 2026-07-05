# MCP Tools Reference

Complete API documentation for all strata MCP tools.

## Overview

The strata MCP server exposes **13 tools** and **2 resources** for infrastructure management.

| Tool                 | Type    | Purpose                                 |
| -------------------- | ------- | --------------------------------------- |
| `workspace_status()` | Query   | Get workspace state and readiness       |
| `validate_file()`    | Query   | Validate YAML against schema            |
| `list_schemas()`     | Query   | List all supported document kinds       |
| `get_schema()`       | Query   | Get JSON Schema for a kind              |
| `scaffold_file()`    | Action  | Generate YAML from template             |
| `build_plan()`       | Preview | Dry-run: show what build would generate |
| `build_run()`        | Action  | Execute build and generate artifacts    |
| `build_sbom()`       | Action  | Generate SBOM or dependency inventory   |
| `deploy_plan()`      | Preview | Dry-run: show what deploy would change  |
| `deploy_status()`    | Query   | Get current infrastructure state        |
| `deploy_health()`    | Action  | Run health checks on deployed stages    |
| `deploy_history()`   | Query   | Get recent deployment history           |
| `audit_query()`      | Query   | Query deployment audit logs             |

---

## Query Tools (Read-Only)

These tools don't modify anything. Safe to call anytime.

### `workspace_status(work_path: str = None) → dict`

Return full workspace state: readiness phases, profiles, repos, integrations, and health.

**Always call this first.** The response tells you exactly what command to run next.

**Parameters:**
| Parameter   | Type | Default | Description                                   |
| ----------- | ---- | ------- | --------------------------------------------- |
| `work_path` | str  | CWD     | Workspace root path (override auto-detection) |

**Returns:**
```json
{
  "success": true,
  "data": {
    "workspace_path": "/path/to/workspace",
    "readiness": {
      "phase": 1,
      "next_step": {
        "hint": "Initialize repositories with: strata repo add ...",
        "command": "strata repo add"
      }
    },
    "active_profile": "production",
    "repositories": [
      {"name": "infrastructure", "path": "../infrastructure", "status": "ok"}
    ],
    "tools": {
      "terraform": {"available": true, "version": "1.5.0"},
      "helm": {"available": true, "version": "3.12.0"},
      "ansible": {"available": false}
    }
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
status = await mcp.call("workspace_status")
if status["data"]["readiness"]["phase"] >= 4:
    print("Ready to deploy!")
else:
    print(f"Next step: {status['data']['readiness']['next_step']['hint']}")
```

---

### `validate_file(file_path: str, work_path: str = None, deep: bool = False) → dict`

Validate a strata YAML file against its kind-specific schema.

**Two-phase validation:**
1. **Phase 1 (default):** Structural validation (required fields, types, enums)
2. **Phase 2 (deep=True):** Cross-reference validation (requires active profile)

**Parameters:**
| Parameter   | Type | Default  | Description                                      |
| ----------- | ---- | -------- | ------------------------------------------------ |
| `file_path` | str  | required | Path to YAML (absolute or relative to work_path) |
| `work_path` | str  | CWD      | Workspace root                                   |
| `deep`      | bool | False    | Enable Phase 2 cross-reference validation        |

**Returns:**
```json
{
  "success": true,
  "data": {
    "valid": true,
    "kind": "deployment",
    "name": "production-aks",
    "errors": [],
    "warnings": [
      "Provisioner 'terraform' not found in workspace configuration"
    ]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
# Phase 1: Quick validation
result = await mcp.call("validate_file", {
    "file_path": "deploy/deploy-prod.yaml"
})

# Phase 2: Deep validation (checks cross-references)
result = await mcp.call("validate_file", {
    "file_path": "deploy/deploy-prod.yaml",
    "deep": True
})

if result["data"]["valid"]:
    print("✅ File is valid")
else:
    for error in result["data"]["errors"]:
        print(f"❌ {error}")
```

---

### `list_schemas() → dict`

List all supported strata document kinds.

**Parameters:** None

**Returns:**
```json
{
  "success": true,
  "data": {
    "kinds": [
      "deployment",
      "configuration",
      "environment",
      "firewall",
      "module",
      "namespace",
      "network",
      "dns",
      "provider",
      "resource",
      "workspace"
    ]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
result = await mcp.call("list_schemas")
for kind in result["data"]["kinds"]:
    print(f"- {kind}")
```

---

### `get_schema(kind: str) → dict`

Return the full JSON Schema for a strata document kind.

**Parameters:**
| Parameter | Type | Default  | Description                                 |
| --------- | ---- | -------- | ------------------------------------------- |
| `kind`    | str  | required | Document kind (e.g., deployment, namespace) |

**Returns:** JSON Schema (OpenAPI 3.0 format)

```text
{
  "success": true,
  "data": {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
      "apiVersion": {...},
      "kind": {...},
      "meta": {...},
      "spec": {...}
    },
    "required": ["apiVersion", "kind", "meta", "spec"]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
schema = await mcp.call("get_schema", {"kind": "deployment"})

# Use schema to validate YAML locally
from jsonschema import validate
validate(instance=yaml_doc, schema=schema["data"])
```

---

### `deploy_status(file: str, work_path: str = None, stage: str = None) → dict`

Return current infrastructure state (Terraform outputs) for a deployment.

**Parameters:**
| Parameter   | Type | Default  | Description                  |
| ----------- | ---- | -------- | ---------------------------- |
| `file`      | str  | required | Path to deployment YAML      |
| `work_path` | str  | CWD      | Workspace root               |
| `stage`     | str  | None     | Limit to specific stage name |

**Returns:**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "stages": [
      {
        "name": "infrastructure",
        "status": "provisioned",
        "outputs": {
          "kubernetes_cluster_fqdn": "prod-aks.eastus.cloudapp.azure.com",
          "load_balancer_ip": "20.45.123.45",
          "resource_group_id": "/subscriptions/.../resourceGroups/prod"
        }
      }
    ]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
status = await mcp.call("deploy_status", {"file": "deploy/deploy-prod.yaml"})

for stage in status["data"]["stages"]:
    print(f"Stage: {stage['name']}")
    for key, value in stage["outputs"].items():
        print(f"  {key}: {value}")
```

---

### `deploy_history(work_path: str = None, lines: int = 20, operation: str = None) → dict`

Return recent deployment execution history.

**Parameters:**
| Parameter   | Type | Default | Description                                  |
| ----------- | ---- | ------- | -------------------------------------------- |
| `work_path` | str  | CWD     | Workspace root                               |
| `lines`     | int  | 20      | Number of history entries to return          |
| `operation` | str  | None    | Filter by operation type: "run" or "destroy" |

**Returns:**
```json
{
  "success": true,
  "data": {
    "entries": [
      {
        "timestamp": "2026-07-05T14:23:45Z",
        "deployment": "deploy-prod.yaml",
        "operation": "run",
        "success": true,
        "duration_seconds": 514,
        "stages": ["infrastructure", "services"]
      }
    ],
    "count": 1
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
history = await mcp.call("deploy_history", {"lines": 10})

for entry in history["data"]["entries"]:
    status = "✅" if entry["success"] else "❌"
    print(f"{status} {entry['timestamp']} - {entry['deployment']} ({entry['duration_seconds']}s)")
```

---

### `audit_query(work_path: str = None, last: int = 20, since: str = None, stage: str = None) → dict`

Query deployment audit logs.

**Parameters:**
| Parameter   | Type | Default | Description                                   |
| ----------- | ---- | ------- | --------------------------------------------- |
| `work_path` | str  | CWD     | Workspace root                                |
| `last`      | int  | 20      | Max entries to return                         |
| `since`     | str  | None    | ISO timestamp: return entries after this time |
| `stage`     | str  | None    | Filter to specific stage name                 |

**Returns:**
```json
{
  "success": true,
  "data": {
    "entries": [
      {
        "timestamp": "2026-07-05T14:23:45Z",
        "deployment": "deploy-prod.yaml",
        "stage": "infrastructure",
        "operation": "run",
        "success": true,
        "duration_seconds": 514,
        "user": "alice@example.com",
        "error": null
      }
    ],
    "count": 1
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
# Get last 50 failed deployments
query = await mcp.call("audit_query", {
    "last": 50,
    "since": "2026-06-01T00:00:00"
})

for entry in query["data"]["entries"]:
    if not entry["success"]:
        print(f"Failed: {entry['deployment']} - {entry['error']}")
```

---

## Preview Tools (Dry-Run)

These tools show what _would_ happen without making changes.

### `build_plan(file: str, work_path: str = None) → dict`

Preview what the build would produce (dry-run).

**Parameters:**
| Parameter   | Type | Default  | Description             |
| ----------- | ---- | -------- | ----------------------- |
| `file`      | str  | required | Path to deployment YAML |
| `work_path` | str  | CWD      | Workspace root          |

**Returns:**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "artifacts": {
      "platform": "generated",
      "terraform": ["main.tf", "variables.tf", "outputs.tf"],
      "configs": ["azure-aks.yaml"]
    },
    "stages": [
      {
        "name": "infrastructure",
        "provisioner": "terraform",
        "modules": [
          {"name": "azure/aks", "version": "1.2.3"},
          {"name": "azure/network", "version": "1.1.0"}
        ]
      }
    ],
    "messages": ["terraform modules will be installed from registry"]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
plan = await mcp.call("build_plan", {"file": "deploy/deploy-prod.yaml"})

if plan["success"]:
    print(f"Build would generate: {plan['data']['artifacts']}")
else:
    print(f"Build would fail: {plan['errors']}")
```

---

### `deploy_plan(file: str, work_path: str = None, stage: str = None) → dict`

Preview what the deployment would do (dry-run).

**Shows Terraform plan output and resource changes.**

**Parameters:**
| Parameter   | Type | Default  | Description                  |
| ----------- | ---- | -------- | ---------------------------- |
| `file`      | str  | required | Path to deployment YAML      |
| `work_path` | str  | CWD      | Workspace root               |
| `stage`     | str  | None     | Limit plan to specific stage |

**Returns:**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "stages": [
      {
        "name": "infrastructure",
        "provisioner": "terraform",
        "plan": {
          "add": 11,
          "change": 0,
          "destroy": 0,
          "resources": [
            {
              "type": "azurerm_resource_group",
              "name": "prod",
              "action": "add"
            },
            {
              "type": "azurerm_kubernetes_cluster",
              "name": "prod",
              "action": "add"
            }
          ]
        },
        "estimated_cost": "$2,340/month",
        "estimated_duration": "15 minutes"
      }
    ]
  },
  "errors": [],
  "messages": []
}
```

**⚠️ Important:** Show this plan to the user. **Only CLI can execute** `strata deploy run`.

**Example:**

```python
plan = await mcp.call("deploy_plan", {"file": "deploy/deploy-prod.yaml"})

if plan["success"]:
    for stage in plan["data"]["stages"]:
        print(f"Stage: {stage['name']}")
        print(f"  Changes: +{stage['plan']['add']} resources")
        print(f"  Cost: {stage['estimated_cost']}")
        print(f"  Duration: {stage['estimated_duration']}")
    print("\n✅ Preview complete. Review above and run:")
    print("  strata deploy run -f deploy/deploy-prod.yaml")
else:
    print(f"❌ Deployment would fail: {plan['errors']}")
```

---

## Action Tools

These tools modify the workspace or generate outputs.

### `scaffold_file(kind: str, name: str, extra_vars: dict = None) → dict`

Generate a strata YAML file from template (returns content, doesn't write to disk).

**Parameters:**
| Parameter    | Type | Default  | Description                                          |
| ------------ | ---- | -------- | ---------------------------------------------------- |
| `kind`       | str  | required | Document kind (deployment, namespace, etc.)          |
| `name`       | str  | required | The `meta.name` value                                |
| `extra_vars` | dict | {}       | Additional template variables (owner, version, etc.) |

**Returns:**
```json
{
  "success": true,
  "data": {
    "kind": "deployment",
    "name": "production-aks",
    "content": "apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\n...",
    "suggested_path": "deployments/production-aks.yaml"
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
scaffold = await mcp.call("scaffold_file", {
    "kind": "deployment",
    "name": "staging-gke",
    "extra_vars": {
        "owner": "platform-team",
        "version": "1.0.0",
        "region": "us-central1"
    }
})

if scaffold["success"]:
    yaml_content = scaffold["data"]["content"]
    suggested_path = scaffold["data"]["suggested_path"]
    print(f"Save this content to: {suggested_path}")
    print(yaml_content)
else:
    print(f"Scaffold generation failed: {scaffold['errors']}")
```

---

### `build_run(file: str, work_path: str = None) → dict`

Execute the full build pipeline and generate artifacts.

**⚠️ Creates files in `build/` directory. Safe to retry/delete.**

**Parameters:**
| Parameter   | Type | Default  | Description             |
| ----------- | ---- | -------- | ----------------------- |
| `file`      | str  | required | Path to deployment YAML |
| `work_path` | str  | CWD      | Workspace root          |

**Returns:**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "artifacts_path": "build/deploy-prod",
    "artifacts": {
      "platform": "build/deploy-prod/platform.json",
      "terraform": ["build/deploy-prod/terraform/main.tf"],
      "configs": ["build/deploy-prod/configs/azure-aks.yaml"]
    },
    "duration_seconds": 45,
    "stages": [{"name": "infrastructure", "status": "success"}]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
build = await mcp.call("build_run", {"file": "deploy/deploy-prod.yaml"})

if build["success"]:
    print(f"✅ Build complete: {build['data']['artifacts_path']}")
    print(f"Duration: {build['data']['duration_seconds']}s")
else:
    print(f"❌ Build failed: {build['errors']}")
```

---

### `build_sbom(file: str = None, work_path: str = None, scan: str = None, report: str = "inventory") → dict`

Generate SBOM (Software Bill of Materials) or dependency inventory.

**Two modes:**
1. **Standard:** Build SBOM from deployment (requires `build_run` first)
2. **Scan:** Scan directory directly (no deployment file needed)

**Parameters:**
| Parameter   | Type | Default     | Description                                             |
| ----------- | ---- | ----------- | ------------------------------------------------------- |
| `file`      | str  | None        | Path to deployment YAML (standard mode)                 |
| `work_path` | str  | CWD         | Workspace root                                          |
| `scan`      | str  | None        | Directory to scan (scan mode)                           |
| `report`    | str  | "inventory" | Output format: "cyclonedx" (JSON) or "inventory" (text) |

**Returns (inventory mode):**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "components": [
      {
        "type": "container-image",
        "name": "azure-cli",
        "version": "2.50.0"
      },
      {
        "type": "helm-chart",
        "name": "nginx-ingress",
        "version": "4.8.3"
      },
      {
        "type": "python-library",
        "name": "requests",
        "version": "2.31.0"
      }
    ],
    "vulnerabilities": [
      {"component": "base-image", "cve": "CVE-2023-1234", "severity": "high"}
    ]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
# Generate inventory for deployment
sbom = await mcp.call("build_sbom", {
    "file": "deploy/deploy-prod.yaml",
    "report": "inventory"
})

# Or scan a directory directly
sbom = await mcp.call("build_sbom", {
    "scan": "/path/to/repo",
    "report": "cyclonedx"
})

if sbom["success"]:
    for component in sbom["data"]["components"]:
        print(f"{component['type']}: {component['name']} ({component['version']})")
```

---

### `deploy_health(file: str, work_path: str = None, stage: str = None) → dict`

Run health checks against provisioned deployment stages.

**Executes HTTP GET or TCP checks defined in deployment YAML.**

**Parameters:**
| Parameter   | Type | Default  | Description                    |
| ----------- | ---- | -------- | ------------------------------ |
| `file`      | str  | required | Path to deployment YAML        |
| `work_path` | str  | CWD      | Workspace root                 |
| `stage`     | str  | None     | Limit checks to specific stage |

**Returns:**
```json
{
  "success": true,
  "data": {
    "deployment": "deploy-prod.yaml",
    "overall_status": "healthy",
    "stages": [
      {
        "name": "infrastructure",
        "checks": [
          {
            "name": "api-endpoint",
            "type": "http",
            "target": "https://api.example.com/health",
            "status": "pass",
            "response_time_ms": 45
          },
          {
            "name": "database",
            "type": "tcp",
            "target": "db.example.com:5432",
            "status": "pass",
            "response_time_ms": 12
          }
        ],
        "passed": 2,
        "failed": 0
      }
    ]
  },
  "errors": [],
  "messages": []
}
```

**Example:**

```python
health = await mcp.call("deploy_health", {"file": "deploy/deploy-prod.yaml"})

if health["success"]:
    print(f"Overall: {health['data']['overall_status']}")
    for stage in health["data"]["stages"]:
        print(f"  {stage['name']}: {stage['passed']}/{stage['passed'] + stage['failed']} checks passed")
else:
    print(f"Health check failed: {health['errors']}")
```

---

## MCP Resources

### `strata://schema/{kind}`

Auto-loaded resource: JSON Schema for a strata document kind.

**Automatically loaded into AI context.**

**Example:**

```python
# Claude/AI automatically loads this
schema = resource("strata://schema/deployment")
```

### `strata://workspace`

Auto-loaded resource: Current workspace state and readiness.

**Automatically loaded into AI context.**

**Example:**

```python
# Claude/AI automatically loads this
workspace = resource("strata://workspace")
```

---

## Error Handling

### Success Field

Always check `success` first:

```python
result = await mcp.call("validate_file", {"file_path": "deploy.yaml"})

if result["success"]:
    print(result["data"])
else:
    # Handle error
    print(f"Error: {result['errors']}")
```

### Error Codes

| Code | Meaning                     | Action                             |
| ---- | --------------------------- | ---------------------------------- |
| 0    | Success                     | Proceed normally                   |
| 1    | System/execution failure    | Check `messages` for crash reason  |
| 2    | Usage error (bad arguments) | Fix command syntax                 |
| 3    | Validation failure          | Check `errors` array for specifics |

### Example Error Response

```json
{
  "success": false,
  "data": null,
  "errors": [
    "File not found: deploy/deploy-prod.yaml",
    "Expected absolute or relative path"
  ],
  "messages": ["Validation failed during file loading"]
}
```

---

## Rate Limits & Timeouts

- **No built-in rate limits** in strata MCP
- **Host rate limits** applied by Claude, Copilot, etc.
- **Long operations** (`build_run`, `deploy_plan`) may take minutes
- **Recommended timeout:** 10 minutes for deployment operations

---

## See Also

- [Claude Example](claude-deployment-assistant.md) — Usage patterns
- [Security](security-and-workflows.md) — API key management
- [Troubleshooting](ai-troubleshooting.md) — Real-world scenarios
