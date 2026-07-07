# Strata VS Code Extension

<!-- markdownlint-disable MD033 -->

<div align="center">

**Integrated infrastructure workspace management for VS Code**

[Features](#features) • [Installation](#installation) • [Quick Start](#quick-start) • [Commands](#commands) • [Configuration](#configuration) • [Troubleshooting](#troubleshooting)

</div>

---

## Overview

The **Strata VS Code extension** brings the power of the Strata CLI into your editor, enabling you to validate, build, and deploy infrastructure configurations without leaving VS Code.

Strata solves the problem of managing multiple nearly-identical environments. Instead of copy-pasting Terraform folders for each environment (`dev`, `staging`, `prd`, etc.), Strata treats environments as **data**, not code. Define your infrastructure once in YAML, and deploy to any number of environments with a single command.

**What it does:**
- 🔍 **Validate** your YAML configuration in real-time with inline error highlighting
- 📊 **Explore** your infrastructure workspace with intelligent tree views (files, repositories, environments, audit trail)
- 🏗️ **Build** deployment artifacts without applying changes (dry-run mode)
- 🚀 **Deploy** to your infrastructure with one-click or CLI integration
- 🔗 **Monitor** drift between your configuration and live deployments
- 📜 **Track** all changes via integrated audit trail
- 💬 **Chat** with the Strata AI assistant to ask questions about your workspace

---

## Features

### Core Capabilities

- ✅ **Real-time YAML Validation**
  - Inline diagnostics with line numbers and fix suggestions
  - Validates against Kubernetes-style schema with 15+ model types
  - Cross-file reference resolution (@repo/path patterns)
  - Runs on save or on-type (configurable)

- 🗂️ **Workspace Explorer**
  - Workspace health & readiness status
  - Repository browser with git integration (latest release/quality-gate tags)
  - Environment management view
  - File browser with validation badges

- 🔨 **Build & Deploy**
  - Dry-run mode to preview changes before applying
  - Stage-based deployment (deploy one stage at a time)
  - Terraform plan visibility
  - Build artifact inspection

- 📋 **Multi-View Workspace Navigation**
  - **Workspace** — readiness checklist (8-phase onboarding guide)
  - **Files** — YAML files with validation badges
  - **Repositories** — git repos with tag metadata
  - **Environments** — deployment targets and drift status
  - **Tools** — terraform, ansible, git, docker availability
  - **Audit Trail** — deployment history and changes

- 💬 **Chat Integration**
  - Ask Strata assistant questions about your workspace
  - Get status, validation results, build/deploy instructions
  - Repository release status queries

- 🎯 **Code Lens**
  - Clickable "Validate", "Build", "Deploy" actions above YAML documents
  - Disabled by default; enable in settings

- 🔗 **Schema Wiring**
  - One-click schema export and VS Code integration
  - Enables autocomplete and validation in YAML editor
  - Automatic schema discovery for all Strata model types

- 📊 **Dependency Graph**
  - Visualize file references and cross-repo dependencies
  - Identifies missing or circular dependencies

### Advanced Features

- 🔐 **Secret Management**
  - Integrated with Azure Key Vault, HashiCorp Vault, Bitwarden, and others
  - Auto-generate secrets on first build
  - Safe handling of sensitive values in outputs

- 🔒 **Deployment Locking**
  - Prevents concurrent deployments
  - Configurable lock backends (local, Azure, Terraform Cloud, Consul)

- 🌍 **Multi-Environment Composition**
  - Merge environment files with override tracking
  - Built-in policy engine (12+ built-in policies)
  - Lifecycle hooks (27+ phases)

- 📦 **SBOM Generation**
  - Generate CycloneDX SBOMs from deployments
  - Scan dependencies across Python, Node.js, Go, .NET, Java, Ruby, Rust, PHP
  - Supply-chain compliance and inventory reporting

---

## Installation

### From VS Code Marketplace

1. Open **Extensions** in VS Code (`Ctrl+Shift+X`)
2. Search for **"Strata"**
3. Click **Install**

### From VSIX File (Development)

```bash
cd src/vscode
npm run compile          # Build the extension
vsce package           # Create .vsix file
# Install via Extensions panel > "Install from VSIX..."
```

### Prerequisites

- **VS Code** 1.90.0 or later
- **Strata CLI** installed (`strata` command must be in PATH)
  - Install via: `pipx install xyz-strata` or `uv run strata`
  - Or configure `strata.cliPath` in settings if in a non-standard location

---

## Quick Start

### 1. **Initialize a Strata Workspace**

```
View > Command Palette > "Strata: Initialize Workspace"
```

This creates `.strata/solution.json` in your workspace root and opens the getting-started walkthrough.

### 2. **Wire YAML Schemas**

```
View > Command Palette > "Strata: Export & Wire Schemas"
```

This enables autocomplete and inline validation for all `.yaml` files in your workspace.

### 3. **Check Workspace Readiness**

Open the **Strata** view in the activity bar (icon on the left sidebar):
- View the **Workspace** tab to see the 8-phase readiness checklist
- Follow the recommended steps in the **Guide**

### 4. **Validate Your Configuration**

1. Open any `.yaml` file in your workspace
2. The extension auto-validates on save (configurable)
3. Errors appear in the **Problems** panel with fix suggestions
4. Or run **"Strata: Validate Current File"** from the Command Palette

### 5. **Build & Deploy**

```
View > Command Palette > "Strata: Build (Dry Run)"
View > Command Palette > "Strata: Deploy (Dry Run)"
```

Or right-click in the editor and select **Strata: Build** or **Strata: Deploy**.

---

## Commands

### Main Commands

| Command                           | Shortcut | Purpose                                                       |
| --------------------------------- | -------- | ------------------------------------------------------------- |
| **Strata: Initialize Workspace**  | —        | Create `.strata/solution.json` and start the onboarding guide |
| **Strata: Validate Current File** | —        | Validate the active YAML file                                 |
| **Strata: Validate All Files**    | —        | Validate all YAML files in the workspace                      |
| **Strata: Build (Dry Run)**       | —        | Preview build artifacts without writing them                  |
| **Strata: Build**                 | —        | Generate build artifacts (Terraform, Helm, configs, etc.)     |
| **Strata: Deploy (Dry Run)**      | —        | Preview deployment changes without applying them              |
| **Strata: Deploy**                | —        | Apply deployment to your infrastructure                       |
| **Strata: Show Guide**            | —        | Display the 8-phase readiness checklist                       |
| **Strata: Switch Profile**        | —        | Activate a different environment profile                      |

### Environment & Diagnostics

| Command                             | Purpose                                                        |
| ----------------------------------- | -------------------------------------------------------------- |
| **Strata: Show Environment Status** | Check deployment status and recent changes                     |
| **Strata: Detect Drift**            | Compare Terraform state vs. current config                     |
| **Strata: Run Doctor**              | Health check: tools, auth, store connectivity, build freshness |
| **Strata: Show Audit Trail**        | View deployment history for the current file                   |

### Workspace Exploration

| Command                           | Purpose                                                  |
| --------------------------------- | -------------------------------------------------------- |
| **Strata: Show Dependency Graph** | Visualize YAML file references and dependencies          |
| **Strata: Export & Wire Schemas** | Enable autocomplete for YAML editor                      |
| **Strata: Open Console**          | Launch interactive Strata console in integrated terminal |

### Other

| Command             | Purpose                                             |
| ------------------- | --------------------------------------------------- |
| **Strata: Refresh** | Refresh all tree views (run in any Strata view tab) |

---

## Views

### Workspace View
Shows workspace health, readiness phases, and the active profile.

**Features:**
- Current profile name
- Phase checklist (Init → Configure → Validate → Build → Deploy → etc.)
- Recommended next actions
- Button to open the getting-started guide

### Files View
Lists all YAML files in the workspace with validation badges.

**Badges:**
- ✅ Valid
- ⚠️ Warning (non-blocking issues)
- ❌ Error (validation failed)

**Actions:**
- Click to open file
- Right-click > **Validate** to validate the file
- Right-click > **Show Status** to see detailed errors

### Repositories View
Shows all configured remote repositories with git metadata.

**Features:**
- Latest release tag (matches `v*.*.*` pattern)
- Latest quality-gate tag (if defined)
- Repository path and git origin
- Tag creation date and commit SHA

### Tools View
Checks availability of required external tools.

**Status indicators:**
- ✅ Available (with version)
- ❌ Not found / Not installed

**Tools checked:**
- terraform
- ansible
- git
- docker
- uv (Python package manager)

### Environments View
Displays deployments and their status.

**Features:**
- Deployment file path
- Current status (Deployed / Drifted / Unknown)
- Profile used
- Environment name
- Last deployment timestamp

**Actions:**
- Right-click > **Show Status** to fetch live deployment state
- Right-click > **Detect Drift** to check for infrastructure drift

### Audit Trail View
Displays deployment history with changeset details.

**Features:**
- Timestamp of each deployment
- Operation (build / deploy / destroy)
- Files changed
- Errors (if any)

**Actions:**
- Right-click > **Resend to SIEM** to forward entry to audit sinks
- Right-click > **Show Details** to view full change metadata

---

## Configuration

### Settings

Access via **Settings** (`Ctrl+,`) and search for **"strata"**.

#### `strata.cliPath`
**Type:** `string`  
**Default:** `"strata"`  
**Description:** Path to the Strata CLI executable.

Set to `"uv run strata"` if using a uv-managed development environment.

```json
{
  "strata.cliPath": "uv run strata"
}
```

#### `strata.validateOnSave`
**Type:** `boolean`  
**Default:** `true`  
**Description:** Automatically validate YAML files when saved.

#### `strata.validateOnType`
**Type:** `boolean`  
**Default:** `false`  
**Description:** Validate YAML as you type (debounced, 1.5s delay).

⚠️ **Warning:** Can impact editor responsiveness on large files or slow systems. Use with caution.

#### `strata.showCodeLens`
**Type:** `boolean`  
**Default:** `true`  
**Description:** Display inline "Validate / Build / Deploy" code lens above YAML documents.

#### `strata.showStatusBar`
**Type:** `boolean`  
**Default:** `true`  
**Description:** Show workspace readiness and active profile in the VS Code status bar.

#### `strata.autoExportSchemas`
**Type:** `boolean`  
**Default:** `false`  
**Description:** Automatically export and wire JSON schemas when a Strata workspace is opened.

#### `strata.showFileDecorations`
**Type:** `boolean`  
**Default:** `true`  
**Description:** Show validation badges (✓/⚠️/❌) on Strata files in the Explorer.

#### `strata.defaultProfile`
**Type:** `string`  
**Default:** `""`  
**Description:** Profile to activate automatically when the workspace opens.

Leave empty to use the last active profile.

---

## Tasks

Use Strata in VS Code tasks for CI/CD pipelines.

### Task Type: `strata`

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "type": "strata",
      "label": "Strata: Validate All",
      "command": "validate",
      "problemMatcher": ["$strata-validate"],
      "group": {
        "kind": "build",
        "isDefault": false
      }
    },
    {
      "type": "strata",
      "label": "Strata: Deploy Dev",
      "command": "deploy",
      "file": "deploy/dev.yaml",
      "dryRun": true,
      "group": {
        "kind": "build",
        "isDefault": false
      }
    }
  ]
}
```

### Task Properties

| Property  | Type    | Description                                          |
| --------- | ------- | ---------------------------------------------------- |
| `command` | enum    | `validate`, `build`, or `deploy`                     |
| `file`    | string  | YAML file path relative to workspace root (optional) |
| `dryRun`  | boolean | Run in dry-run mode (default: false)                 |
| `stage`   | string  | Target a specific deployment stage (optional)        |

---

## Chat Participant

Use the **Strata** chat participant to ask questions about your workspace.

### In VS Code Chat

```
@strata <message>
```

### Available Commands

| Command            | Purpose                                       |
| ------------------ | --------------------------------------------- |
| `@strata status`   | Show workspace health, profile, and readiness |
| `@strata validate` | Validate the current file or named file       |
| `@strata guide`    | Show the 8-phase readiness checklist          |
| `@strata build`    | Show how to build your deployment             |
| `@strata deploy`   | Show how to deploy your deployment            |
| `@strata repos`    | Show repository status with latest tags       |

---

## Troubleshooting

### "strata: command not found"

**Problem:** The extension cannot find the Strata CLI.

**Solution:**
1. Ensure Strata is installed: `pipx install xyz-strata` or `uv sync`
2. If installed in a virtual environment, set `strata.cliPath`:
   - For uv projects: `"strata.cliPath": "uv run strata"`
   - For virtualenv: `"strata.cliPath": "/path/to/venv/bin/strata"`

### "No strata workspace found"

**Problem:** The extension doesn't see a `.strata/solution.json` file.

**Solution:**
1. Open the **Strata** view in the activity bar
2. Click **"Initialize Workspace"**
3. Or run: `strata sln init` in the terminal
4. Reload VS Code (`Ctrl+Shift+P` > "Reload Window")

### Validation runs slowly

**Problem:** On-save validation is slow on large files or slow systems.

**Solution:**
1. Disable `strata.validateOnType` (should already be false by default)
2. Increase debounce delay (internal setting, not user-configurable yet)
3. Or disable `strata.validateOnSave` and validate manually

### Schema autocomplete not working

**Problem:** YAML editor doesn't show autocomplete suggestions.

**Solution:**
1. Run **"Strata: Export & Wire Schemas"**
2. This wires the schemas for the YAML extension
3. Schemas are cached in `.vscode/strata-schemas.json` (auto-generated)

### Authentication errors (Azure Key Vault, Vault, etc.)

**Problem:** Errors when accessing secret stores during validation.

**Solution:**
1. Ensure you're authenticated:
   - Azure: `az login`
   - HashiCorp Vault: Set `VAULT_ADDR` and `VAULT_TOKEN`
   - AWS: Configure `~/.aws/credentials`
2. Run **"Strata: Run Doctor"** to check connectivity
3. Check the **Output** panel for detailed error messages

### "View shows empty tree"

**Problem:** A tree view (Files, Repositories, Environments, etc.) is empty.

**Solution:**
1. Click **Refresh** at the top of the view
2. Check that your `.strata/solution.json` is valid: `strata validate .strata/solution.json`
3. Check the extension output: **View** > **Output** > select **"Strata"** from dropdown

### Extension takes a long time to activate

**Problem:** Strata panel doesn't appear immediately when opening a workspace.

**Solution:**
1. Extension only activates when `.strata/solution.json` is detected
2. Initialize workspace: **"Strata: Initialize Workspace"**
3. Reload VS Code after creating `.strata/solution.json`

### Weird validation errors with "@repo/path" references

**Problem:** Cross-repository references show as "file not found" errors.

**Solution:**
1. Verify repositories are registered: `strata repo list`
2. Check that remote paths exist: `strata repo status`
3. Use `strata validate --deep` to validate cross-repo references
4. Activate a profile first: `strata profile activate <name>`

---

## Development

### Building from Source

```bash
cd src/vscode

# Install dependencies
npm install

# Compile TypeScript
npm run compile

# Watch for changes (development)
npm run watch

# Run tests
npm run test

# Package as VSIX (for distribution)
npm run package
```

### Testing

```bash
npm run test
```

Tests use mocha and are located in `tests/`.

### Extension Structure

```
src/vscode/
├── src/
│   ├── extension.ts           # Entry point
│   ├── strataClient.ts        # CLI wrapper
│   └── providers/             # View providers, code lens, chat, etc.
├── tests/                     # Unit tests
├── resources/                 # Icons, walkthrough markdown
├── package.json               # Extension manifest
├── tsconfig.json              # TypeScript config
└── README.md                  # This file
```

### Debugging

1. Press **F5** to start the debug session
2. A new VS Code window opens with the extension loaded
3. Use the **Debug Console** to inspect logs

---

## Known Limitations

- ⚠️ **Large files** — Validation on-type may be slow for files > 1000 lines
- ⚠️ **Circular dependencies** — Circular @repo references are detected but not resolved
- ⚠️ **Terminal integration** — Console REPL is launched in VS Code's integrated terminal; some features may behave differently than in a native shell
- ⚠️ **Remote workspaces** — Limited testing on VS Code Remote (SSH, Dev Containers). Use `uv run strata` for best results.

---

## Support & Contributing

- **Issues & Feature Requests:** [GitHub Issues](https://github.com/huybrechtsxyz/strata/issues)
- **Discussions:** [GitHub Discussions](https://github.com/huybrechtsxyz/strata/discussions)
- **Contributing:** See [CONTRIBUTING.md](../../CONTRIBUTING.md)

---

## License

**AGPL-3.0-or-later**

The Strata extension is part of the [Strata project](https://github.com/huybrechtsxyz/strata) and is licensed under the GNU Affero General Public License v3.0 or later. See [LICENSE](../../LICENSE) for details.

---

## Related Resources

- 📖 [Full Strata Documentation](https://huybrechtsxyz.github.io/strata)
- 🚀 [Getting Started Guide](https://huybrechtsxyz.github.io/strata/platform/getting-started.html)
- 📋 [Architecture Decisions (ADRs)](https://huybrechtsxyz.github.io/strata/decisions/)
- 🎓 [Examples & Tutorials](https://huybrechtsxyz.github.io/strata/examples/)
