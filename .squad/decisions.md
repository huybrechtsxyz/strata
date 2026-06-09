# Squad Decisions

## Active Decisions

### 2026-05-19 — Separate VS Code task sets for SDK vs config repos
- **Decision:** VS Code `tasks.json` in configuration/operator repos must NOT include SDK development tasks (`Check: lint + format + types`, `uv run strata`). Operator repos contain only `strata` CLI tasks: `strata: validate`, `strata: deploy run`, `strata: build run`, and a generic `strata` fallback.
- **Rationale:** Config repo users are operators, not SDK developers. The platform SDK's `tasks.template.json` is for SDK dev workspaces and correctly retains SDK tasks.
- **Implications:** Any config/operator repo generated or updated by `xyz init` or related scaffolding uses operator-focused tasks only. The `configFile` promptString (default `@haven/deploy/deploy-prd.yaml`) is the standard input for file-targeting tasks in config repos.
- **Proposed by:** Linus

### 2026-05-19 — README Quick Start consolidation (pending Danny review)
- **Decision:** Collapsed `## Quick Install` and `## Quick Start` in `README.md` into a single `## Quick Start` section (4 commands: install, init, validate, deploy). Dev-install detail (`uv sync`) retained as inline note; deeper workflow lives in `docs/platform/getting-started.md`.
- **Rationale:** Old Quick Start (6 commands) was overwhelming for first-time readers. Two separate sections caused scroll/redundancy. New Getting Started guide carries the full workflow.
- **Implications:** Users needing dev install detail should follow Getting Started guide. Consider removing any stale `docs/README.md#quick-start` references. **Requires Danny's review and acceptance.**
- **Proposed by:** Reuben

### 2026-04-22 — CLI work-path resolution strategy
- **Decision:** Resolve work-path via: `--work-path` flag > `STRATA_WORK_PATH` env var > walk up from CWD looking for `.strata/` > error
- **Rationale:** Local dev needs zero-friction (walk from CWD); CI/CD needs explicit control (flag or env var)
- **Implications:** All commands receive `work_path` via `ctx.obj` — never pass it as an argument between services

### 2026-04-22 — CLI preferences stored in workspace config
-- **Decision:** Preferences (output format, verbosity) stored in `.strata/cli.yaml` via `strata config set`. Env vars (`STRATA_OUTPUT`, etc.) override. Explicit flags override those.
- **Rationale:** Workspace-scoped defaults are more ergonomic than global user config; CI/CD uses env vars
-- **Implications:** `main()` loads `.strata/cli.yaml` into Click `default_map` before any subcommand runs

### 2026-04-22 — Python CLI only (no extension/service yet)
- **Decision:** Build the Python CLI first. VS Code extension and service variants are out of scope.
- **Rationale:** Validate the core workflow before multiplying surfaces
- **Implications:** No web server, no websocket, no VS Code API dependencies in the codebase

### ~~2026-04-23 — CLI surface for solution management: `xyz solution <verb>`~~ (SUPERSEDED 2026-05-05)
- **Superseded by:** 2026-05-05 flat CLI structure decision (see below)
- ~~Decision: Use `xyz solution init`...~~

### ~~2026-05-04 — CLI surface for repository management: `xyz solution repo <verb>`~~ (SUPERSEDED 2026-05-05)
- **Superseded by:** 2026-05-05 flat CLI structure decision (see below)
- ~~Decision: Use `xyz solution repo add|remove|list`...~~

### ~~2026-05-05 — Flat top-level CLI structure (no solution wrapper)~~ (SUPERSEDED 2026-05-19)
- **Superseded by:** 2026-05-19 `sln` group for workspace lifecycle (see below)

### 2026-05-19 — Introduce `sln` group for workspace lifecycle (supersedes 2026-05-05)
- **By:** Danny (architecture review)
- **Supersedes:** 2026-05-05 — Flat top-level CLI structure decision
- **Decision:** Introduce `xyz sln` as a dedicated command group for workspace lifecycle operations:
  - `xyz sln init`    ← replaces flat `xyz init`
  - `xyz sln clean`   ← replaces flat `xyz clean`
  - `xyz sln status`  ← replaces flat `xyz status`
  - `xyz sln export`  ← new command (Option C — save workspace as scaffold template)
  - `xyz config` stays at top level (workspace preferences, not lifecycle)
  - All other groups (repo, profile, ref, context, build, deploy, validate, schema, audit, tools, values, new) remain flat and unchanged.
- **Why this differs from the rejected `solution` wrapper:** The 2026-05-05 rejection was about wrapping ALL commands under a `solution` noun. That added depth with no clarity gain. This proposal is narrower: only the 4 commands that have always been "workspace lifecycle orphans" (flat commands with no group) move to `sln`. Everything else stays flat.
- **Rationale:** `init`, `clean`, `status` have always operated on the same noun (the solution workspace) but had no shared group. `sln export` naturally belongs with `sln init`. `sln` is an established abbreviation (Visual Studio, dotnet CLI) — widely understood in DevOps tooling. Pre-release: no production breakage risk.
- **Implications:** `cli.py` removes flat registrations for `init`, `clean`, `status` and adds `sln_group`. `cli_sln.py` is the new group wiring file. Underlying command implementations (InitSolutionCommand, CleanSolutionCommand, StatusCommand) are unchanged. All tests referencing flat `init`/`clean`/`status` commands must be updated. `getting-started.md` must be updated. copilot-instructions.md registered command list must be updated.

### 2026-05-05 — `build` and `deploy` commands deferred
- **Decision:** `xyz build` and `xyz deploy` are deferred. Not in scope for the current milestone.
- **Rationale:** Core workspace management (init, repo, profile, ref) must be stable first.
- **Implications:** No build/deploy code. When added, they register as flat top-level commands in `cli.py`.

### 2026-05-21 — Build output folder at workspace root, not inside `.strata/`
- **By:** Vincent Huybrechts
- **Decision:** Build output goes to `work_path/build/` — not `work_path/.strata/build/`. `DEFAULT_BUILD_PATH = "build"` set in `utils/config.py`. No `--build-path` flag or `STRATA_BUILD_PATH` env var override.
- **Rationale:** `.strata/` is internal CLI state (registry, preferences). Build artifacts are generated outputs for external tools (terraform, ansible) — a different concern. Ecosystem convention puts generated outputs at workspace root (`build/`, `target/`, `dist/`).
- **Implications:** `base_build_command.py` and `base_deploy_command.py` must use `DEFAULT_BUILD_PATH` instead of `SolutionController.get_state_dir(...) / "build"`. Haven's `.gitignore` needs `build/` added. `.strata/.gitignore` template should remove `build/` if present. See `.squad/agents/danny/todo-build-folder-migration.md`.

### 2026-05-28 — Helm: no new `kind`, use `deployment` with `stage.type = "helm"`
- **By:** Danny (architecture review)
- **Decision:** No `kind: helm-deployment`. Helm is modelled as `ProvisionerType.HELM` on an existing `DeploymentStageModel`. Add `WorkspaceHelmModel` to `workspace_model.py` (fields: `name`, `release_name`, `chart`, `chart_version`, `repo_name`, `repo_url`, `namespace`, `values_files`, `values`, `kube_context`, `kubeconfig`, `wait`, `atomic`, `timeout`). `WorkspaceSpecModel` gets `helm: Optional[List[WorkspaceHelmModel]]`. Stage-level overrides deferred to follow-up.
- **Rationale:** `DeploymentModel` already handles multi-provisioner pipelines by design. `kind: helm-deployment` would be over-engineering. Helm rides `build`, `deploy run`, `deploy destroy`, `deploy status` — no new commands needed.
- **Implications:** `ProvisionerType.HELM` added to `common_models.py`. No new CLI commands. `strata build run` should `helm pull`; `strata deploy run` runs `helm upgrade --install`.

### 2026-05-28 — Helm integration: `IInfrastructureTool`, not a new protocol
- **By:** Basher (DevOps Integrations)
- **Decision:** `HelmIntegration` implements `IInfrastructureTool` (`init` → `helm dependency update`; `plan` → `helm diff upgrade`; `apply` → `helm upgrade --install`). Singleton key is `config.name`. No `HelmBuilder` in Phase 1. `plan` step is advisory — absent `helm-diff` plugin emits a warning and returns success (does not block `apply`). `destroy` requires `force=True`.
- **Rationale:** `IPackageManager` is premature — add it only when a second package manager requires the abstraction. `helm diff` advisory matches Ansible's graceful degradation pattern.
- **Implications:** New files: `integrations/helm.py`, `deployers/helm_deployer.py`. `integrations/__init__.py` and `docs/platform/integrations.md` updated.

### 2026-05-29 — `github` secret store: thin subtype (Option C)
- **By:** Danny (architecture review) + Basher (mechanics confirmation)
- **Decision:** Add `SecretStoreType.GITHUB = "github"`. Resolution delegates to `os.environ.get(str(item.value))` (GitHub Actions injects secrets as env vars before the job starts). No integration class. Add branch in `value_controller._resolve_secret()` parallel to `ENVIRONMENT`. Add `model_validator` rejecting `version` field when `store == "github"` (GitHub secrets are unversioned). Emit `logger.warning` if `GITHUB_ACTIONS != "true"` — non-fatal, informational only.
- **Rationale:** Option A (alias coercion) destroys provenance. Option B (integration class) adds complexity with zero functional gain — there is no external process to call. Option C preserves `secret.store.value == "github"` throughout the object lifecycle, keeps JSON schema accurate, and allows `allowed_secret_stores` policy matching on the enum value.
- **Implications:** `store_models.py`, `value_controller.py`, `configuration_model.py` updated. `GITHUB_ACTIONS` warning is advisory — CI quiet mode can suppress it.
- **Status:** Implementation complete (Linus, 2026-05-29)

### 2026-06-01 — HelmBuilder: per-module output (not per-namespace)
- **By:** Linus
- **Decision:** `HelmBuilder` writes one `values.yaml` + `meta.yaml` pair per module at `{build_path}/{namespace}/{module}/values.yaml` and `{build_path}/{namespace}/{module}/meta.yaml`.
- **Rationale:** Helm deploys are release-scoped (`helm upgrade` per chart), not namespace-scoped. `meta.yaml` carries `releaseName` and `namespace` — per-module properties with no meaningful per-namespace aggregation. Contrast with `ComposeBuilder` (per-namespace, because all containers share one `docker compose up`).
- **Implications:** `HelmDeployer` (deferred) iterates `{build_path}/**/{module}/meta.yaml` to discover releases. Modules with no services are skipped silently.

### 2026-06-09 — DNS kind: 4 architecture decisions
- **By:** Danny (architecture review)
- **Decision 1 — `spec.provider`:** INCLUDE as `Optional[str]` with enum validation (`inwx`, `cloudflare`, `route53`). Single-provider workspaces omit it; multi-provider workspaces are self-documenting.
- **Decision 2 — workspace field name:** `dns_zones` (not `dns`). `dns` is too ambiguous — `_zones` makes plurality and unit explicit. Follows firewall precedent of plural nouns but adds the qualifier for clarity.
- **Decision 3 — merge strategy:** Zone merge by name (last-definition-wins). Record merge by `(name, type)` RRset replacement — entire RRset replaced (not per-value dedup). Matches Terraform provider semantics; prevents impossible states (two A records for `@` with different IPs).
- **Decision 4 — tfvars output shape:** Nested `dns_zones → attachment_name → {provider, zones: {domain → {ttl, records: [{name, type, value, ttl, priority}]}}}`. Records serialized with null fields included (`exclude_none=False`) — uniform per-record schema prevents type-union complexity in Terraform variable definitions.
- **Implications:** `DnsModel.spec.provider` validated against enum. `workspace_model.py` uses `dns_zones` field name. `DnsService.merge_dns()` merges at `(name, type)` RRset level. `terraform_builder._build_dns_vars()` uses `model_dump(exclude_none=False)` for records.

### 2026-06-09 — DNS implementation deviations from firewall pattern
- **By:** Linus
- **Decision 1 — `DnsMetaModel.labels` optional:** Made fully optional with `None` default (unlike `FirewallMetaModel` which requires labels). DNS config files are typically simpler.
- **Decision 2 — `PlatformDnsModel` extends `BaseModel`:** Not `PlatformBaseModel` (extra="forbid") — same pattern as `PlatformFirewallModel`. Zone structure needs no flattening.
- **Decision 3 — Records serialized with explicit nulls:** `exclude_none=False` for record `model_dump()` — differs from firewall `exclude_none=True`. Deliberate, matches Danny's Decision 4 above.
- **Decision 4 — `dns_zones` field name consistent:** Used across `WorkspaceSpecModel`, `PlatformSpecModel`, and tfvars output key — aligns with Danny's Decision 2.
- **Decision 5 — `platform_builder.py` not modified:** DNS wiring (loading `dns_zones` from workspace, building `PlatformDnsModel`) deferred to Basher per task scope.
- **Implications:** `strata validate dns-file.yaml`, `strata schema show dns`, and `dns.auto.tfvars.json` generation all work. Platform builder DNS population is a Basher follow-up.

## Governance

- All meaningful architectural changes require a decision entry here
- Danny triages and records decisions — other agents propose via decisions/inbox/
- Keep decisions focused on direction, not implementation detail

- All meaningful architectural changes require a decision entry here
- Danny triages and records decisions — other agents propose via decisions/inbox/
- Keep decisions focused on direction, not implementation detail
