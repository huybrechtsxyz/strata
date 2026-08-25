# Features

## Activity Bar

The extension adds a Strata icon to the Activity Bar on the left. Click it to open the Strata panel.

### Status Bar

At the bottom of the editor, a status bar item shows:

```
$(cloud) strata: dev | Phase 5/8    ← healthy workspace
$(warning) strata: 2 issues          ← degraded (warnings/errors)
$(error) strata: broken              ← broken (major issues)
```

Click it to open the Guide panel. The icon changes color based on workspace health.

## Tree Views

### Workspace

Shows workspace health, active profile, readiness, and repositories.

- **Health** — current status (🟢 HEALTHY, 🟡 DEGRADED, 🔴 BROKEN)
- **Profile** — active profile and list of all profiles
- **Readiness** — phases complete, next step with command hint
- **Repositories** — list of registered configuration repositories

### Files

All strata YAML documents grouped by kind with validation badges:

```
📄 Configurations
  config/main.yaml     ✅ (validated, no issues)
  config/test.yaml     ⚠️ (2 warnings)
📄 Deployments
  deploy/prd.yaml      ❌ (1 error)
```

Click a file to open it. Right-click for context menu (validate, build, deploy, show schema).

### Repositories

List of configured configuration repositories with clone status, branch, and URL.

```
📦 infra
    Branch: main
    Status: ✅ cloned
📦 modules
    Branch: dev
    Status: ⚠️ not cloned
```

### Tools

Availability and version of external tools (terraform, docker, helm, etc.):

```
🟢 terraform    1.9.0
🟢 docker       27.0.1
🔴 helm         (not found)
```

## Command Palette

Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac) to open the Command Palette. Type "strata" to see all available commands:

- **Strata: Initialize Workspace** — `strata sln init`
- **Strata: Validate Current File** — validate active editor file
- **Strata: Validate All Files** — scan workspace and validate all strata YAML
- **Strata: Build (Dry Run)** — `strata build run --dry-run`
- **Strata: Build** — `strata build run`
- **Strata: Deploy (Dry Run)** — `strata deploy run --dry-run`
- **Strata: Deploy** — `strata deploy run`
- **Strata: Show Guide** — open readiness checklist
- **Strata: Switch Profile** — quick-pick to activate a different profile
- **Strata: Export Schemas** — export and wire JSON schemas to `.vscode/settings.json`
- **Strata: Open Console** — open strata interactive console
- **Strata: Show Dependency Graph** — visualize document relationships and infrastructure
- **Strata: List Diagrams** — show all available diagrams (built-in + workspace)

## Diagnostics (Problems Panel)

Real-time validation runs on:

- **File save** — always (if `validateOnSave: true`)
- **While typing** — debounced 1.5s (if `validateOnType: true`)
- **On demand** — Command Palette → "Strata: Validate Current File"

Errors appear as red squiggles in the editor and in the **Problems** panel (`Ctrl+Shift+M`).

Each error shows:

- The exact field path (e.g., `spec.provisioner`)
- The error message
- The value that caused it (if known)
- Phase information (Phase 1 = structural, Phase 2 = cross-reference)

**Quick fixes** (when available):

- "Did you mean `provisioner`?" for typos
- "Remove unknown field" for extra fields (due to `extra="forbid"`)

## CodeLens

Inline actions appear above YAML documents:

```yaml
# [Validate] [Schema] [Build DryRun] [Deploy DryRun] [Guide]
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
```

- **Validate** — runs `strata validate` and shows results
- **Schema** — opens JSON schema for this `kind` in a side panel
- **Build DryRun** — runs `strata build run --dry-run` in terminal
- **Deploy DryRun** — runs `strata deploy run --dry-run` in terminal
- **Guide** — opens the readiness checklist

## File Decorations

Validation badges appear directly in the Explorer file tree:

```
✓  config/main.yaml         ← validated, no issues
⚠️  config/test.yaml        ← warnings
❌ deploy/prd.yaml         ← errors
```

Badges propagate to parent folders, so a single error in a deeply nested file bubbles up to the root.

Toggle this feature with `strata.showFileDecorations` setting.

## Hover and Go-to-Definition

**Cross-references** (`@repo_name/path/file.yaml`) are interactive:

- **Hover** — shows resolved absolute path and document kind
- **Ctrl+Click** — opens the referenced file
- **F2** — rename the reference across all files

## Snippet Provider

When editing a YAML file, type `strata:` to trigger snippets:

```
strata:workspace     → full workspace boilerplate
strata:configuration → configuration document
strata:deployment    → deployment document
strata:module        → module (with helm/compose/script choice)
...
```

Snippets are generated from JSON schemas, so they stay in sync with your version.

## Diagram Visualization

The extension renders interactive Mermaid diagrams for any workspace data via `strata diagram show`.

### Built-in diagrams
- **topology** — resource topology, module dependencies, cross-references
- **refs** — file cross-references and relationships

### Custom diagrams
Create `.strata/diagrams/<name>.yaml` files to visualize:

**Workspace structure:** Topology, Files, Resources, Modules, Namespaces, Networks, Firewalls, DNS zones  
**Deployment details:** Stages, Environments, Tenants, Policies  
**History:** Execution records, Promotions, Approval gates  
**Configuration:** Variables, Secrets, Feature flags, Values (metadata only, values never exposed)  
**Live state:** Drift tracking, Deployment outputs, Deployment locks  
**Tooling:** Repositories, Software Bill of Materials (SBOM)

**Example diagram:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: diagram
meta:
  name: deployment-flow
spec:
  sources:
    - type: stages
    - type: environments
  layout: flowchart TD
  style:
    color_by: kind
```

Nodes are color-coded by status. Click a node to open its source file. VS Code shows the diagram inline; the CLI renders it as SVG or Mermaid source.

### Dependency Graph

The "Show Dependency Graph" command is a shorthand for the built-in `refs` diagram — visualizing all `@repo/path` cross-references in your workspace.

Nodes are color-coded by kind:
- 🔷 configuration
- 🔶 deployment
- 🔸 module
- etc.

Edges show `@repo/path` references. Click a node to open the file.

## Chat Integration

In Copilot Chat, type `@strata` followed by:

- **Freeform question** — e.g., `@strata what's the current status?` → LLM gets workspace context
- **/status** — table with health, profile, readiness, issues, next step
- **/validate** — validate current or named file
- **/guide** — show the 8-phase readiness checklist
- **/build** — list deployment files with build commands
- **/deploy** — list deployment files with deploy commands

Follow-ups are suggested after each response.

## Auto-Refresh

The extension watches `.strata/solution.json` for external changes. If the file is created, modified, or deleted outside VS Code (e.g., from the CLI in another terminal), all tree views, status bar, and other UI elements refresh automatically.

## Walkthrough

When a workspace has no `.strata/` directory, VS Code offers a Walkthrough (Help → Welcome → Walkthroughs). The walkthrough guides you through:

1. Initialize workspace
2. Wire YAML schemas
3. Check readiness

Each step has a button to run the command and a checkmark when complete.

## Task Provider

Strata operations are registered as VS Code tasks. Open `.vscode/tasks.json` to add custom tasks:

```json
{
  "type": "strata",
  "command": "validate",
  "file": "config/main.yaml"
}
```

or

```json
{
  "type": "strata",
  "command": "build",
  "file": "deploy/prd.yaml",
  "dryRun": true,
  "stage": "infrastructure"
}
```

Run with `Ctrl+Shift+B` (or via Command Palette → "Tasks: Run Task").
