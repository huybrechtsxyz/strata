# Tree Views

The Strata panel (in the Activity Bar) contains four tree views that give you a live window into your workspace state.

## Workspace

The **Workspace** view shows overall health and readiness:

```
📋 Workspace: my-project
├── 🟢 Health: HEALTHY
├── 👤 Profile: dev (active)
│   └── Available: dev, staging, prd
├── 📊 Readiness
│   ├── Phase 1: Workspace initialized ✅
│   ├── Phase 2: Repositories registered ✅
│   ├── Phase 5: Build artifacts ready ⏳
│   └── Next step: strata build run -f deploy/main.yaml
└── 📦 Repositories: 2 registered
```

**Clicking:**

- Click profile name → quick-pick to switch profiles
- Click the Refresh icon (top-right) → manually refresh workspace state
- Click "Next step" hint → copies command to clipboard

**What it displays:**

- **Health** — overall status (HEALTHY, DEGRADED, BROKEN) with issue count
- **Active profile** — current profile and list of all available profiles
- **Readiness checklist** — phases complete, statuses (ok/pending/failed), and the next suggested action
- **Repository count** — total registered repositories

## Files

The **Files** view shows all strata YAML documents grouped by kind:

```
📄 Configurations (2)
  main.yaml           ✅ (0 issues)
  test.yaml           ⚠️ (2 warnings)
📄 Deployments (1)
  production.yaml     ❌ (1 error)
📄 Environments (3)
  dev.yaml            ✅
  staging.yaml        ✅
  prod.yaml           ⏳ (not validated)
📄 Modules (1)
  api.yaml            ✅
📄 Namespaces (0)
📄 Providers (2)
  aws.yaml            ✅
  azure.yaml          ✅
📄 Resources (5)
  network.yaml        ✅
  storage.yaml        ✅
  ...
📄 Firewalls (1)
  main.yaml           ✅
📄 Networks (2)
📄 DNS (0)
📄 Tenants (0)
```

**Icons:**

- ✅ Green — validated, no issues
- ⚠️ Yellow — validated, warnings present
- ❌ Red — validation errors
- ⏳ Gray — not yet validated
- (number) — count of documents in this kind

**Right-click menu:**

- **Open** — open the file
- **Validate** — run `strata validate` on this file
- **Build** — run `strata build` (for deployment files)
- **Deploy** — run `strata deploy` (for deployment files)
- **Show Schema** — open JSON schema for this document kind
- **Copy Path** — copy relative path to clipboard

**Single-click:**

Opens the file in the editor.

**Filtering:**

The view auto-discovers files from `profiles.paths` in the last workspace status. Only files known to strata appear here. If you add a new YAML file, save it and the view refreshes.

## Repositories

The **Repositories** view shows all configured configuration repositories:

```
📦 Repositories (2)
├── infra
│   ├── 🔗 URL: git@github.com:myorg/xyz-infra.git
│   ├── 📂 Path: /home/user/repos/xyz-infra
│   ├── 🌿 Branch: main
│   └── ✅ Status: cloned
└── modules
    ├── 🔗 URL: (not cloned)
    ├── 📂 Path: /home/user/repos/xyz-modules
    ├── 🌿 Branch: main
    └── ⏳ Status: not cloned
```

**What it shows:**

- **Name** — repository name from `strata repo list`
- **URL** — Git remote URL
- **Path** — local path to the repository
- **Branch** — current branch (if cloned)
- **Status** — ✅ cloned, ⏳ not cloned, ❌ error

**Right-click menu:**

- **Open in Explorer** — open the repository folder in file explorer
- **Open in Terminal** — open a new terminal in the repo directory
- **Copy Path** — copy path to clipboard

## Tools

The **Tools** view shows availability and version of external integrations:

```
🔧 Tools (5)
├── 🟢 terraform    1.9.0
├── 🟢 docker       27.0.1
├── 🟢 git          2.43.0
├── 🟢 helm         3.14.0
└── 🔴 kustomize    (not found)
```

**Status indicators:**

- 🟢 **Available** — tool is installed, version shown
- 🔴 **Not found** — tool is not in `$PATH`
- 🟡 **Error** — tool was found but failed to report version

This helps you quickly see what integrations are available for your deployment. If a required tool is missing (e.g., terraform), you'll see it here before running a build.

**Right-click menu:**

- **Check Again** — re-probe tool availability (useful if you just installed something)

---

## Interactivity

All tree views refresh automatically when:

- **On save** — you save any YAML file and validation runs
- **On workspace change** — external tools modify `.strata/solution.json`
- **On profile change** — you switch profiles via the status bar or Command Palette
- **On command completion** — after running build/deploy commands
- **On manual refresh** — click the Refresh icon at the top of the Workspace view

**No file I/O during rendering** — all tree data comes from the last `getStatus()` call, so the UI stays responsive even with large workspaces.
