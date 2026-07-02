# VS Code Integration Strategy

How strata can meet users where they already are — in VS Code, talking to an AI assistant.

---

## The interaction model

Most strata users will never read the docs. Their workflow is:

1. Open VS Code in a workspace
2. Ask Copilot / Claude: *"help me deploy this"*
3. Expect it to just work

Every integration layer we add reduces the round-trips between the user, the AI, and the CLI. The layers build on each other:

```
Layer 0: CLI only             ← user memorizes commands
Layer 1: Prompt templates     ← user triggers workflows via /prompt
Layer 2: YAML schema wiring   ← editor catches mistakes before the AI does
Layer 3: AI context command    ← AI understands workspace in one call
Layer 4: MCP server            ← AI calls strata as a native tool (no CLI parsing)
Layer 5: VS Code extension     ← full IDE integration (tree views, diagnostics, CodeLens)
```

---

## Layer 1: Prompt Templates (`.prompt.md`)

Invokable from Copilot Chat. Users type `/` and see strata workflows.

Scaffold these into `.github/prompts/` during `sln init`.

### Proposed prompts

| File                            | Trigger                | What it does                                                           |
| ------------------------------- | ---------------------- | ---------------------------------------------------------------------- |
| `strata-init.prompt.md`         | `/strata-init`         | Walk through `sln init` → `repo add` → `profile create` → `guide show` |
| `strata-new.prompt.md`          | `/strata-new`          | Ask which kind, scaffold YAML, validate, register                      |
| `strata-deploy.prompt.md`       | `/strata-deploy`       | Validate → build dry-run → build → deploy dry-run → deploy             |
| `strata-troubleshoot.prompt.md` | `/strata-troubleshoot` | Run `guide show --output json`, parse failures, suggest fixes          |
| `strata-explain.prompt.md`      | `/strata-explain`      | Explain a YAML file's purpose, validate it, show what it connects to   |
| `strata-status.prompt.md`       | `/strata-status`       | Show workspace readiness, active profile, pending actions              |

### Example: `strata-deploy.prompt.md`

```markdown
---
description: "Run the full strata deploy pipeline: validate → build → deploy"
mode: agent
tools:
  - execute
  - read
  - search
---

You are helping the user deploy using strata. Follow this exact sequence:

1. Run `strata ai context --output json` to understand the workspace state
2. Ask which deployment file to use (or detect from context)
3. Run `strata validate <file>` — if errors, fix them first
4. Run `strata build run -f <file> --dry-run` — show what would be generated
5. Ask user to confirm, then `strata build run -f <file>`
6. Run `strata deploy run -f <file> --dry-run` — show what would change
7. Ask user to confirm, then `strata deploy run -f <file>`
8. Report final status
```

---

## Layer 2: YAML Schema Wiring

`strata schema export` already writes JSON schemas to `.strata/schemas/`. Wire them to the VS Code YAML extension so users get **autocomplete and inline validation without asking the AI**.

### Auto-generate `.vscode/settings.json` mapping

Add a `strata schema wire` subcommand that writes:

```json
{
  "yaml.schemas": {
    ".strata/schemas/workspace.schema.json": "**/workspace.yaml",
    ".strata/schemas/configuration.schema.json": ["config/**/*.yaml", "**/configuration.yaml"],
    ".strata/schemas/deployment.schema.json": ["deploy/**/*.yaml", "**/deployment.yaml"],
    ".strata/schemas/environment.schema.json": ["environments/**/*.yaml", "**/environment*.yaml"],
    ".strata/schemas/module.schema.json": ["modules/**/*.yaml", "**/module*.yaml"],
    ".strata/schemas/namespace.schema.json": ["namespaces/**/*.yaml", "**/namespace*.yaml"],
    ".strata/schemas/provider.schema.json": ["providers/**/*.yaml", "**/provider*.yaml"],
    ".strata/schemas/resource.schema.json": ["resources/**/*.yaml", "**/resource*.yaml"],
    ".strata/schemas/firewall.schema.json": ["firewalls/**/*.yaml", "**/firewall*.yaml"],
    ".strata/schemas/network.schema.json": ["networks/**/*.yaml", "**/network*.yaml"],
    ".strata/schemas/dns.schema.json": ["**/dns*.yaml"],
    ".strata/schemas/tenant.schema.json": ["tenants/**/*.yaml", "**/tenant*.yaml"]
  }
}
```

### What this gives users for free

- Red squiggles on unknown fields (catches `extra="forbid"` errors before running validate)
- Autocomplete for `kind`, `apiVersion`, `meta.name`, and every `spec` field
- Hover documentation from schema `description` fields
- Inline enum suggestions (e.g., valid `kind` values, provisioner names)

### Requirements

- JSON schemas must include `description` on every field (drives hover docs)
- Schemas must use `$ref` for shared types so changes propagate
- `strata schema export` should run automatically during `sln init` and `build run`
- Recommend `redhat.vscode-yaml` extension in `.vscode/extensions.json`

---

## Layer 3: Workspace State in One Call

The AI agent needs to understand the workspace in a single call before taking any action.

**Decision:** Rather than a new `strata ai context` command (which would duplicate `sln status` and `guide show`), the `readiness` block from the guide is merged directly into `strata sln status --output json`.

```bash
strata sln status --output json
```

### Output structure

```json
{
  "health": { "status": "HEALTHY|DEGRADED|BROKEN", "issues": [] },
  "solution": {
    "initialized": true,
    "work_path": "/home/user/project",
    "id": "uuid",
    "name": "my-workspace"
  },
  "readiness": {
    "phases_complete": 5,
    "phases_total": 8,
    "complete": false,
    "checklist": [
      { "phase": 1, "label": "Workspace initialized", "status": "ok", "detail": null },
      { "phase": 2, "label": "Repositories registered", "status": "ok", "detail": "2 repos" },
      { "phase": 6, "label": "Build artifacts ready", "status": "pending", "detail": null }
    ],
    "next_step": {
      "phase": 6,
      "label": "Build artifacts ready",
      "hint": "strata build run -f deploy/main.yaml",
      "see_also": null
    }
  },
  "profiles": { "active": "dev", "all": ["dev", "prd"], "paths": { ... } },
  "repositories": [ ... ],
  "integrations": { "terraform": { "available": true, "version": "1.9.0" }, ... }
}
```

### Why this is better than a separate `ai context` command

`sln status` already returned workspace, profiles, repositories, and integrations. The guide checklist + next_step was the only missing piece. Merging them:
- One command instead of two — the agent.md says "start with `strata sln status --output json`"
- No new CLI surface area to maintain
- Both pieces stay in sync naturally
- `readiness.next_step.hint` gives the AI the exact command to suggest next

---

## Layer 4: MCP Server

Expose strata operations as Model Context Protocol tools. The AI calls strata natively — no CLI parsing, no output scraping.

### Configuration

In `.vscode/mcp.json` or `.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "strata": {
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

### Proposed tools

| Tool                | Parameters             | Returns                                            |
| ------------------- | ---------------------- | -------------------------------------------------- |
| `workspace_status`  | —                      | Workspace state, readiness, profiles, repos        |
| `validate_file`     | `path`                 | Structured errors with field paths and suggestions |
| `validate_all`      | —                      | All validation errors across workspace             |
| `get_schema`        | `kind`                 | JSON schema for a document kind                    |
| `list_schemas`      | —                      | Available kinds with descriptions                  |
| `scaffold_file`     | `kind`, `name`, `path` | Generated YAML content                             |
| `build_plan`        | `deployment_file`      | What would change (dry-run)                        |
| `build_run`         | `deployment_file`      | Build result with artifacts list                   |
| `deploy_plan`       | `deployment_file`      | What would be deployed (dry-run)                   |
| `deploy_run`        | `deployment_file`      | Deployment result                                  |
| `guide_show`        | —                      | 8-phase readiness checklist                        |
| `explain_file`      | `path`                 | Kind, purpose, relationships, validation           |
| `resolve_reference` | `ref`                  | Resolved `@repo/path` reference                    |
| `list_profiles`     | —                      | Profiles with active flag                          |
| `search_documents`  | `kind`, `name_pattern` | Find documents by kind/name                        |

### Proposed resources (read-only context)

| Resource          | URI                       | Description                   |
| ----------------- | ------------------------- | ----------------------------- |
| Solution registry | `strata://solution`       | Current solution.json content |
| Active profile    | `strata://profile/active` | Active profile details        |
| Workspace guide   | `strata://guide`          | Current readiness state       |
| Document schema   | `strata://schema/{kind}`  | JSON schema for a kind        |

### Implementation approach

The MCP server is a thin wrapper around existing services and controllers:

```python
# src/strata/mcp/server.py
from mcp.server import Server
from strata.services.solution_service import SolutionService
from strata.commands.guide.show_guide_command import ShowGuideCommand

server = Server("strata")

@server.tool("workspace_status")
async def workspace_status(work_path: str) -> dict:
    """Return workspace state for AI consumption."""
    # Reuse existing service layer — no new logic
    solution = SolutionService.load(work_path)
    guide = ShowGuideCommand()
    guide.execute(work_path=work_path, output_format="json")
    return guide.result
```

### What this changes for the user

Before MCP:
```
User: "deploy my app"
AI: [runs strata guide show] [parses emoji output] [runs strata validate] [parses text errors]
    [runs strata build run] [reads terminal output] [runs strata deploy run] [reads more output]
    "Done! I think it worked."
```

After MCP:
```
User: "deploy my app"
AI: [calls workspace_status] → knows state
    [calls validate_all] → knows errors
    [calls build_run] → gets structured result
    [calls deploy_run] → gets structured result
    "Deployed successfully. 3 resources created, 1 updated."
```

---

## Layer 5: VS Code Extension

A dedicated `strata` VS Code extension adds visual integration that no CLI or MCP layer can provide.

### What the extension unlocks

#### Tree View: Workspace Explorer

A sidebar panel showing the live workspace structure:

```
STRATA WORKSPACE
├── 📋 Solution: my-project
│   ├── Profile: dev (active) ✅
│   └── Profile: prd
├── 📦 Repositories
│   ├── infra (repos/infra) — main
│   └── modules (repos/modules) — main
├── 📄 Documents
│   ├── ✅ config/main.yaml (configuration)
│   ├── ✅ environments/dev.yaml (environment)
│   ├── ⚠️ modules/api.yaml (module) — 2 warnings
│   └── ❌ deploy/main.yaml (deployment) — 1 error
└── 🔧 Tools
    ├── ✅ terraform (1.9.0)
    ├── ✅ docker (27.0.1)
    └── ❌ helm (not found)
```

- Click a document → opens the file
- Right-click → Validate / Build / Deploy
- Refresh button → re-reads workspace state
- Badge count on the panel icon showing total errors

#### Diagnostics Provider (Squiggly Lines)

Real-time validation as users type, powered by `strata validate`:

- Red squiggles on invalid field names (`extra="forbid"`)
- Yellow squiggles on warnings (deprecated fields, missing optional best-practices)
- Hover shows the error message + link to schema docs
- Quick Fix actions: "Did you mean `provisioner`?" / "Remove unknown field"
- Runs on file save (or on-type with debounce)

This is more powerful than YAML schema validation because it includes strata's Phase 2 dynamic validation (cross-file references, repo resolution, profile checks).

#### CodeLens

Inline actions above YAML documents:

```yaml
# [Validate] [Build] [Deploy] [Schema] [Explain]     ← clickable CodeLens
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: main
```

- **Validate** — runs `strata validate <file>`, shows results in Problems panel
- **Build** — runs `strata build run -f <file>` in terminal
- **Schema** — opens the JSON schema for this kind in a side panel
- **Explain** — shows a hover panel with what this file does, what depends on it

#### Status Bar

Persistent status bar items:

```
$(cloud) strata: dev | Phase 5/8 | 2 errors
```

- Shows active profile name
- Shows readiness phase
- Shows error count (click to open Problems panel)
- Click profile name → quick-pick to switch profiles

#### Walkthrough (Getting Started)

A VS Code Walkthrough that appears when a workspace has no `.strata/` directory:

```
Welcome to Strata
─────────────────
Step 1: Initialize workspace          [Run strata sln init]
Step 2: Add a configuration repo      [Run strata repo add]
Step 3: Create a profile              [Run strata profile create]
Step 4: Scaffold your first config    [Run strata new configuration]
Step 5: Validate and build            [Run strata build run]

Progress: ████░░░░░░ 2/5
```

Each step has a description, a button to run the command, and a checkmark when complete.

#### File Decorations

Show validation status directly in the Explorer file tree:

```
📁 config/
  main.yaml          ✅
  network.yaml       ⚠️ (2 warnings)
📁 deploy/
  production.yaml    ❌ (1 error)
📁 environments/
  dev.yaml           ✅
  staging.yaml       (not validated)
```

#### Go-to-Definition for Cross-References

`@repo_name/path/to/file.yaml` references become clickable:

- Ctrl+Click on `@infra/modules/api.yaml` → opens the file
- Hover shows the resolved absolute path and file kind
- F2 rename propagates across files

#### Snippet Provider

Context-aware YAML snippets triggered by `kind:` value:

- Type `strata:deployment` → inserts full deployment boilerplate
- Type `strata:module` → inserts module boilerplate with required fields
- Snippets are generated from JSON schemas, so they stay in sync

#### Command Palette

All strata operations available via `Ctrl+Shift+P`:

```
Strata: Initialize Workspace
Strata: Validate Current File
Strata: Validate All Files
Strata: Build (Dry Run)
Strata: Build
Strata: Deploy (Dry Run)
Strata: Deploy
Strata: Show Guide
Strata: Switch Profile
Strata: Export Schemas
Strata: Open Console
Strata: Show Dependency Graph
```

#### Webview: Dependency Graph

A visual panel showing how documents relate to each other:

```
[configuration] ──→ [environment] ──→ [deployment]
       │                                    │
       ├──→ [module]                        ├──→ [stage: infra]
       ├──→ [namespace]                     └──→ [stage: services]
       ├──→ [provider]
       └──→ [resource]
```

Rendered with D3.js or Mermaid inside a webview panel. Clicking a node opens the file.

#### Notifications

- After `build run` → notification: *"Build complete. 12 artifacts generated."*
- After `deploy run` → notification: *"Deployment to dev succeeded. 3 resources created."*
- On validation error in background → notification: *"2 new validation errors in deploy/main.yaml"*

#### Task Provider

Auto-discovers strata operations and registers them as VS Code tasks:

```json
{
  "type": "strata",
  "command": "build",
  "file": "deploy/main.yaml",
  "profile": "dev",
  "dryRun": true
}
```

Users can bind keyboard shortcuts or add to `.vscode/tasks.json`.

#### Extension settings

```json
{
  "strata.validateOnSave": true,
  "strata.validateOnType": false,
  "strata.autoExportSchemas": true,
  "strata.showCodeLens": true,
  "strata.showStatusBar": true,
  "strata.defaultProfile": "",
  "strata.cliPath": "strata"
}
```

#### Chat participation

- The extension can participate in Copilot Chat or Claude chat sessions

---

## Implementation Priority

| Priority | Layer                                   | Effort    | Impact    | Status      |
| -------- | --------------------------------------- | --------- | --------- | ----------- |
| **P0**   | Prompt templates                        | 1–2 days  | High      | ✅ Done      |
| **P0**   | YAML schema wiring (`schema wire`)      | 1 day     | High      | ✅ Done      |
| **P1**   | Readiness in `sln status --output json` | 1 day     | High      | ✅ Done      |
| **P1**   | Enrich `strata.agent.md` template       | 1 day     | Medium    | ✅ Done      |
| **P2**   | MCP server (core tools)                 | 1–2 weeks | Very high | ✅ Done      |
| **P3**   | VS Code extension (tree + diagnostics)  | 2–3 weeks | Very high | Not started |
| **P3**   | VS Code extension (CodeLens + status)   | 1 week    | High      | Not started |
| **P4**   | VS Code extension (webviews + graph)    | 2 weeks   | Medium    | Not started |

### Recommended sequence

```
Phase 1 (now):     Prompts + Schema wiring + ai context command
Phase 2 (next):    MCP server with core tools
Phase 3 (later):   VS Code extension consuming MCP
```

The extension should consume the MCP server — not call the CLI directly. This way the MCP server is useful for any AI tool (Cursor, Windsurf, terminal agents), and the extension gets structured data for free.

### Why not an HTTP server?

A dedicated HTTP server (`strata serve`) was considered and rejected. The two existing surfaces already cover every consumer:

| Surface             | Transport           | Consumer                            | Persistent?        |
| ------------------- | ------------------- | ----------------------------------- | ------------------ |
| CLI + JSON envelope | subprocess / stdout | VS Code extension, scripts, CI      | No (cold per call) |
| MCP server          | stdio / SSE         | AI agents (Copilot, Claude, Cursor) | Yes                |

An HTTP server would be a third integration surface that overlaps with both:

- **For AI tools** — MCP already provides structured tool access. HTTP would require additional auth, CORS, port discovery, and process lifecycle management — all solved problems in MCP.
- **For the extension** — subprocess calls work fine. Switching to HTTP means managing port allocation, startup ordering (server must be up before extension calls it), and health checks. The current `execFile` approach is simpler and self-contained.
- **For CI/scripts** — CLI is the natural interface. Nobody wants to spin up a server to run `strata validate`.

The one scenario where HTTP would genuinely help is a **browser-based web UI** (e.g., a deployment dashboard at `localhost:9000`). That's a separate product decision, not something the extension or MCP layer needs.

If subprocess cold-start ever becomes a bottleneck, the extension can switch to consuming the MCP server directly — persistent process, structured data, no new surface.

---

## Scaffolding changes to `sln init`

When `strata sln init` creates a workspace, scaffold these AI integration files:

```
.github/
  agents/
    strata.agent.md              ← already exists
  prompts/
    strata-init.prompt.md
    strata-new.prompt.md
    strata-deploy.prompt.md
    strata-troubleshoot.prompt.md
    strata-status.prompt.md
    strata-explain.prompt.md
.vscode/
  settings.json                  ← with yaml.schemas mapping
  extensions.json                ← recommend redhat.vscode-yaml
.strata/
  schemas/                       ← auto-exported JSON schemas
```

This way every new workspace is AI-ready from day one.
