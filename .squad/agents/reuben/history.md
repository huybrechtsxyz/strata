# Reuben — History

## Core Context

Docs / Technical Writer for strata. Sphinx docs, Markdown guides, CLI reference.
User: Vincent Huybrechts. Stack: Sphinx, reStructuredText, Markdown.
Key paths: `docs/`, `docs/conf.py`, `docs/index.rst`, `docs/cli-preferences.md`, `docs/SQUAD.md`.

### 2026-06-09 — DNS kind documentation

- **`docs/config/dns.md`** — Created new reference doc. Structure mirrors `firewall.md`: What it is / When to use; top-level schema block; three field tables (top-level, zone, record); record type reference table (A, AAAA, CNAME, MX, TXT, SRV, NS, PTR, CAA) with trailing-dot callout and priority notes; complete real-world example (huybrechts.xyz zone with A, CNAME, dual MX, SPF TXT, DMARC TXT, CAA); workspace linking pattern (`spec.dns_zones`); build output description (`dns.auto.tfvars.json`); validation rules table; best practices.
- **`docs/config/readme.md`** — Added `dns.md` to the Resources kind listing, positioned before `firewall.md` (alphabetical order: d before f).
- **`docs/platform/commands.md`** — Two changes: (1) Added `dns` to the **Valid kinds** list in `schema get KIND` (alphabetical: between `deployment` and `environment`); (2) Added `strata new dns my-zones --path config/dns/` to the `strata new` usage examples.
- **`docs/platform/workflow.md`** — Added a callout block in Phase 6.2 (Full build) explaining workspace-level resource kinds: firewall rules (`spec.firewalls` → `firewalls.auto.tfvars.json`) and DNS zones (`spec.dns_zones` → `dns.auto.tfvars.json`) with cross-links to the config docs. Firewall was not previously mentioned in workflow.md — the callout introduces both kinds together.
- **Key structural decision:** firewall was absent from workflow.md. Rather than adding only DNS and leaving firewall undiscovered, the callout introduces the pattern for both kinds simultaneously — more useful to a reader following the workflow guide.

### 2026-05-19 — README pitch rewrite + Getting Started guide

- **`README.md`** — Replaced the opening paragraph (generic feature description) and the Quick Install + Quick Start sections with: (1) a one-paragraph pain statement pitch answering "why not just Terraform?"; (2) a minimal 4-command Quick Start block; (3) a link to the new Getting Started guide. Automation and License sections left untouched.
- **`docs/platform/getting-started.md`** — Created new file. Target reader: DevOps engineer, first contact with the tool. Structure: Prerequisites → Install (pipx + dev) → Init → File structure → Repo registration → Profiles → Validate → Deploy → Troubleshooting (audit, --verbose, JSON output) → Persist preferences → Next steps. Kept to ~150 lines (well under 200 limit).
- **Audience notes:** Operators scan — used tables, short paragraphs, and code blocks throughout. Avoided marketing language in the guide itself; saved the pitch for README only.
- **Decisions:** Chose to fold the old Quick Install and Quick Start into a single simplified Quick Start rather than maintaining two separate sections. Wrote to decisions/inbox for Danny's review.

### 2026-05-18 — Devcontainer scaffolding added to `xyz init`

- **`docs/platform/commands.md`** — Rewrote the `## init` section description and added a "Files created" table listing all five scaffolded paths (`.platform/project.json`, `.platform/cli.yaml`, `.platform/logging.yaml`, `.devcontainer/devcontainer.json`, `.devcontainer/post-create.sh`). Added a "Dev container" callout about "Reopen in Container".
- **`docs/platform/workflow.md`** — Extended the "Creates:" bullet list in Phase 1 to include both `.devcontainer/` files and a note about VS Code / Codespaces.
- **`docs/README.md`** — Added a one-line inline comment in the Quick Start `xyz init` step pointing users to "Reopen in Container".
- Only touched the three files directly affected; no new files created.

### 2026-05-19 — sln group docs and instructions update

- **`docs/platform/getting-started.md`** — Updated all `xyz init` references to `xyz sln init`. Added a `xyz sln export` section documenting the workflow for saving a workspace as a scaffold template.
- **`.github/copilot-instructions.md`** — Added `sln` to the registered CLI command groups list. Canonical list now includes: `sln init`, `sln clean`, `sln status`, `sln export` under the `sln` group. Flat `init`, `clean`, `status` are no longer registered directly.
- **Key convention:** `xyz sln init` is the canonical entry point for workspace creation in all documentation. Any doc referencing the old flat `xyz init` must be updated.

### 2026-05-29 — Document `github` as a valid secret store

- **`docs/config/configuration.md`** — Added a new "## Secret Stores" section (before Notes) with: a full store-type reference table (`constant`, `environment`, `github`, `azure-keyvault`, `bitwarden`, `vault`, `infisical`); a dedicated `### github — GitHub Actions secrets` subsection with YAML example, uppercase normalization note, local development workaround, `version` not-supported callout, and `allowed_secret_stores` production policy snippet.
- **`docs/platform/integrations.md`** — Updated the `secrets` row in the Capability Protocols table to note that `github` and `environment` are built-in resolvers, not integrations. Added a blockquote callout below the table pointing to the configuration.md reference.
- **Key fact to preserve:** `store: github` is NOT an integration — it is a built-in resolver in `ValueController` that reads `os.environ.get(value.upper())`. GitHub Actions injects secrets as env vars before each step. `GITHUB_ACTIONS != "true"` triggers a warning; missing env var returns an error.
- `version` field raises a validation error for `store: github` (enforced in `SecretStoreModel` via `model_validator`).

### 2026-06-01 — HelmBuilder documentation

- **`docs/platform/builders.md`** — Added `HelmBuilder` to the overview table. Appended a new `## HelmBuilder` section. Coverage: purpose sentence + security callout; output path pattern (two files per module: `values.yaml` + `meta.yaml`); service key naming table (same prefix rule as ComposeBuilder); environment variable source types table (value/var/secret/feature → literal or `${KEY}`); PVC persistence block (storage_class mounts → `persistence.{name}` with storageClass/accessMode/size); `meta.yaml` contents table with fallback defaults; full authentik YAML module example with two generated outputs; deploy-time `${KEY}` injection note (`--set` or secrets values file); `configuration:` escape hatch with example; three-phase pipeline summary.
- **Style decisions:** Mirrored `## ComposeBuilder` section structure exactly — security callout first, then reference tables, then example with generated output, then pipeline. Placed `meta.yaml` fields in a table (source + fallback) for quick scanning.
- **Key fact to preserve:** Service key naming rule is identical to ComposeBuilder: `{module}-{service}` unless module name equals service name, in which case just `{service}`. Only mounts with `storage_class` produce a `persistence` block — bind mounts and volume refs are not emitted. `releaseName` falls back to module name; `namespace` falls back to the strata namespace name.

### 2026-06-01 — ComposeBuilder documentation

- **`docs/platform/builders.md`** — Appended a new `## ComposeBuilder` section. Updated the overview table to include the new builder. Coverage: purpose statement; output path pattern; service naming rules table; environment variable source types (value/var/secret/feature) and what gets emitted; volume conventions (named vs bind); healthcheck type mapping; a full YAML module example with two services; the generated `docker-compose.yml` for that example; `.env` injection note; `configuration:` escape hatch with example; three-phase pipeline summary.
- **Style decisions:** Kept structure parallel to existing `TerraformBuilder` section — overview → security callout → tables → examples → three-phase pipeline. Used tables for naming rules, env sources, volumes, and healthchecks (scannable for ops engineers). Placed generated output directly after the input YAML so the transformation is immediately visible.
- **Key fact to preserve:** `after_build` always returns `True` — no compose modules in a namespace is intentionally not an error. Secret/var/feature references are always emitted as `${KEY}` tokens, never resolved. Volume naming pattern: `{namespace}_{module}_{volume_ref}`.

### 2026-06-01 — Helm deployer/integration documentation audit

**Audit scope:** `docs/platform/deployers.md`, `docs/platform/integrations.md`, `docs/platform/commands.md`, `docs/config/deployment.md`.

**Key facts about the Helm implementation (for use when writing the docs):**
- `HelmDeployer` lives in `deployers/helm_deployer.py`. All 8 steps supported: `setup` (helm repo update), `check` (helm lint per module), `plan` (helm upgrade --dry-run --install), `apply` (helm upgrade --install -n {ns} -f values.yaml {release} {chart}), `destroy` (helm uninstall, requires force=True), `plan_destroy` (helm get manifest), `output` (helm get values), `show_plan` (no-op).
- `validate_workspace` iterates all namespace/module pairs in the build path; only processes modules where `module.spec.type == ServiceDeployerType.HELM`. Reads `values.yaml` + `meta.yaml` per module from build output.
- `validate_environment` calls `HelmIntegration.ensure_available()`.
- Chart source resolution: `meta.yaml` provides `releaseName` and `namespace`. Chart ref comes from `module.spec.source`: chart_repository + chart_name → registry chart; repository + source_path → local chart path.
- `resolved_values` injected at plan/apply/destroy via `--set` flags (or secrets values file).
- `HelmIntegration` uses `CAPABILITIES = [IInfrastructureTool]` — NOT `IContainerTool`. The integrations.md capability table currently lists Helm under `IContainerTool`, which is wrong.
- Stage type token is `"helm"` (`ProvisionerType.HELM`). Written in deployment YAML as `type: helm` in a stage.
- `ServiceDeployerType.HELM` is the module-level deployer type (used by HelmBuilder and HelmDeployer).

**Gaps found:**
- `deployers.md`: No `HelmDeployer` row in overview table. No `## HelmDeployer` section. Zero mentions of helm. **Biggest gap.**
- `integrations.md`: `HelmIntegration` missing from overview table entirely. No per-integration `### Helm` reference section. Wrong capability classification in the Protocols table (Helm listed under `IContainerTool`, should be `IInfrastructureTool`).
- `deployment.md`: No mention of `type: helm` as a valid stage type. Workflow description references only "Terraform/manifests". The deploy command note says "requires Terraform CLI" with no Helm caveat.
- `commands.md`: Minor — deploy section note references only Terraform; no `type: helm` example. Incidental Helm mentions exist (devcontainer description, tools section) but nothing about Helm deployment.

**Priority order for updates:** (1) `deployers.md` — missing entire section; (2) `integrations.md` — missing entry + wrong capability; (3) `deployment.md` — no stage type documented; (4) `commands.md` — minor note update.

### 2026-06-02 — ComposeDeployer documentation

- **`docs/platform/deployers.md`** — Added `ComposeDeployer` row to overview table (between AnsibleDeployer and HelmDeployer, alphabetical order). Inserted new `## ComposeDeployer` section immediately before `## HelmDeployer`. Section structure mirrors HelmDeployer: one-paragraph description + Swarm mode callout; step → Docker command table (all 8 steps); `### validate_workspace` (build path discovery, missing files silently skipped, empty-found warning); `### validate_environment` (DockerIntegration.ensure_available); `### Deployment YAML example`; "See also: DockerIntegration" cross-reference.
- **`docs/config/deployment.md`** — Added `compose` row to the `## Provisioner Stage Types` table (between `ansible` and `helm`, alphabetical). Notes Docker Swarm requirement and links to ComposeDeployer section.
- **`docs/platform/integrations.md`** — Added "Used by `ComposeDeployer` for Docker Stack deployments." sentence at the end of the Docker section (before the `---` separator), matching the Helm section's "Which deployer uses it" pattern without adding a new heading (targeted minimal edit as instructed).
- **Key structural decision:** Placed ComposeDeployer before HelmDeployer in deployers.md to maintain alphabetical order (Ansible → Compose → Helm → Script → Terraform). The HelmDeployer section was added at the end yesterday — this insertion restores alphabetical ordering.
- **Swarm mode callout:** Added as a blockquote directly after the opening paragraph in `## ComposeDeployer` — DevOps engineers running plain Docker Engine without swarm init will get the error immediately before they waste time debugging step failures.

### 2026-06-02 — Helm deployer/integration documentation (implementation)

- **`docs/platform/deployers.md`** — Added `HelmDeployer` row to overview table (after `AnsibleDeployer`). Appended new `## HelmDeployer` section with: one-paragraph description; step → Helm command table (all 8 steps); `### validate_workspace`, `### validate_environment`, `### Chart source resolution`, and `### Deployment YAML example` subsections matching TerraformDeployer/AnsibleDeployer section structure.
- **`docs/platform/integrations.md`** — Three changes: (1) Fixed capability table — moved Helm from `IContainerTool` to `IInfrastructureTool` row; (2) Added `HelmIntegration` row to the overview table after `DockerIntegration`; (3) Added `### Helm` per-integration reference section (after Docker, before Bitwarden) with env vars, auth methods, which deployer uses it, and troubleshooting tip.
- **`docs/config/deployment.md`** — Two changes: (1) Updated Deployment Workflow step 5 from "Create Terraform/manifests" to "Create Terraform/manifests/Helm values"; (2) Added new `## Provisioner Stage Types` section (between Deployment Workflow and Source Types) with a 3-column table listing all 5 stage types (`terraform`, `opentofu`, `ansible`, `helm`, `script`) and a cross-reference to deployers.md.
- **`docs/platform/commands.md`** — Updated the `## deploy` section blockquote note from Terraform-specific language to general: now references all provisioner CLIs and directs users to `strata tools status`.
- **Key structural decision:** `deployment.md` had no stage types section at all — added `## Provisioner Stage Types` as a new section rather than shoehorning content into the existing schema block. This is the canonical location in deployment.md for stage type documentation.
- **Bug fixed:** `HelmIntegration` was classified under `IContainerTool` in the capabilities table — corrected to `IInfrastructureTool` (matches `CAPABILITIES = [IInfrastructureTool]` in `src/strata/integrations/helm.py`).
