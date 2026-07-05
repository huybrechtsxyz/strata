# MCP Server Integration Guide for AI Agents

The **strata MCP server** exposes infrastructure operations as **Model Context Protocol tools**, enabling AI assistants (Claude, GitHub Copilot, etc.) to programmatically validate, build, and preview infrastructure deployments. This guide covers setup, security, and real-world integration patterns.

## Quick Start

**Install the optional MCP dependency:**
```bash
pip install xyz-strata[mcp]
```

**Start the server** (communicates via stdio):
```bash
strata mcp serve
```

**Configure your AI client** to use the strata MCP server, then ask:
> "Validate my deployment YAML and show me what would be deployed."

The AI now has access to 13 specialized tools and 2 auto-loaded resources.

---

## What's Possible with MCP

| Scenario         | Workflow                                       | Executor                                                       |
| ---------------- | ---------------------------------------------- | -------------------------------------------------------------- |
| **Setup**        | AI scaffolds YAML from template                | AI tool: `scaffold_file()`                                     |
| **Validation**   | AI validates YAML structure & cross-references | AI tool: `validate_file()`                                     |
| **Preview**      | AI shows what build/deploy _would_ produce     | AI tool: `build_plan()`, `deploy_plan()`                       |
| **Execute**      | User approves and runs via CLI                 | **User via CLI only**                                          |
| **Monitor**      | AI checks health, status, and audit logs       | AI tool: `deploy_status()`, `audit_query()`                    |
| **Troubleshoot** | AI analyzes drift and suggests remediation     | AI tool: `deploy_status()`, `audit_query()`, `validate_file()` |

## Key Principles

1. **AI previews, humans execute**
   - MCP tools provide dry-runs and planning only
   - Actual deployments happen via CLI with explicit user confirmation
   - Audit logs track all operations

2. **Local-first security**
   - MCP server runs locally via stdio (no network exposure)
   - Secrets are never transmitted via MCP
   - Workspace permissions are enforced by the OS

3. **Full audit trail**
   - `audit_query()` tracks all deploy operations
   - `deploy_history()` shows recent executions
   - Use with approval workflows for compliance

---

## Documentation Map

| Chapter                                                       | Topic                                                                            |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [Setup & Installation](setup-and-installation.md)             | Prerequisites, installation, server startup, workspace resolution                |
| [Claude Deployment Assistant](claude-deployment-assistant.md) | Full end-to-end example: setup Claude, validate, build, preview, deploy workflow |
| [Copilot Integration](copilot-integration.md)                 | Ideas for enhancing GitHub Copilot extension with MCP-based validation           |
| [Security & Workflows](security-and-workflows.md)             | API keys, secrets management, audit logging, approval gates, RBAC patterns       |
| [AI-Assisted Troubleshooting](ai-troubleshooting.md)          | Use case: detect drift, analyze changes, suggest fixes via MCP tools             |
| [Tools Reference](tools-reference.md)                         | Complete API documentation for all 13 MCP tools                                  |

---

## Available Tools

### Workspace & Schema
- **`workspace_status()`** — Full workspace state, readiness phases, active profile
- **`list_schemas()`** — All supported document kinds
- **`get_schema(kind)`** — JSON Schema for a kind

### Authoring
- **`scaffold_file(kind, name)`** — Generate YAML from template
- **`validate_file(file_path, deep=False)`** — Validate YAML against schema

### Build Pipeline
- **`build_plan(file)`** — Preview build output (dry-run)
- **`build_run(file)`** — Execute build and generate artifacts
- **`build_sbom(file, report="inventory")`** — Generate SBOM or dependency inventory

### Deployment
- **`deploy_plan(file, stage=None)`** — Preview deploy changes (dry-run)
- **`deploy_status(file, stage=None)`** — Current infrastructure state (Terraform outputs)
- **`deploy_health(file, stage=None)`** — Run health checks against deployed stages

### Monitoring & Diagnostics
- **`deploy_history(lines=20, operation=None)`** — Recent deployment history
- **`audit_query(since=None, stage=None, last=20)`** — Query deployment logs

---

## Who Should Read This?

- **AI engineers** — Building Claude or Copilot integrations with strata
- **DevOps teams** — Setting up AI-assisted deployment approval workflows
- **Platform engineers** — Using MCP for programmatic infrastructure queries
- **Security architects** — Understanding audit, secrets management, and approval gates

---

## Prerequisites

- **strata CLI** installed and working (`strata --version`)
- **Python 3.10+** (same as strata)
- **MCP optional dependency** (`pip install xyz-strata[mcp]`)
- **Workspace** with `.strata/` initialized (`strata sln init`)

---

## Next Steps

1. **[Install & Start](setup-and-installation.md)** the MCP server
2. **[Try the Claude example](claude-deployment-assistant.md)** (15 min walkthrough)
3. **Review [Security & Workflows](security-and-workflows.md)** before production use
4. **Explore [Troubleshooting](ai-troubleshooting.md)** for operational patterns

---

## Support & Examples

- **GitHub Issues**: [strata issues](https://github.com/huybrechtsxyz/strata/issues)
- **Example configs**: `config/` directory in strata repository
- **Related features**: See [VS Code Extension](../vscode/README.md) for editor integration
