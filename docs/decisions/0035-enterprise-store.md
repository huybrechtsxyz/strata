# Enterprise Catalog — Private Organization-Level Content Repository

- Status: deferred
- Date: 2026-07-11

## Revised Design Direction (2026-07-21)

**The `strata store` CLI command group proposed below is dropped.** Two reasons:

1. **Naming collision**: `store` is already a first-class concept in strata — `SecretStoreType`, `VariableStoreType`, `FeatureStoreType`, and `ManifestStoreType` all use "store" to mean the backend where values live (`azure-keyvault`, `bitwarden`, `constant`, etc.). A `strata store add` command would be confusing alongside `spec.secrets[].store: azure-keyvault` in YAML files.

2. **Duplicate mechanism**: The transport is identical to `strata repo`. A catalog is simply a git repo with a `catalog.yaml` manifest — users register it with `strata repo add` and sync it with `strata repo sync`. No new command group is needed.

**Revised CLI surface:**
- Registration/sync: existing `strata repo add/sync/remove/list`
- Discovery: `strata repo browse <name>` — lists catalog content from repos that have a `catalog.yaml`
- The manifest file is renamed `catalog.yaml` (not `store.yaml`) to avoid the collision

The detailed design below is preserved for reference but should be re-read with these amendments in mind. The term **"catalog"** replaces **"store"** throughout.

---

## Context and Problem Statement

Strata has three content distribution tiers today:

1. **Built-in** — ships with strata (limited, generic)
2. **Community** — public git repos anyone can contribute to (e.g., `strata-dimensions`)
3. **Workspace-local** — custom files in `config/` (per-project, not shared)

The gap: **enterprise DevOps teams want a private, governed registry of strata content shared across all their workspaces.** They don't want to publish their sizing standards, compliance policies, or approved module patterns to the public community — but they also don't want every project team to reinvent them locally.

Typical enterprise needs:

- *"All our teams should use the same cost dimensions — our DBA team maintains the SQL sizing standards"*
- *"Platform engineering curates the approved Terraform modules — project teams pull from our store"*
- *"We have compliance policies that every deployment must pass — those live centrally"*
- *"New projects should scaffold from our standard templates, not from scratch"*

The existing `strata repo add` mechanism technically works (it's just git), but it lacks:
- Discoverability (what's available? what version?)
- Governance (who approved it? is it current?)
- Multi-content-type support (policies, dimensions, templates, scenarios in one place)
- Authentication patterns for private repos
- Catalog/browsing UX

## What Users Expect From a Registry

Users arrive with different mental models depending on their background:

| Expectation                 | What they picture                      | Git repo sufficient?                 |
| --------------------------- | -------------------------------------- | ------------------------------------ |
| Browse available content    | Terraform registry search UX           | Maybe — `strata store list` via CLI  |
| Version pinning             | `version = "2.1.0"` semver constraint  | Yes — git tags are versions          |
| Private / not public        | Only my org can see it                 | Yes — any private git repo           |
| Governed / approved content | PR review before publish               | Yes — git branch protection policies |
| Air-gapped / no internet    | Self-hosted, no public dependency      | Yes — any self-hosted git server     |
| Docs alongside content      | README, examples/ folder               | Yes — just files in the repo         |
| Per-team access control     | Infra team only sees security policies | Yes — repo-level permissions         |

**Is a GitHub repo OK?** Yes — for most teams a private GitHub repo is the registry.
No new infrastructure needed. `strata repo add <url>` is the install command.

**Acceptable hosting targets (all supported via `strata repo add`):**
- **GitHub / GitHub Enterprise** — private repo, branch protection for governance
- **Azure DevOps** — private repo, PAT or managed identity authentication
- **GitLab / GitLab Enterprise** — private repo, deploy tokens for CI
- **Gitea / Forgejo / self-hosted** — air-gapped environments
- **Any HTTPS git URL** — SSH or token auth

**Gaps vs Terraform Cloud registry** (where a git repo falls short):
- No web search/browse UI — discovery is `strata store list` or the repo README
- No automatic version indexing — versions are git tags (must be created manually)
- No built-in download stats, deprecation warnings, or input/output schema rendering

These gaps are acceptable for internal platform content. Teams already manage
modules via git and know what's in their repos. The Terraform Cloud registry
UX matters for public/community modules, not internal standards.

## Decision Drivers

- Enterprise teams have 5–50+ strata workspaces that should share standards
- Security: some content is proprietary (custom pricing, internal compliance rules)
- Platform teams want to publish-once, consume-everywhere
- Teams want to discover what's available without knowing exact repo URLs
- Must work with existing enterprise git hosting (Azure DevOps, GitHub Enterprise, GitLab)
- Must compose with community content (enterprise overrides community, not replaces)

## Considered Options

### Option A — Enterprise Store as a Typed Git Repository

A single git repository with a well-known directory structure and a manifest (`store.yaml`) that declares what content is available. Strata knows how to index it.

- Good: Uses existing `strata repo add/sync` infrastructure
- Good: Works with any git host (Azure DevOps, GitHub Enterprise, GitLab, Gitea)
- Good: Standard git workflows for contribution (PRs, code review, branch policies)
- Good: No new infrastructure — just a repo with conventions
- Bad: Discovery requires the repo URL (someone has to know about it first)
- Bad: Single repo can become a monolith for large orgs

### Option B — Federated Store Registry (Multiple Repos, One Catalog)

A lightweight registry file in the workspace (or org-level config) that points to multiple store repos, each typed. Like npm scopes or Docker registries.

- Good: Scales to large orgs (policies team owns policies repo, DBA team owns dimensions repo)
- Good: Independent versioning per content type
- Good: Fine-grained access control (not everyone needs access to everything)
- Bad: More configuration complexity
- Bad: Multiple repos to manage, more moving parts

### Option C — HTTP API Registry (Artifact Store)

A REST API service (like a private npm registry or Helm chart museum) that serves strata content.

- Good: Rich discovery (search, filtering, versioning endpoints)
- Good: Doesn't require git client on build agents
- Bad: Requires infrastructure to host and operate
- Bad: Overkill for most enterprises (adds operational burden)
- Bad: Diverges from strata's git-native philosophy

## Decision Outcome

Chosen: **Option A — Enterprise Store as a Typed Git Repository**, with Option B conventions for organizations that outgrow a single repo.

Rationale:
- Zero new infrastructure — enterprises already have private git repos
- Existing `strata repo add` mechanism is the transport layer
- A `store.yaml` manifest at the repo root makes it discoverable and self-describing
- The resolution order (local → enterprise → community → built-in) composes cleanly
- Large orgs can split into multiple store repos and register each one

### Consequences

- Good: No new services to deploy — works today with any git host
- Good: Platform teams can govern content via git branch policies and PR reviews
- Good: Composes with community — enterprise can override or extend community content
- Good: Teams can pin versions (git tags/refs) for stability
- Bad: No search/browse UX beyond `strata store list` (CLI-based discovery)
- Bad: Enterprise teams must maintain the store repo (but they'd maintain content anyway)
- Bad: Large stores may be slow to clone (mitigated by sparse checkout)

---

## Terminology

| Term               | Definition                                                                                        |
| ------------------ | ------------------------------------------------------------------------------------------------- |
| **Store**          | A git repository containing strata content with a `store.yaml` manifest                           |
| **Content type**   | A category of content in the store: `dimensions`, `policies`, `modules`, `templates`, `scenarios` |
| **Manifest**       | `store.yaml` at the repo root — declares what content types are available and where to find them  |
| **Store registry** | The list of stores configured in the workspace (`.strata/stores.yaml` or configuration)           |

---

## Detailed Design

### 1. Store Manifest (`store.yaml`)

Every enterprise store has a `store.yaml` at its root that describes the content available:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: store
meta:
  name: acme-platform-store
  labels:
    version: "2.1.0"
  annotations:
    description: "ACME Corp platform engineering standards"
    maintainer: "platform-engineering@acme.com"
    organization: "ACME Corporation"

spec:
  # What content types this store provides
  content:
    dimensions:
      path: dimensions/
      description: "Resource sizing standards maintained by the DBA and infra teams"
    
    policies:
      path: policies/
      description: "Compliance and security policies — mandatory for all deployments"
    
    modules:
      path: modules/
      description: "Approved Terraform module wrappers with enterprise defaults"
    
    templates:
      path: templates/
      description: "Workspace and deployment scaffolding templates"
    
    scenarios:
      path: scenarios/
      description: "Standard cost scenarios: acme-dev, acme-staging, acme-production"
  
  # Optional: default policies that MUST apply when this store is registered
  # (enforced automatically — teams can't skip them)
  enforce:
    policies:
      - security/require-encryption.yaml
      - compliance/data-residency.yaml
```

### 2. Store Repository Structure

```
acme-platform-store/
├── store.yaml                          ← manifest (required)
├── dimensions/
│   ├── azure/
│   │   ├── databases/
│   │   │   ├── azurerm_mssql_database.yaml
│   │   │   └── azurerm_cosmosdb_account.yaml
│   │   ├── compute/
│   │   │   └── azurerm_linux_virtual_machine.yaml
│   │   └── containers/
│   │       └── azurerm_kubernetes_cluster.yaml
│   └── aws/
│       └── databases/
│           └── aws_db_instance.yaml
├── policies/
│   ├── security/
│   │   ├── require-encryption.yaml
│   │   ├── no-public-endpoints.yaml
│   │   └── minimum-tls-version.yaml
│   ├── compliance/
│   │   ├── data-residency.yaml
│   │   └── backup-retention.yaml
│   └── cost/
│       └── budget-limits.yaml
├── modules/
│   ├── networking/
│   │   └── hub-spoke-vnet/
│   │       ├── module.yaml
│   │       └── README.md
│   └── databases/
│       └── standard-sql/
│           ├── module.yaml
│           └── README.md
├── templates/
│   ├── workspace-web-app/
│   │   ├── template.yaml
│   │   ├── workspace.yaml
│   │   ├── configuration.yaml
│   │   └── README.md
│   └── workspace-data-platform/
│       ├── template.yaml
│       └── ...
├── scenarios/
│   ├── acme-dev.yaml
│   ├── acme-staging.yaml
│   ├── acme-production.yaml
│   └── acme-peak-season.yaml
└── meta/
    ├── README.md
    ├── CONTRIBUTING.md
    └── CHANGELOG.md
```

### 3. Registering a Store

```bash
# Add an enterprise store (private git repo)
strata store add \
  --name acme-platform \
  --url https://dev.azure.com/acme/platform/_git/strata-store \
  --ref main \
  --output json

# Add with a specific version tag
strata store add \
  --name acme-platform \
  --url https://dev.azure.com/acme/platform/_git/strata-store \
  --ref v2.1.0 \
  --output json

# Add the public community store (optional — could be auto-registered)
strata store add \
  --name community \
  --url https://github.com/huybrechtsxyz/strata-community.git \
  --ref main \
  --output json

# Sync stores (pulls latest from refs)
strata store sync --output json

# List registered stores
strata store list --output json

# Browse a store's content
strata store browse --name acme-platform --output json
strata store browse --name acme-platform --type dimensions --output json
strata store browse --name acme-platform --type policies --provider azure --output json
```

### 4. Store Registry (`.strata/stores.yaml`)

Registered stores are persisted in the workspace state:

```yaml
# .strata/stores.yaml (managed by CLI — not hand-edited)
stores:
  - name: acme-platform
    url: https://dev.azure.com/acme/platform/_git/strata-store
    ref: v2.1.0
    path: .strata/stores/acme-platform    # local checkout path
    synced_at: "2026-07-10T14:30:00Z"
    manifest:
      content_types: [dimensions, policies, modules, templates, scenarios]
      enforce: [security/require-encryption.yaml, compliance/data-residency.yaml]

  - name: community
    url: https://github.com/huybrechtsxyz/strata-community.git
    ref: main
    path: .strata/stores/community
    synced_at: "2026-07-10T14:30:00Z"
    manifest:
      content_types: [dimensions]
      enforce: []
```

### 5. Resolution Order (Local Wins, Enterprise Overrides Community)

When strata resolves content (dimensions, policies, modules), it uses this priority:

```
1. config/{type}/...                    ← workspace-local custom (highest priority)
2. @{enterprise-store}/{type}/...       ← enterprise store
3. @{community-store}/{type}/...        ← community store
4. built-in (src/strata/.../built-in/)  ← strata defaults (lowest priority)
```

**Rules:**
- Local always wins — teams can override anything
- Enterprise beats community — org standards take priority over generic community content
- Multiple enterprise stores are resolved by registration order (first registered = highest priority)
- If the same content exists at multiple levels, the highest-priority version is used (no merge)

**Example — dimension resolution for `azurerm_mssql_database`:**

```
1. config/dimensions/azurerm_mssql_database.yaml    → found? USE IT, stop.
2. .strata/stores/acme-platform/dimensions/azure/databases/azurerm_mssql_database.yaml → found? USE IT, stop.
3. .strata/stores/community/azure/databases/azurerm_mssql_database.yaml → found? USE IT, stop.
4. src/strata/pricing/dimensions/built-in/azurerm_mssql_database.yaml → found? USE IT.
5. (not found anywhere → no dimensions for this resource type)
```

### 6. Enforced Policies

Enterprise stores can declare policies that are **automatically enforced** when the store is registered — teams cannot skip them:

```yaml
# In store.yaml
spec:
  enforce:
    policies:
      - security/require-encryption.yaml    # relative to policies/ dir in store
      - compliance/data-residency.yaml
```

When a workspace has this store registered, `strata validate` and `strata build run` will automatically include these enforced policies in the policy evaluation — alongside any workspace-local policies. This gives platform engineering a "push" mechanism: register the store, and compliance applies everywhere.

**Override / exemption:** Teams can declare exemptions in their workspace config for specific enforced policies (requires explicit opt-out with justification):

```yaml
# configuration.yaml
spec:
  store_exemptions:
    - store: acme-platform
      policy: compliance/data-residency.yaml
      reason: "This workspace deploys to a non-EU region by design (US-only service)"
      approved_by: "platform-engineering"
      expires: "2027-01-01"
```

### 7. Authentication

Enterprise stores are private git repos — they need authentication. Strata delegates to git's existing credential mechanisms:

| Git host          | Credential method                                |
| ----------------- | ------------------------------------------------ |
| Azure DevOps      | `az login` + git credential manager, or PAT      |
| GitHub Enterprise | `gh auth login` + git credential manager, or PAT |
| GitLab            | Personal access token or SSH key                 |
| Any git host      | SSH key, credential helper, or `.netrc`          |

Strata never stores credentials itself — it relies on `git clone` / `git pull` succeeding with whatever credential helper is configured. This is identical to how `strata repo add` works today.

**For CI/CD pipelines:**
```yaml
# Azure DevOps pipeline — use System.AccessToken for checkout
- script: |
    git config --global credential.helper store
    echo "https://x-access-token:$(System.AccessToken)@dev.azure.com" > ~/.git-credentials
    strata store sync --output json
```

### 8. Store Versioning and Pinning

Stores use git refs for versioning:

```bash
# Pin to a specific release
strata store add --name acme-platform --url ... --ref v2.1.0

# Use latest from main (auto-sync gets newest)
strata store add --name acme-platform --url ... --ref main

# Update the pin
strata store update --name acme-platform --ref v2.2.0
```

**Recommendation for enterprises:**
- Use tags (`v2.1.0`) for production workspaces — predictable, immutable
- Use `main` for development workspaces — always get latest standards
- CI pipelines should pin to tags for reproducible builds

### 9. CLI Commands

| Command               | Purpose                                            |
| --------------------- | -------------------------------------------------- |
| `strata store add`    | Register a new store                               |
| `strata store remove` | Unregister a store                                 |
| `strata store list`   | List registered stores with sync status            |
| `strata store sync`   | Pull latest from all stores (or `--name` for one)  |
| `strata store browse` | List available content in a store                  |
| `strata store status` | Show content resolution order and active overrides |
| `strata store info`   | Show manifest details for a store                  |

### 10. Integration with Existing Features

| Feature                        | Integration                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| **Cost dimensions (ADR-0031)** | Enterprise store provides dimensions; resolution order applies                |
| **Policy engine (ADR-0006)**   | Enforced policies from stores are added to the evaluation set                 |
| **Templates / scaffolding**    | `strata new workspace --template @acme-platform/web-app`                      |
| **Scenarios (ADR-0031)**       | Enterprise scenarios available as starting points                             |
| **Remotes (ADR-0010)**         | Store repos are NOT remotes — they're content sources, not artifact endpoints |
| **Validation**                 | `strata validate` checks store-enforced policies automatically                |

---

## Open Questions

1. **Should stores support partial sync (sparse checkout)?** Large stores with hundreds of dimension files may be slow to clone. Sparse checkout would let workspaces pull only what they need.

2. **Should there be a `strata store init` to scaffold a new store repo?** Platform teams would benefit from a generator that creates the directory structure and `store.yaml`.

3. **How to handle store content that depends on specific strata versions?** A dimension file using features from strata v2.0 shouldn't be consumed by strata v1.x.

4. **Should store content be validated on `store sync`?** Catch errors early — validate pulled content against schemas immediately.

5. **Namespace collisions:** If two enterprise stores provide the same dimension file, should it error or use priority order?

---

## Implementation Roadmap

### Phase 1 — Core Store Infrastructure
- `strata store add/remove/list/sync` commands
- `store.yaml` manifest parsing and validation
- Store registry persistence (`.strata/stores.yaml`)
- Content resolution order (local → enterprise → community → built-in)

### Phase 2 — Content Type Integration
- Dimension resolution from stores (ties into ADR-0031)
- Policy enforcement from stores (ties into ADR-0006)
- Scenario inheritance from stores

### Phase 3 — Templates and Scaffolding
- `strata new` reads templates from stores
- `strata store browse --type templates` with descriptions

### Phase 4 — Governance and UX
- `strata store status` — shows what content is active and from where
- Store exemptions for enforced policies
- VS Code extension: store content in tree view, browse/install flow

---

## Relationship to Community Content

The enterprise store and community content are the **same mechanism** at different access levels:

| Aspect              | Community                           | Enterprise                                       |
| ------------------- | ----------------------------------- | ------------------------------------------------ |
| Visibility          | Public                              | Private (auth required)                          |
| Governance          | Open PR process                     | Internal platform team                           |
| Hosting             | github.com                          | Azure DevOps / GitHub Enterprise / GitLab        |
| Content types       | Primarily dimensions                | All types (dimensions, policies, templates, ...) |
| Enforce policies    | Never                               | Can enforce via `store.yaml`                     |
| Resolution priority | Lower                               | Higher (enterprise overrides community)          |
| Registration        | `strata store add --name community` | `strata store add --name acme-platform`          |

Both are just git repos with a `store.yaml` manifest. The distinction is access control and priority — not implementation.
