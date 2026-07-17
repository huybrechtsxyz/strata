# `strata validate graph` — Workspace Dependency Graph

- Status: completed
- Date: 2026-06-24
- Parent: [0014-onboarding-experience.md](0014-onboarding-experience.md) (item #11)

## Summary

A new `strata validate graph` subcommand that generates Mermaid dependency diagrams of the workspace. Two graph modes:

1. **File graph** (`--mode files`, default) — nodes are YAML files, edges are cross-file references. Answers: "how do my files wire together?"
2. **Resource graph** (`--mode resources`) — nodes are logical resources/modules/namespaces, edges are `depends_on`, module attachments, subnet assignments, and topology groupings. Answers: "what does my infrastructure look like?"

## Context

Strata workspaces contain a web of YAML files that reference each other. The dependency chain is:

```
configuration
  └─ environments (referenced by deployments)
  └─ workspace
       ├─ resources (file: references)
       │    └─ modules (nested file: references)
       ├─ namespaces (file: references)
       ├─ networks (file: references)
       ├─ firewalls (file: references)
       └─ dns_zones (file: references)
  └─ deployment
       ├─ → workspace (spec.workspace.file)
       ├─ → environments (spec.environments[])
       └─ → configurations (spec.configurations[].file)
```

A new user can't see these relationships. A `validate graph` command makes the dependency tree visible and actionable — you see what's connected, what's missing, and what's broken.

## Command Placement

**Chosen:** `strata validate graph`

| Candidate               | Reasoning                                                                                                                                                    | Verdict                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| `strata validate graph` | Graph is a visual validation artifact — "show me if my workspace wires up correctly." Extends the existing `validate` command without a new top-level group. | ✅ Selected             |
| `strata guide graph`    | Conceptually fits onboarding, but `guide` is currently a single command (not a group). Turning it into a group is a Phase 2 REPL concern.                    | ❌ Deferred             |
| `strata build graph`    | `build` is about producing deploy artifacts. A dependency graph is informational, not a build step.                                                          | ❌ Wrong semantics      |
| `strata flow`           | Top-level would be clean, but violates the flat `strata <group> <command>` convention for non-trivial commands.                                              | ❌ Convention violation |

**Consequence:** `validate` becomes a Click **group** with two subcommands:

```
strata validate run   ← current validate behavior (default when invoked as `strata validate`)
strata validate graph ← new dependency graph
```

Backward compatibility: `strata validate -f foo.yaml` continues to work via Click's `invoke_without_command` + `result_callback` pattern or by making `run` the default.

### Impact of Group Conversion

Converting `validate` from a `@click.command` to a `@click.group` is a **structural change** with broad ripple effects:

| Area                              | What changes                                                                                                                                                                                                |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CLI wiring**                    | `cli_validate.py` rewrites from single command to group with `run` (default) + `graph` subcommands                                                                                                          |
| **Click help text**               | `strata validate --help` changes from showing options to showing subcommands. The `run` subcommand needs its own `--help` with all current options. Group-level help must explain both subcommands clearly. |
| **Existing tests**                | All tests in `tests/strata/commands/` that invoke `validate` via `CliRunner` must be verified — they should still pass since `run` is the default, but the Click invocation path changes                    |
| **CI template validation test**   | `test_template_validation.py` uses `PlatformValidator` directly (not CLI), so unaffected — but any CLI-level validation tests need review                                                                   |
| **Documentation**                 | `docs/platform/commands.md` must update the command table (validate moves from standalone command to group), add `graph` subcommand docs, update examples                                                   |
| **Sphinx docs**                   | `docs/platform/validators.md` may reference CLI usage — verify                                                                                                                                              |
| **`strata guide` hints**          | The guide command suggests `strata validate <file>` as a next step — verify these hints still work or update to `strata validate run -f <file>`                                                             |
| **`.github/prompts/`**            | Copilot prompts in the init scaffold reference `strata validate` — update examples                                                                                                                          |
| **`GETTING_STARTED.md` template** | Generated at `sln init` — references `strata validate`                                                                                                                                                      |
| **CI workflows**                  | Any `strata validate` in GitHub Actions workflows continues to work (default subcommand), but should be audited                                                                                             |
| **Skill file**                    | The `strata-onboarding` skill (ADR #18) will reference the command — plan for `graph` from the start                                                                                                        |

**Mitigation:** The `invoke_without_command=True` pattern ensures bare `strata validate -f foo.yaml` keeps working. But help output changes visually, and anything that parses `--help` output (unlikely but possible in tests) will break.

**Test plan for the group conversion:**
1. All existing `validate` CLI tests pass unchanged (bare invocation = `run`)
2. `strata validate --help` shows both subcommands with descriptions
3. `strata validate run --help` shows all current options
4. `strata validate graph --help` shows graph-specific options
5. `strata validate -f foo.yaml` still works (backward compat)
6. `strata validate run -f foo.yaml` also works (explicit)
7. Exit codes unchanged for all existing validation scenarios

---

## Node Identity: File Path vs `meta.name`

|                             | File path                                                    | `meta.name`                                                                         |
| --------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| **Actionable**              | ✅ User can open/find the file directly                       | ❌ Need to know which file defines `aks_cluster`                                     |
| **Unique**                  | ✅ Always unique (filesystem guarantees)                      | ⚠️ Not unique across kinds — a workspace and resource could both be named `platform` |
| **Readable**                | ❌ Verbose in diagrams (`stack/azure-res-aks.yaml`)           | ✅ Short, semantic (`aks_cluster`)                                                   |
| **Works for missing nodes** | ✅ The path IS the reference — even if the file doesn't exist | ❌ Can't read `meta.name` from a file that doesn't exist                             |
| **Cross-reference match**   | ❌ `depends_on` uses names, not paths                         | ✅ Matches how the model refers to things internally                                 |

**Decision:** Use **both**, with mode-appropriate defaults:

| Mode               | Node ID (internal) | Display label                                                      | Rationale                                                                 |
| ------------------ | ------------------ | ------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| `--mode files`     | Relative file path | `meta.name (path)` — e.g. `aks_cluster (stack/azure-res-aks.yaml)` | It's a file graph — paths are primary. But show the name for readability. |
| `--mode resources` | `meta.name`        | `meta.name` with role/kind annotation — e.g. `aks_cluster`         | It's a logical graph — names are primary. File paths are noise.           |

For **missing nodes** in file mode: the path is the label (no name available since the file can't be parsed). For **dangling references** in resource mode: the referenced name is the label (shown in red).

In JSON output, both `path` and `name` are always included when available, so consumers can use either.

---

## File Graph (`--mode files`)

### Nodes

Each YAML file in the workspace is a node. Node identity is the relative file path; display label includes `meta.name` when the file is parseable.

```text
graph LR
  config["azure_aks_config (config/azure-aks-config.yaml)"]
  env["azure_aks_env_prd (environments/azure-aks-env-prd.yaml)"]
  ws["azure_ws_platform (stack/azure-ws-platform.yaml)"]
  res["aks_cluster (stack/azure-res-aks.yaml)"]
  mod["traefik (stack/azure-mod-traefik.yaml)"]
  deploy["azure_aks_deploy_prd (deploy/azure-aks-deploy-prd.yaml)"]
```

### Node Status (Color)

| Status   | Color                 | Condition                                                                        |
| -------- | --------------------- | -------------------------------------------------------------------------------- |
| Valid    | green (`:::valid`)    | File exists, Phase 1 validation passes                                           |
| Invalid  | orange (`:::invalid`) | File exists, Phase 1 validation fails                                            |
| Missing  | red (`:::missing`)    | Referenced by another file but does not exist on disk                            |
| External | grey (`:::external`)  | `@repo/path` reference to a file in another repository                           |
| Orphan   | dashed (`:::orphan`)  | File exists and validates, but no other file references it (e.g. provider files) |

### Edges

Edges represent cross-file references discovered by inspecting model fields:

| Source Kind | Field                             | Target Kind   | Edge Label                           |
| ----------- | --------------------------------- | ------------- | ------------------------------------ |
| deployment  | `spec.workspace.file`             | workspace     | `workspace`                          |
| deployment  | `spec.environments[]`             | environment   | `environment`                        |
| deployment  | `spec.configurations[].file`      | configuration | `configuration`                      |
| workspace   | `spec.resources[].file`           | resource      | `resource`                           |
| workspace   | `spec.resources[].modules[].file` | module        | `module`                             |
| workspace   | `spec.namespaces[].file`          | namespace     | `namespace`                          |
| workspace   | `spec.networks[].file`            | network       | `network`                            |
| workspace   | `spec.firewalls[].file`           | firewall      | `firewall`                           |
| workspace   | `spec.dns_zones[].file`           | dns           | `dns`                                |
| workspace   | `spec.resources[].depends_on[]`   | resource      | `depends_on`                         |
| workspace   | `spec.topology[].provisioner`     | (inline)      | `provisioner` (annotation, not edge) |

> **Note on `depends_on`:** The `depends_on` field lives on `WorkspaceResourceModel` (the gluing layer inside the workspace file), NOT on the resource file itself. A resource YAML file doesn't know about its siblings. The graph renders the edge between two resource *file* nodes, but the data is discovered by parsing the *workspace* file.

### Edge Discovery Algorithm

1. **Discover entry points:** Glob all `.yaml` / `.yml` files in the workspace. Parse each for the `kind` field. Files with `kind: deployment` are entry points. (There is no deployment registry — this is a full scan.)
2. For each entry point, recursively follow file references (parse with the appropriate model, Phase 1 only — no cross-ref resolution needed).
3. Extract reference fields (table above). Resolve relative paths against the referencing file's directory.
4. For `@repo/` prefixed paths: mark as `external` node (grey) — don't attempt resolution.
5. Build adjacency list: `{source_path → [(target_path, edge_label)]}`.
6. For files not reachable from any deployment (orphan providers, standalone DNS/firewall): include as disconnected nodes if `--entry` is not set.

### Mermaid Node ID Generation

Mermaid node IDs cannot contain `/`, `.`, or `-` without quoting. Slugify rule:

```
file path:  deploy/azure-aks-deploy-prd.yaml
mermaid ID: deploy_azure_aks_deploy_prd
```

Algorithm: strip extension → replace `/`, `-`, `.` with `_` → lowercase. The display label (in quotes) shows the full `meta.name (path)` string.

---

## Resource Graph (`--mode resources`)

The resource graph shows the **logical infrastructure topology** — not files, but the named resources, modules, namespaces, and their relationships as defined in the workspace.

### Data Source

The resource graph is extracted from a single workspace file (`kind: workspace`). It reads:

- `spec.resources[]` — resource definitions (name, depends_on, modules, subnet, firewalls)
- `spec.namespaces[]` — namespace definitions
- `spec.networks[]` — network references
- `spec.topology[]` — provisioner groupings (which resources go to which provisioner)
- `spec.provisioners[]` — IaC tool definitions

If `--entry` points to a deployment, the deployment's `spec.workspace.file` is resolved to find the workspace.

### Resources Not Assigned to Any Topology

A workspace may define resources in `spec.resources[]` that aren't referenced by any `spec.topology[].components[]`. These appear as nodes **outside all subgraphs**, grouped under a virtual "(unassigned)" section. This makes forgotten/misconfigured resources visible — a common wiring mistake.

### Nodes

| Node Type | Source                            | Shape     | Label                                |
| --------- | --------------------------------- | --------- | ------------------------------------ |
| Resource  | `spec.resources[].name`           | rectangle | `{name}` with role annotation        |
| Module    | `spec.resources[].modules[].name` | rounded   | `{name}` (slot: main/staging/canary) |
| Namespace | `spec.namespaces[].name`          | hexagon   | `{name}`                             |
| Network   | `spec.networks[].name`            | cylinder  | `{name}`                             |
| Provider  | `spec.provisioners[].name`        | diamond   | `{name}` (type annotation)           |

### Node Status (Color)

| Status   | Color | Condition                                                      |
| -------- | ----- | -------------------------------------------------------------- |
| Active   | green | `enabled: true` (or omitted — default)                         |
| Disabled | grey  | `enabled: false`                                               |
| Dangling | red   | Referenced in `depends_on` but not defined in `spec.resources` |

### Edges

| Source   | Target         | Field            | Edge Label               |
| -------- | -------------- | ---------------- | ------------------------ |
| Resource | Resource       | `depends_on[]`   | `depends_on`             |
| Resource | Module         | `modules[].name` | `runs`                   |
| Resource | Network/Subnet | `subnet`         | `subnet: {net}/{subnet}` |
| Resource | Firewall       | `firewalls[]`    | `firewall`               |
| Resource | Resource       | `references`     | `ref: {key}`             |

### Topology Subgraphs

Each topology entry becomes a Mermaid **subgraph** that groups its components:

```text
graph TD
  subgraph topology_infra["infrastructure (terraform · azure)"]
    res_aks["aks_cluster"]
    res_pg["postgres"]
    res_acr["acr"]
    res_kv["keyvault"]
    net["platform_network"]
  end

  subgraph topology_services["services (helm · kubernetes)"]
    ns_platform["platform (namespace)"]
    mod_traefik["traefik (main)"]
  end

  res_aks -->|depends_on| res_acr
  res_aks -->|depends_on| res_kv
  res_aks -->|runs| mod_traefik
  res_aks -.->|subnet| net
```

### Example: Azure AKS Resource Graph

```text
graph TD
  classDef resource fill:#dbeafe,stroke:#2563eb
  classDef module fill:#fef3c7,stroke:#d97706
  classDef namespace fill:#d1fae5,stroke:#059669
  classDef network fill:#e0e7ff,stroke:#4f46e5
  classDef disabled fill:#e2e3e5,stroke:#6c757d

  subgraph infra["infrastructure (terraform · westeurope)"]
    aks["aks_cluster"]:::resource
    pg["postgres"]:::resource
    acr["acr"]:::resource
    kv["keyvault"]:::resource
    net["platform_network"]:::network
  end

  subgraph services["services (helm · kubernetes)"]
    ns["platform (namespace)"]:::namespace
    traefik["traefik (main)"]:::module
  end

  aks -->|depends_on| acr
  aks -->|depends_on| kv
  aks -->|runs| traefik
  aks -.->|subnet: platform_network/snet_aks| net
  pg -.->|subnet: platform_network/snet_postgres| net
  traefik -.-> ns
```

### Console Output (Resource Mode)

```
azure-ws-platform Resource Graph

infrastructure (terraform · azure-provider-westeurope)
├── aks_cluster (resource)
│   ├── → depends_on: acr
│   ├── → depends_on: keyvault
│   ├── → subnet: platform_network/snet_aks
│   └── traefik (module, slot: main)
├── postgres (resource)
│   └── → subnet: platform_network/snet_postgres
├── acr (resource)
├── keyvault (resource)
└── platform_network (network)

services (helm · kubernetes)
└── platform (namespace)
    └── traefik → deployed here

Resources: 4 | Modules: 1 | Namespaces: 1 | Networks: 1
Dependencies: aks_cluster → [acr, keyvault]
```

### JSON Output (Resource Mode)

```json
{
  "success": true,
  "data": {
    "workspace": "azure_ws_platform",
    "topologies": [
      {
        "name": "infrastructure",
        "provisioner": "platform_iac",
        "provider": "azure_provider_westeurope",
        "type": "azure-native",
        "resources": ["aks_cluster", "postgres", "acr", "keyvault"],
        "namespaces": []
      },
      {
        "name": "services",
        "provisioner": "platform_helm",
        "provider": "azure_provider_westeurope",
        "type": "kubernetes",
        "resources": [],
        "namespaces": ["platform"]
      }
    ],
    "nodes": [
      {"name": "aks_cluster", "type": "resource", "role": "compute", "enabled": true, "depends_on": ["acr", "keyvault"], "modules": ["traefik"], "subnet": "platform_network/snet_aks"},
      {"name": "postgres", "type": "resource", "role": "database", "enabled": true, "depends_on": [], "modules": [], "subnet": "platform_network/snet_postgres"},
      {"name": "acr", "type": "resource", "role": "registry", "enabled": true, "depends_on": [], "modules": []},
      {"name": "keyvault", "type": "resource", "role": "secrets", "enabled": true, "depends_on": [], "modules": []},
      {"name": "platform_network", "type": "network", "enabled": true},
      {"name": "platform", "type": "namespace", "enabled": true},
      {"name": "traefik", "type": "module", "slot_type": "main", "enabled": true, "parent_resource": "aks_cluster"}
    ],
    "edges": [
      {"source": "aks_cluster", "target": "acr", "label": "depends_on"},
      {"source": "aks_cluster", "target": "keyvault", "label": "depends_on"},
      {"source": "aks_cluster", "target": "traefik", "label": "runs"},
      {"source": "aks_cluster", "target": "platform_network", "label": "subnet: snet_aks"},
      {"source": "postgres", "target": "platform_network", "label": "subnet: snet_postgres"}
    ],
    "summary": {"resources": 4, "modules": 1, "namespaces": 1, "networks": 1, "dependencies": 2}
  }
}
```

### Deployment Order (bonus insight)

The resource graph has enough information to derive a **deployment order** (topological sort of `depends_on` edges). When `--verbose` is set in resource mode, append:

```
Deployment Order (topological):
  1. platform_network, acr, keyvault
  2. aks_cluster, postgres
  3. traefik (module on aks_cluster)
```

This is pure derived output — same graph, different projection. Useful for understanding "what gets provisioned first?"

---

## Output Modes

### 1. Mermaid markdown file (`--save` / default)

Writes `graph.md` (or user-specified path) containing:

```markdown
# Workspace Dependency Graph

Generated: 2026-06-24T14:30:00Z
Workspace: my-platform

## Graph

​```text
graph LR
  classDef valid fill:#d4edda,stroke:#28a745
  classDef invalid fill:#fff3cd,stroke:#ffc107
  classDef missing fill:#f8d7da,stroke:#dc3545
  classDef external fill:#e2e3e5,stroke:#6c757d

  deploy_deploy_prd["my_deploy_prd (deploy/deploy-prd.yaml)"]:::valid
  stack_ws_platform["my_ws_platform (stack/ws-platform.yaml)"]:::valid
  envs_env_prd["my_env_prd (envs/env-prd.yaml)"]:::valid
  stack_res_aks["aks_cluster (stack/res-aks.yaml)"]:::valid
  stack_mod_traefik["traefik (stack/mod-traefik.yaml)"]:::invalid
  stack_ns_app["stack/ns-app.yaml"]:::missing

  deploy_deploy_prd -->|workspace| stack_ws_platform
  deploy_deploy_prd -->|environment| envs_env_prd
  stack_ws_platform -->|resource| stack_res_aks
  stack_ws_platform -->|namespace| stack_ns_app
  stack_res_aks -->|module| stack_mod_traefik
​```

## Summary

| Status    | Count | Files                                                         |
| --------- | ----- | ------------------------------------------------------------- |
| ✅ Valid   | 4     | deploy-prd.yaml, ws-platform.yaml, env-prd.yaml, res-aks.yaml |
| ⚠️ Invalid | 1     | mod-traefik.yaml                                              |
| ❌ Missing | 1     | ns-app.yaml                                                   |

## Mermaid Live

[Open in Mermaid Live Editor](https://mermaid.live/edit#base64=...)
```

### 2. Console output (`--output console`)

Renders a text tree (no Mermaid rendering in terminal):

```
Workspace Dependency Graph: my-platform

azure_aks_deploy_prd (deploy/deploy-prd.yaml) [deployment] ✅
├── azure_aks_env_prd (envs/env-prd.yaml) [environment] ✅
└── azure_ws_platform (stack/ws-platform.yaml) [workspace] ✅
    ├── aks_cluster (stack/res-aks.yaml) [resource] ✅
    │   └── traefik (stack/mod-traefik.yaml) [module] ⚠️
    │       └── Error: spec.services[0].image — required field missing
    └── stack/ns-app.yaml [namespace] ❌ NOT FOUND

Summary: 4 valid, 1 invalid, 1 missing
```

Note: missing nodes show only the path (no `meta.name` — file can't be parsed).

### 3. JSON output (`--output json`)

```json
{
  "success": true,
  "data": {
    "mode": "files",
    "nodes": [
      {"path": "deploy/deploy-prd.yaml", "name": "azure_aks_deploy_prd", "kind": "deployment", "status": "valid"},
      {"path": "stack/ws-platform.yaml", "name": "azure_ws_platform", "kind": "workspace", "status": "valid"},
      {"path": "stack/ns-app.yaml", "name": null, "kind": "namespace", "status": "missing"}
    ],
    "edges": [
      {"source": "deploy/deploy-prd.yaml", "target": "stack/ws-platform.yaml", "label": "workspace"},
      {"source": "stack/ws-platform.yaml", "target": "stack/ns-app.yaml", "label": "namespace"}
    ],
    "summary": {"valid": 4, "invalid": 1, "missing": 1}
  }
}
```

Note: `name` is `null` for missing/unparseable nodes — consumers should fall back to `path` for display.

---

## CLI Interface

```
strata validate graph [OPTIONS]

Options:
  --mode MODE        Graph type: files (default) | resources.
                     'files' shows YAML file dependency tree.
                     'resources' shows logical infrastructure topology.
  --entry PATH       Entry point file (deployment or workspace YAML). If omitted,
                     discovers all deployments in the workspace and graphs each.
  --save [PATH]      Write Mermaid markdown to file (in addition to console output).
                     Default path: graph.md in work_path. When combined with
                     --quiet, only the file is written.
  --direction DIR    Mermaid graph direction: LR (default for files) | TD (default
                     for resources) | BT | RL.
  --no-validate      Skip validation (all nodes shown as neutral/grey).
                     Faster for large workspaces when you only want structure.
  -f, --file PATH    Alias for --entry (consistency with validate run).
  --output FORMAT    console | json | text. Default: console.
  --work-path PATH   Workspace root (standard option).
  --verbose          Include validation error details (files mode) or deployment
                     order (resources mode).
  --quiet            Suppress console output (only write --save file).
```

### Examples

```bash
# File dependency graph (default mode)
strata validate graph

# Resource topology graph
strata validate graph --mode resources

# Graph a specific deployment's file tree
strata validate graph --entry deploy/deploy-prd.yaml

# Resource graph for a specific workspace file
strata validate graph --mode resources --entry stack/ws-platform.yaml

# Save both graphs as markdown
strata validate graph --save graph-files.md
strata validate graph --mode resources --save graph-resources.md

# Save as markdown with top-down layout
strata validate graph --save --direction TD

# CI: check for missing references (exit code 3 if any node is "missing")
strata validate graph --output json | jq '.data.summary.missing'

# Resource graph with deployment order
strata validate graph --mode resources --verbose

# Structure only (skip validation, fast)
strata validate graph --no-validate --save docs/architecture.md
```

---

## Implementation Architecture

```
commands/
  cli_validate.py            ← convert from @click.command to @click.group
  validate/
    run_validate_command.py   ← existing (rename internally, stays backward-compatible)
    graph_validate_command.py ← NEW: thin Click wrapper

controllers/
  graph_controller.py        ← NEW: orchestrates graph building (both modes)

services/
  (no new services — reuses PlatformValidator + existing model loading)

utils/
  graph.py                   ← NEW: pure functions for graph building + Mermaid rendering
```

### `GraphController`

```python
class GraphController(BaseController):
    """Build a workspace dependency graph from YAML file references."""

    def execute(self, mode: str = "files") -> GraphResult:
        if mode == "files":
            return self._build_file_graph()
        else:
            return self._build_resource_graph()

    def _build_file_graph(self) -> GraphResult:
        # 1. Discover entry points (deployments) or use --entry
        # 2. For each deployment, recursively resolve file references
        # 3. Validate each discovered node (unless --no-validate)
        # 4. Build GraphResult (nodes + edges + summary)
        ...

    def _build_resource_graph(self) -> GraphResult:
        # 1. Find workspace file (from --entry or via deployment)
        # 2. Load workspace model (Phase 1 parse)
        # 3. Extract resources, modules, namespaces, networks
        # 4. Map depends_on, subnet, firewall, module relationships
        # 5. Group by topology (subgraphs)
        # 6. Build GraphResult with topology metadata
        ...
```

### `utils/graph.py`

Pure utility functions (no service imports):

```python
@dataclass
class GraphNode:
    identifier: str        # primary key: file path (file mode) or meta.name (resource mode)
    path: Optional[str]    # file path (always set in file mode; set in resource mode if known)
    name: Optional[str]    # meta.name (null for missing/unparseable files)
    kind: str              # PlatformKind or resource/module/namespace/network
    status: str            # valid | invalid | missing | external | active | disabled | dangling
    errors: list[str]      # validation errors (if any)
    metadata: dict         # role, slot_type, subnet, topology, etc.

@dataclass
class GraphEdge:
    source: str            # node identifier
    target: str            # node identifier
    label: str             # reference type

@dataclass
class GraphTopology:
    name: str              # topology name
    provisioner: str       # provisioner name
    provider: str          # provider name
    type: str              # topology type (kubernetes, azure-native, etc.)
    components: list[str]  # resource names in this topology
    namespaces: list[str]  # namespace names in this topology

@dataclass
class GraphResult:
    mode: str              # "files" or "resources"
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    entry_points: list[str]
    topologies: list[GraphTopology]  # only populated in resource mode

def render_mermaid(result: GraphResult, direction: str = "LR") -> str:
    """Render GraphResult as a Mermaid graph definition."""
    ...

def render_mermaid_resources(result: GraphResult, direction: str = "TD") -> str:
    """Render resource graph with topology subgraphs."""
    ...

def render_tree(result: GraphResult) -> str:
    """Render GraphResult as a text tree for console output."""
    ...

def render_mermaid_live_url(mermaid_source: str) -> str:
    """Generate a Mermaid Live Editor URL from source."""
    ...

def compute_deployment_order(result: GraphResult) -> list[list[str]]:
    """Topological sort of resources by depends_on edges. Returns layers."""
    ...
```

---

## Exit Codes

| Condition                                              | Exit Code | Applies to         |
| ------------------------------------------------------ | --------- | ------------------ |
| All nodes valid (or `--no-validate`)                   | 0         | both modes         |
| System error (can't read workspace)                    | 1         | both modes         |
| Any node has status `missing` or `invalid` (file mode) | 3         | `--mode files`     |
| Any node has status `dangling` (resource mode)         | 3         | `--mode resources` |

This aligns with the existing convention: exit code 3 = "processed but invalid." CI pipelines can gate on `strata validate graph` to catch broken references. Both modes use the same exit code semantics — any structural problem that would prevent a successful build returns 3.

---

## Edge Cases

| Case                                   | Behavior                                                                                                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Circular references (A → B → A)        | Detect during traversal (visited set). Log warning, render cycle as bidirectional edge, don't infinite-loop.                                                  |
| `@repo/` paths with no repo map        | Mark as `external` (grey). No resolution attempted — this is a structural view, not a runtime resolver.                                                       |
| File exists but isn't valid YAML       | Node status = `invalid`, error = "Failed to parse YAML".                                                                                                      |
| File has valid YAML but unknown `kind` | Node status = `invalid`, error = "Unknown kind: X". Still include in graph — the reference exists.                                                            |
| Workspace with no deployments          | If no `--entry` and no deployments found, graph all YAML files as disconnected nodes. Warn: "No deployment entry points found — showing all workspace files." |
| Very large workspace (100+ files)      | Mermaid supports this. Console tree may be long — consider `--entry` to scope. JSON is always complete.                                                       |

---

## Example: Azure AKS Reference Workspace

Given `config/azure-aks/`:

```text
graph LR
  classDef valid fill:#d4edda,stroke:#28a745
  classDef external fill:#e2e3e5,stroke:#6c757d
  classDef orphan fill:#f5f5f5,stroke:#adb5bd,stroke-dasharray:5

  deploy_azure_aks_deploy_prd["azure_aks_deploy_prd (deploy/azure-aks-deploy-prd.yaml)"]:::valid
  config_azure_aks_config["azure_aks_config (config/azure-aks-config.yaml)"]:::valid
  environments_azure_aks_env_prd["azure_aks_env_prd (environments/azure-aks-env-prd.yaml)"]:::valid
  stack_azure_ws_platform["azure_ws_platform (stack/azure-ws-platform.yaml)"]:::valid
  stack_azure_res_aks["aks_cluster (stack/azure-res-aks.yaml)"]:::valid
  stack_azure_res_postgres["postgres (stack/azure-res-postgres.yaml)"]:::valid
  stack_azure_res_acr["acr (stack/azure-res-acr.yaml)"]:::valid
  stack_azure_res_keyvault["keyvault (stack/azure-res-keyvault.yaml)"]:::valid
  stack_azure_net_platform["platform_network (stack/azure-net-platform.yaml)"]:::valid
  stack_azure_ns_platform["platform (stack/azure-ns-platform.yaml)"]:::valid
  stack_azure_mod_traefik["traefik (stack/azure-mod-traefik.yaml)"]:::valid
  stack_azure_provider_westeurope["azure_provider_westeurope (stack/azure-provider-westeurope.yaml)"]:::orphan

  deploy_azure_aks_deploy_prd -->|workspace| stack_azure_ws_platform
  deploy_azure_aks_deploy_prd -->|environment| environments_azure_aks_env_prd
  deploy_azure_aks_deploy_prd -->|configuration| config_azure_aks_config
  stack_azure_ws_platform -->|resource| stack_azure_res_aks
  stack_azure_ws_platform -->|resource| stack_azure_res_postgres
  stack_azure_ws_platform -->|resource| stack_azure_res_acr
  stack_azure_ws_platform -->|resource| stack_azure_res_keyvault
  stack_azure_ws_platform -->|network| stack_azure_net_platform
  stack_azure_ws_platform -->|namespace| stack_azure_ns_platform
  stack_azure_res_aks -->|module| stack_azure_mod_traefik
  stack_azure_res_aks -->|depends_on| stack_azure_res_acr
  stack_azure_res_aks -->|depends_on| stack_azure_res_keyvault
```

Note: `azure-provider-westeurope.yaml` is an orphan node — it exists in the workspace but no file references it by path. Shown with dashed border (`:::orphan`) to indicate it's discoverable but not wired into the dependency tree.

Console tree view:

```
azure-aks Dependency Graph

azure_aks_deploy_prd (deploy/azure-aks-deploy-prd.yaml) [deployment] ✅
├── azure_aks_config (config/azure-aks-config.yaml) [configuration] ✅
├── azure_aks_env_prd (environments/azure-aks-env-prd.yaml) [environment] ✅
└── azure_ws_platform (stack/azure-ws-platform.yaml) [workspace] ✅
    ├── aks_cluster (stack/azure-res-aks.yaml) [resource] ✅
    │   ├── traefik (stack/azure-mod-traefik.yaml) [module] ✅
    │   ├── ─ depends_on → acr (stack/azure-res-acr.yaml) ✅
    │   └── ─ depends_on → keyvault (stack/azure-res-keyvault.yaml) ✅
    ├── postgres (stack/azure-res-postgres.yaml) [resource] ✅
    ├── acr (stack/azure-res-acr.yaml) [resource] ✅
    ├── keyvault (stack/azure-res-keyvault.yaml) [resource] ✅
    ├── platform_network (stack/azure-net-platform.yaml) [network] ✅
    └── platform (stack/azure-ns-platform.yaml) [namespace] ✅

Summary: 11 valid, 0 invalid, 0 missing
```

---

## Phase 2 REPL Integration

When the `strata guide` REPL ships (ADR items #6–8), the `flow` REPL command calls `GraphController` directly and renders inline with Rich. Same data model, different presentation layer:

```
guide> flow
┌─────────────────────────────────────────┐
│  Workspace Dependency Graph             │
│  ✅ 11 valid  ⚠️ 0 invalid  ❌ 0 missing │
├─────────────────────────────────────────┤
│  (Rich tree rendering here)             │
└─────────────────────────────────────────┘
guide> flow --save
  → Saved to graph.md
```

---

## Resolved Design Questions

1. **Provider files** — providers (`kind: provider`) aren't referenced by file path from other models. → **Resolved:** Include as orphan nodes with dashed border (`:::orphan`). They're part of the workspace but not wired into the dependency tree. Makes them visible for auditing without cluttering edges.

2. **Tenant/DNS/Firewall isolation** — these kinds may not be in the deployment→workspace path. → **Resolved:** Yes, include all workspace YAML files. Unconnected nodes are shown as orphans (dashed border). The algorithm step 6 already handles this: "For files not reachable from any deployment: include as disconnected nodes if `--entry` is not set."

3. **Multi-deployment workspaces** — when multiple deployments exist, should the graph merge them? → **Resolved:** Merged by default (one unified graph). `--entry` scopes to a single deployment's tree. Shared nodes (same workspace referenced by multiple deployments) appear once with edges from each deployment.

## Open Questions

1. **Depth limit** — should there be a `--depth N` flag to limit traversal depth for very large workspaces? → Propose: not in v1 — premature optimization. Add if requested.

2. **`--watch` mode** — should graph auto-regenerate on file changes (like `strata guide --auto`)? → Propose: deferred to Phase 2 REPL. The REPL can call `GraphController` on each change; the standalone CLI stays one-shot.

---

## Acceptance Criteria

### File Graph (`--mode files`)

- [ ] `strata validate graph` produces a Mermaid markdown file for the Azure AKS reference workspace
- [ ] Console output shows a readable text tree with status indicators
- [ ] JSON output includes nodes, edges, and summary
- [ ] Missing file references are detected and shown as red nodes (exit code 3)
- [ ] `--entry` scopes the graph to a single deployment's tree
- [ ] `--no-validate` skips validation and shows pure structure
- [ ] Circular references are detected without infinite loops
- [ ] `@repo/` paths shown as external (grey) nodes
- [ ] Mermaid Live Editor URL included in `--save` output
- [ ] Backward compatibility: `strata validate -f foo.yaml` still works after group conversion

### Resource Graph (`--mode resources`)

- [ ] `strata validate graph --mode resources` produces a topology-grouped Mermaid diagram
- [ ] Resources grouped into topology subgraphs with provisioner/provider labels
- [ ] `depends_on` edges rendered between resources
- [ ] Module attachments shown as edges from resource → module nodes
- [ ] Subnet references shown as dashed edges to network nodes
- [ ] Disabled resources shown as grey nodes
- [ ] Dangling `depends_on` references (name not in `spec.resources`) shown as red nodes
- [ ] `--verbose` appends deployment order (topological sort)
- [ ] JSON output includes topologies array with component listings
- [ ] Console output shows topology-grouped tree with dependency annotations
