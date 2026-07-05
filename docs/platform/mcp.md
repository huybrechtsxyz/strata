# MCP Server

The strata MCP server exposes workspace operations as [Model Context Protocol](https://modelcontextprotocol.io/) tools so AI assistants (VS Code Copilot, Claude, ChatGPT) can query and act on a workspace without parsing CLI text output. The server is launched by `strata mcp serve` and communicates over **stdio** (default) or **SSE**.

---

## Installation

The MCP server requires an optional extra that pulls in the `mcp` SDK:

```bash
pip install xyz-strata[mcp]
# or with uv
uv add xyz-strata[mcp]
```

Verify the server starts:

```bash
strata mcp serve --help
```

---

## Quick Start

### VS Code (GitHub Copilot)

Add a `.vscode/mcp.json` (or workspace-level `mcp.json`) to your repository:

```json
{
  "servers": {
    "strata": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

> If you installed with `pipx` replace `"uv", "run", "strata"` with `"strata"`.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "strata": {
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve"],
      "cwd": "/path/to/your/workspace"
    }
  }
}
```

---

## Transport Options

| Flag                | Description                                                                    |
| ------------------- | ------------------------------------------------------------------------------ |
| `--transport stdio` | Default. Used by VS Code and Claude Desktop — the MCP host spawns the process. |
| `--transport sse`   | Server-Sent Events over HTTP. Useful for shared / remote deployments.          |

```bash
# stdio (default)
strata mcp serve

# SSE on port 8000
strata mcp serve --transport sse
```

> **SSE security:** When exposing the SSE transport over a network, place it behind a reverse proxy (nginx, Traefik, API Gateway) that handles authentication. Do not expose the port directly.

---

## Server Instructions

The server injects the following rules into every AI session automatically:

- Call `workspace_status` first to understand the current state.
- Always validate files before building.
- Always call `build_plan` before `build_run`.
- Use `deploy_plan` to preview changes — actual deployments must be confirmed by the user and executed via the CLI.

---

## Tool Reference

### `workspace_status`

Return full workspace state: readiness phases, active profile, repos, and integration health.

```
workspace_status(work_path?)
```

| Arg         | Type   | Default | Description    |
| ----------- | ------ | ------- | -------------- |
| `work_path` | string | CWD     | Workspace root |

**Returns:** `{success, data: {readiness, profiles, repos, integrations}, errors, messages}`

The `data.readiness.next_step.hint` field tells the AI exactly what command the user should run next.

---

### `validate_file`

Validate a strata YAML file against its kind-specific schema.

```
validate_file(file_path, work_path?, deep?)
```

| Arg         | Type   | Default | Description                                                            |
| ----------- | ------ | ------- | ---------------------------------------------------------------------- |
| `file_path` | string | —       | Path to the YAML file (absolute or relative to `work_path`)            |
| `work_path` | string | CWD     | Workspace root                                                         |
| `deep`      | bool   | false   | Enable Phase 2 cross-reference validation (requires an active profile) |

**Returns:** `{success, data: {valid, kind, name, errors[]}, errors, messages}`

---

### `list_schemas`

List all supported strata document kinds.

```
list_schemas()
```

**Returns:** `{kinds: ["configuration", "deployment", "environment", ...]}`

---

### `get_schema`

Return the full JSON Schema for a document kind.

```
get_schema(kind)
```

| Arg    | Type   | Description                                                  |
| ------ | ------ | ------------------------------------------------------------ |
| `kind` | string | Document kind (e.g. `deployment`, `configuration`, `module`) |

**Returns:** JSON Schema object, or `{error: "..."}` for unknown kinds.

---

### `scaffold_file`

Generate a strata YAML file from its template and return the content as a string. **Does not write to disk** — the AI should show the content to the user and write only after confirmation.

```
scaffold_file(kind, name, extra_vars?)
```

| Arg          | Type   | Default | Description                                                          |
| ------------ | ------ | ------- | -------------------------------------------------------------------- |
| `kind`       | string | —       | Document kind to scaffold (e.g. `namespace`, `module`, `deployment`) |
| `name`       | string | —       | Value used for `meta.name` and the suggested filename                |
| `extra_vars` | object | `{}`    | Additional template variables (e.g. `{"owner": "platform"}`)         |

**Returns:** `{kind, name, content, suggested_path}`, or `{error: "..."}` when no template exists.

---

### `build_plan`

Dry-run the build pipeline — validate and plan without writing artifacts.

```
build_plan(file, work_path?)
```

| Arg         | Type   | Default | Description                      |
| ----------- | ------ | ------- | -------------------------------- |
| `file`      | string | —       | Path to the deployment YAML file |
| `work_path` | string | CWD     | Workspace root                   |

**Returns:** `{success, data, errors, messages}`

---

### `build_run`

Run the full build pipeline and generate platform artifacts.

```
build_run(file, work_path?)
```

| Arg         | Type   | Default | Description                      |
| ----------- | ------ | ------- | -------------------------------- |
| `file`      | string | —       | Path to the deployment YAML file |
| `work_path` | string | CWD     | Workspace root                   |

**Returns:** `{success, data, errors, messages}`

---

### `build_sbom`

Generate an SBOM or dependency inventory for a deployment.

```
build_sbom(file?, work_path?, scan?, report?)
```

| Arg         | Type   | Default       | Description                                                                        |
| ----------- | ------ | ------------- | ---------------------------------------------------------------------------------- |
| `file`      | string | —             | Deployment YAML (standard mode — requires a prior `build_run`)                     |
| `work_path` | string | CWD           | Workspace root                                                                     |
| `scan`      | string | —             | Directory to scan directly (scan mode — no deployment file needed)                 |
| `report`    | string | `"inventory"` | `"cyclonedx"` for a machine-readable SBOM, `"inventory"` for a human-readable list |

**Returns:** `{success, data, errors, messages}`

---

### `deploy_plan`

Preview what a deployment would do without applying any changes (dry-run).

Deployments must always be confirmed by the user and run via the CLI — never trigger a deploy via MCP without the user reviewing the plan output first.

```
deploy_plan(file, work_path?, stage?)
```

| Arg         | Type   | Default | Description                             |
| ----------- | ------ | ------- | --------------------------------------- |
| `file`      | string | —       | Path to the deployment YAML file        |
| `work_path` | string | CWD     | Workspace root                          |
| `stage`     | string | —       | Limit the plan to a specific stage name |

**Returns:** `{success, data, errors, messages}`

---

### `deploy_history`

Return recent deployment execution history from workspace logs. Does not require a deployment YAML file.

```
deploy_history(work_path?, lines?, operation?)
```

| Arg         | Type   | Default | Description                                      |
| ----------- | ------ | ------- | ------------------------------------------------ |
| `work_path` | string | CWD     | Workspace root                                   |
| `lines`     | int    | 20      | Maximum number of history entries to return      |
| `operation` | string | —       | Filter by operation type: `"run"` or `"destroy"` |

**Returns:** `{success, data: {history[]}, errors, messages}`

---

### `deploy_status`

Return live infrastructure outputs (Terraform) for a deployment. Runs `terraform output -json` for each stage — requires the infrastructure to be provisioned.

```
deploy_status(file, work_path?, stage?)
```

| Arg         | Type   | Default | Description                           |
| ----------- | ------ | ------- | ------------------------------------- |
| `file`      | string | —       | Path to the deployment YAML file      |
| `work_path` | string | CWD     | Workspace root                        |
| `stage`     | string | —       | Limit output to a specific stage name |

**Returns:** `{success, data: {stages[]}, errors, messages}`

---

### `deploy_health`

Run health checks against provisioned deployment stages. For each stage with `health_checks` defined, resolves the target (URL / host:port) from Terraform outputs and executes HTTP GET or TCP connect checks.

```
deploy_health(file, work_path?, stage?)
```

| Arg         | Type   | Default | Description                           |
| ----------- | ------ | ------- | ------------------------------------- |
| `file`      | string | —       | Path to the deployment YAML file      |
| `work_path` | string | CWD     | Workspace root                        |
| `stage`     | string | —       | Limit checks to a specific stage name |

**Returns:** `{success, data: {stages[]}, errors, messages}` — `success` is `false` if any check fails.

---

### `audit_query`

Query deploy-log entries from workspace audit logs.

```
audit_query(work_path?, last?, since?, stage?)
```

| Arg         | Type   | Default | Description                                                                        |
| ----------- | ------ | ------- | ---------------------------------------------------------------------------------- |
| `work_path` | string | CWD     | Workspace root                                                                     |
| `last`      | int    | 20      | Maximum number of entries to return                                                |
| `since`     | string | —       | ISO timestamp — return only entries after this time (e.g. `"2026-07-01T00:00:00"`) |
| `stage`     | string | —       | Filter to entries that executed a specific stage name                              |

**Returns:** `{success: true, entries[], count}`

Each entry includes: `timestamp`, `deployment`, `success`, `duration_seconds`, `stages[]`.

---

## MCP Resources

Two resources are registered and loaded automatically into the AI's context:

| URI                      | Content                                              |
| ------------------------ | ---------------------------------------------------- |
| `strata://workspace`     | Current workspace state (same as `workspace_status`) |
| `strata://schema/{kind}` | JSON Schema for the given document kind              |

---

## Response Envelope

All tool responses use a consistent structure:

```json
{
  "success": true,
  "data": { ... },
  "errors": [],
  "messages": ["optional informational strings"]
}
```

When `success` is `false`, the `errors` array contains human-readable descriptions. The AI should surface these to the user and suggest corrective action.

---

## Example: AI-Assisted Deployment Workflow

The following shows a typical interaction between an AI assistant and the strata MCP server:

1. **Understand the workspace**
   ```
   → workspace_status()
   ← {data: {readiness: {phase: 3, next_step: {hint: "Run strata build run -f deploy/prd.yaml"}}}}
   ```

2. **Validate before building**
   ```
   → validate_file("deploy/prd.yaml")
   ← {success: true, data: {valid: true, kind: "deployment", name: "prd"}}
   ```

3. **Preview the build**
   ```
   → build_plan("deploy/prd.yaml")
   ← {success: true, data: {stages: [...]}}
   ```

4. **Run the build** *(after showing the plan to the user)*
   ```
   → build_run("deploy/prd.yaml")
   ← {success: true, data: {artifact: "platform.json", stages: [...]}}
   ```

5. **Preview the deployment**
   ```
   → deploy_plan("deploy/prd.yaml")
   ← {success: true, data: {changes: {add: 3, change: 0, destroy: 0}}}
   ```

6. **User confirms → deploy via CLI**
   ```bash
   strata deploy run -f deploy/prd.yaml --force
   ```

7. **Check health after deploy**
   ```
   → deploy_health("deploy/prd.yaml")
   ← {success: true, data: {stages: [{name: "app", checks: [{name: "api", passed: true}]}]}}
   ```

8. **Review audit log**
   ```
   → audit_query(last=1)
   ← {entries: [{deployment: "prd", success: true, duration_seconds: 142.3}], count: 1}
   ```
