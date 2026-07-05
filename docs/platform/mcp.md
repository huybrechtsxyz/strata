# MCP Server — Design & Implementation Guide

**Issue:** [#166 MCP Server Implementation](https://github.com/huybrechtsxyz/strata/issues/166)
**Milestone:** v1.0.0

---

## Overview

The strata MCP server exposes workspace operations as [Model Context Protocol](https://modelcontextprotocol.io/) tools so AI assistants (Claude, GitHub Copilot, ChatGPT) can query and act on a workspace without parsing CLI text output. The server is launched by `strata mcp serve` and communicates over **stdio** (default) or **SSE**.

---

## Current State (Implemented)

### CLI entry point (`src/strata/commands/cli_mcp.py`)
- `strata mcp serve --transport stdio|sse` — registered under the `mcp` command group in `cli.py`
- Lazy-imports `strata.mcp.server`; raises a clear error when the optional `mcp` extra is not installed

### Server (`src/strata/mcp/server.py`)

| Tool               | Command class                    | Description                                                             |
| ------------------ | -------------------------------- | ----------------------------------------------------------------------- |
| `workspace_status` | `StatusCommand`                  | Full workspace state — readiness phases, profiles, repos                |
| `validate_file`    | `ValidateCommand`                | Schema + Phase 2 cross-reference validation                             |
| `list_schemas`     | `PlatformKind`                   | Enumerate all supported document kinds                                  |
| `get_schema`       | `_KIND_TO_MODEL`                 | Return JSON Schema for a document kind                                  |
| `scaffold_file`    | `TemplateProcessor`              | Generate YAML from template (no disk write)                             |
| `build_plan`       | `RunBuildCommand(dry_run=True)`  | Dry-run build                                                           |
| `build_run`        | `RunBuildCommand(dry_run=False)` | Full build pipeline                                                     |
| `deploy_plan`      | `RunDeployCommand(dry_run=True)` | Dry-run deploy (safe; explicit user confirm required for actual deploy) |

Resources registered: `strata://schema/{kind}`, `strata://workspace`

### Optional dependency (`pyproject.toml`)
```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0"]
```

### Tests (`tests/strata/commands/test_commands_mcp.py`)
- CLI group help / `--transport` option presence
- Graceful error when `mcp` package absent
- Presence tests for all 8 tools
- Functional tests: `list_schemas`, `get_schema`, `scaffold_file`, `workspace_status` (mocked)

---

## Gap Analysis

| Issue Requirement                | Status | Notes                                                               |
| -------------------------------- | :----: | ------------------------------------------------------------------- |
| stdio transport                  |   ✅    | `--transport stdio` (default)                                       |
| SSE/TCP transport                |   ✅    | `--transport sse`                                                   |
| `strata/validate` tool           |   ✅    | `validate_file`                                                     |
| `strata/build` tool              |   ✅    | `build_plan` + `build_run`                                          |
| `strata/deploy` tool (plan)      |   ✅    | `deploy_plan` (dry-run; actual deploy is intentionally CLI-only)    |
| `strata/audit` tool              |   ❌    | **Missing** — issue explicitly lists this                           |
| `strata/schema` tool             |   ✅    | `list_schemas` + `get_schema`                                       |
| Deploy observability tools       |   ❌    | `deploy_history`, `deploy_status`, `deploy_health` missing          |
| `build_sbom` tool                |   ❌    | **Missing**                                                         |
| Response envelope compliance     |   ⚠️    | `_run_command` returns `_output_data` only; errors silently dropped |
| Authentication (API key / SSE)   |   ❌    | Not implemented; deferred (see §3.7)                                |
| Rate limiting                    |   ❌    | Out of scope for v1                                                 |
| MCP compliance tests             |   ⚠️    | Partial — no functional tests for build/deploy/audit tools          |
| VS Code / Claude config examples |   ❌    | No documentation                                                    |
| AI-assisted deployment workflow  |   ❌    | No example guide                                                    |

---

## Design

### 1. `_run_command` — Response Envelope Fix

**Problem:** `_run_command` returns only `cmd._output_data`. When a command fails, errors are in `cmd._errors` and `_output_data` is an empty dict — a silent failure invisible to the MCP caller.

**Fix in `mcp/server.py`:**
```python
def _run_command(cmd: Any) -> Dict[str, Any]:
    """Execute a BaseCommand and return a structured envelope."""
    cmd.execute()
    return {
        "success": not cmd.has_errors(),
        "data": cmd._output_data,
        "errors": cmd.get_errors(),
        "messages": cmd.get_messages(),
    }
```

Every MCP tool response becomes self-describing. The AI checks `success` and reads `errors` without extra logic.

---

### 2. `audit_query` Tool

**Rationale:** `strata/audit` is an explicit acceptance criterion in the issue. The underlying `AuditController.query_deploy_logs()` already exists and is used by `strata audit changes`.

```python
@mcp.tool()
def audit_query(
    work_path: Optional[str] = None,
    last: int = 20,
    since: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Query deploy-log entries from workspace audit logs.

    Args:
        work_path: Workspace root. Defaults to CWD.
        last: Maximum number of entries to return (default 20).
        since: ISO timestamp — return only entries after this time.
        stage: Filter to entries that ran a specific stage name.

    Returns {"success": true, "entries": [...], "count": N}.
    Each entry has: timestamp, deployment, success, duration_seconds, stages[].
    """
    from pathlib import Path

    from strata.controllers.audit_controller import AuditController
    from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

    wp = Path(_work_path(work_path))
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
    controller = AuditController(work_path=wp)
    entries = controller.query_deploy_logs(
        base_path=base_path,
        since=since,
        stage=stage,
        last=last,
    )
    entries_data = [e.model_dump(exclude_none=True) for e in entries]
    return {"success": True, "entries": entries_data, "count": len(entries_data)}
```

---

### 3. `deploy_history` Tool

Wraps `HistoryDeployCommand` — scans `.strata/logs/` for deploy events without requiring a deployment YAML.

```python
@mcp.tool()
def deploy_history(
    work_path: Optional[str] = None,
    lines: int = 20,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    """Return recent deployment execution history.

    Args:
        work_path: Workspace root. Defaults to CWD.
        lines: Maximum history entries to return (default 20).
        operation: Filter by operation type — "run" or "destroy".
    """
    from strata.commands.deploy.history_deploy_command import HistoryDeployCommand

    cmd = HistoryDeployCommand(
        work_path=_work_path(work_path),
        lines=lines,
        operation=operation,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)
```

---

### 4. `deploy_status` Tool

Wraps `StatusDeployCommand` — returns live Terraform outputs for provisioned stages.

```python
@mcp.tool()
def deploy_status(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Return live infrastructure outputs (Terraform) for a deployment.

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
        stage: Limit output to a specific stage name.
    """
    from strata.commands.deploy.status_deploy_command import StatusDeployCommand

    cmd = StatusDeployCommand(
        file=file,
        work_path=_work_path(work_path),
        stage=stage,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)
```

---

### 5. `deploy_health` Tool

Wraps `HealthDeployCommand` — runs HTTP/TCP health checks against provisioned stages.

```python
@mcp.tool()
def deploy_health(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Run health checks against provisioned deployment stages.

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
        stage: Limit checks to a specific stage name.

    Returns pass/fail per check and an overall success flag.
    """
    from strata.commands.deploy.health_deploy_command import HealthDeployCommand

    cmd = HealthDeployCommand(
        file=file,
        work_path=_work_path(work_path),
        stage=stage,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)
```

---

### 6. `build_sbom` Tool

Wraps `SbomBuildCommand` — generates SBOM or inventory report.

```python
@mcp.tool()
def build_sbom(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    scan: Optional[str] = None,
    report: str = "inventory",
) -> Dict[str, Any]:
    """Generate an SBOM or dependency inventory for a deployment.

    Args:
        file: Path to the deployment YAML (standard mode).
        work_path: Workspace root. Defaults to CWD.
        scan: Directory to scan directly (no deployment file required).
        report: "cyclonedx" (default) or "inventory" for human-readable output.
    """
    from strata.commands.builders.sbom_build_command import SbomBuildCommand

    cmd = SbomBuildCommand(
        file=file,
        work_path=_work_path(work_path),
        scan_path=scan,
        report=report,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)
```

---

### 7. Authentication for SSE Transport (Deferred)

**Decision:** Authentication via API key at the application level for SSE is deferred to a post-v1 release.

**Rationale:**
- The primary MCP use case (VS Code, Claude Desktop) uses **stdio** — the process is invoked locally with no network exposure.
- SSE is intended for shared/remote deployments. Users who expose SSE externally should terminate auth at the infrastructure layer (nginx `auth_request`, traefik ForwardAuth, API Gateway), not in the CLI process.
- FastMCP's `mcp.run(transport="sse")` does not provide a stable ASGI middleware injection API as of MCP SDK v1.x.

**Post-v1 approach:** Add `--api-key` / `STRATA_MCP_API_KEY` to `mcp serve`. When set on SSE transport, inject a Starlette `BaseHTTPMiddleware` that validates the `X-API-Key` request header and returns HTTP 401 on mismatch.

---

### 8. Tests

**Additions to `tests/strata/commands/test_commands_mcp.py`:**

Presence tests (pattern: `test_server_has_<tool>_tool`):
- `audit_query`
- `deploy_history`
- `deploy_status`
- `deploy_health`
- `build_sbom`

Functional tests (mocked):
- `test_audit_query_returns_entries` — mock `AuditController.query_deploy_logs`
- `test_deploy_history_calls_command` — mock `HistoryDeployCommand.execute`
- `test_run_command_envelope_on_success` — verify `{success: True, data: ..., errors: [], messages: []}`
- `test_run_command_envelope_on_failure` — verify `{success: False, errors: ["..."]}`

---

### 9. Documentation (`docs/platform/mcp.md`)

Sections to add:

- **Installation** — `pip install xyz-strata[mcp]`
- **Quick start** — `strata mcp serve`
- **VS Code configuration** (`mcp.json`)
- **Claude Desktop configuration** (`claude_desktop_config.json`)
- **Tool reference** — all tools with args/return
- **AI-assisted deployment workflow** — example session
- **SSE deployment** — production setup with reverse proxy auth

#### VS Code `mcp.json` example
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

#### Claude Desktop `claude_desktop_config.json` example
```json
{
  "mcpServers": {
    "strata": {
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve"],
      "cwd": "/path/to/workspace"
    }
  }
}
```

---

## Implementation Order

| #   | Task                                 | File(s)                       | Risk   |
| --- | ------------------------------------ | ----------------------------- | ------ |
| 1   | Fix `_run_command` envelope          | `mcp/server.py`               | Low    |
| 2   | Add `audit_query` tool               | `mcp/server.py`               | Low    |
| 3   | Add `deploy_history` tool            | `mcp/server.py`               | Low    |
| 4   | Add `deploy_status` tool             | `mcp/server.py`               | Low    |
| 5   | Add `deploy_health` tool             | `mcp/server.py`               | Low    |
| 6   | Add `build_sbom` tool                | `mcp/server.py`               | Low    |
| 7   | Update tests (presence + functional) | `test_commands_mcp.py`        | Low    |
| 8   | Finalize docs                        | `docs/platform/mcp.md`        | Low    |
| 9   | *(post-v1)* API key auth for SSE     | `mcp/server.py`, `cli_mcp.py` | Medium |

All items 1–8 are independent — they can be implemented in parallel or in any order. Item 1 (envelope fix) should land first as all other tool tests depend on the correct return structure.
