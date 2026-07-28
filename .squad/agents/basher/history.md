# Basher — History

## Core Context

DevOps Integrations for strata. Owns integrations/, build pipeline, deploy pipeline.
User: Vincent Huybrechts. Stack: Python 3.13, uv, subprocess, Terraform, Ansible, Git, Docker, Azure, HashiCorp.
Key paths: `src/strata/integrations/`, `models/deployment_model.py`.

## Learnings

### 2026-07-28 — Audit log redaction gap: full argv (incl. `--value` secrets) logged unredacted

- Confirmed Option D (docs-only secret-derive recipe) works today with existing CLI, no code needed. Independently found: `base_command.py` (~line 565-570) audits every command with `target=" ".join(sys.argv[1:])` — full unredacted argv, so `strata secret put KEY --value <plaintext>` writes the plaintext into `.strata/deploy-log/*.json` (and possibly `audit resend`). Option-independent, pre-existing issue; flagged in decisions.md as an open finding, not yet fixed.

### 2026-06-15 — Policy template blocks in config/data files
- `xyz-config.yaml` has no top-level `lifecycle:` or `security:` section in `spec:` — policies block was appended after the `topologies:` section (end of file).
- `src/strata/data/configuration.yaml` is the scaffold template for `strata new configuration` — it is minimal (only a `configuration:` sub-key). Policies block was appended after the existing `configuration:` block.
- Policy block is fully commented out in both files; `policies:` is optional and defaults to empty — no model changes needed.
- `src/strata/data/` also contains `logging.yaml`, `guide-hints.yaml`, and a `help/` directory — check these if new top-level spec fields are added in future.

### 2026-04-22 — Config cross-repo `@repo/path` reference resolution
- Pattern `@xyz_configuration/stack/xyz-ws-platform.yaml` is resolved in `utils/system.py::resolve_path(base, target, repo_map={})`.
- `repo_map` = `{repo_name: deploy_path}` built by `ConfigurationService.get_repo_map()` from `spec.repositories[].deploy_path`.
- All three repos in `xyz-config.yaml` are type=`bundled` — they are copied from `repo/<name>` inside the work-path into `<work_path>/<deploy_path>` by `RepositoryController.fetch_all_repositories()`.
- `BaseService._resolve_file_ref()` and `BaseService._validate_file_refs()` call `resolve_path` with the repo_map whenever a field starts with `@`.
- **Gap**: `xyz-ws-platform.yaml` has many `@xyz_configuration/...` references that are parsed by `WorkspaceService` at load time via `load_workspace_services()`. That path is validated at load time; actual file reads happen at build time — no build-time resolver exists yet.

### 2026-04-22 — Integrations layer: what's real vs stub
- `BaseIntegration`: full singleton, availability check, version detection, `_run_integration()` subprocess wrapper — all real.
- `IntegrationFactory`: auto-registers git, docker, terraform, bitwarden, azure-keyvault, azure-appconfig, consul, vault at import time — real.
- `IntegrationRegistry`: singleton, `register_integration`, `get_integration`, capability queries — real.
- `IntegrationController`: status queries, availability checks — real.
- `GitIntegration`: `clone`, `pull`, `get_current_branch` — real and callable.
- `TerraformIntegration`: `init`, `validate`, `plan`, `apply`, `destroy` — all real with proper arg building.
- `DockerIntegration`, `BitwardenIntegration`, etc.: registered but NOT reviewed — assumed partially real.
- **Gap**: No `TerraformIntegration.workspace_select()` or `TerraformIntegration.output()` — needed for multi-workspace Terraform.

### 2026-05-28 — Helm integration design analysis
- Helm maps to `IInfrastructureTool` (no new `IPackageManager` protocol in Phase 1). `init→dependency_update`, `plan→diff upgrade`, `apply→upgrade --install`.
- `HelmIntegration`: `COMMAND = "helm"`, singleton keyed by `config.name`. Version from `helm version --short` → regex `v(\d+\.\d+\.\d+)`.
- Extra methods outside protocol: `install`, `upgrade`, `uninstall`, `diff`, `lint`, `template`, `repo_add`, `repo_update`, `dependency_update`, `status`.
- Env vars: `KUBECONFIG` (required), `HELM_NAMESPACE`, `HELM_REGISTRY_CONFIG`, `HELM_DATA_HOME`.
- No `HelmBuilder` in Phase 1 — charts fetched from repos at deploy time, no local file merge phase.
- `HelmDeployer`: `setup→repo_update+dependency_update`, `check→lint`, `plan→diff` (advisory, graceful if helm-diff plugin absent), `apply→upgrade --install`, `destroy→uninstall` (requires force=True).
- Files: CREATE `helm.py` integration + `helm_deployer.py`; MODIFY `integrations/__init__.py` + `docs/platform/integrations.md`.
- Decision filed: `.squad/decisions/inbox/basher-helm-integration.md`

### 2026-04-22 — Build pipeline: what exists vs what's missing
- **Exists**: YAML loading + Pydantic validation (ConfigurationService, DeploymentService, WorkspaceService). `@repo/path` path resolution. `DeploymentService.get_build_path()` returns `build/<name>-<version>` path. `RepositoryController.fetch_all_repositories()` copies bundled repos into work_path. `LifecycleController.execute_configuration_phase()` runs scripts per phase.
- **Missing (build pipeline)**: No `BuildController` or `build` command. No Terraform file merge/assembly from `xyz_infrastructure/terraform/` into a build output directory. No `xyz build` CLI entry point — it's commented out in `cli.py` along with `cli_builders.py` (not yet created). No artifact staging logic.

### 2026-04-22 — Deploy pipeline: what exists vs what's missing
- **Exists**: `DeploymentService.load_deploy_services()` loads workspace + environment + all infra services. `DeploymentService.apply_environment_overrides()` merges env overrides into workspace resources/providers/modules. Stage model (`DeploymentStageModel`) with DAG dependency validation. `TerraformIntegration.init/plan/apply/destroy` are callable.
- **Missing (deploy pipeline)**: No `DeployController` — no code that iterates `spec.stages` and dispatches to the right integration. No `xyz deploy` CLI entry point (also commented out). No stage-provisioner resolution (the `type=infrastructure` → auto-select Terraform convention exists only as a YAML comment, no Python logic). No Terraform var-file generation from workspace/environment model. No Docker Swarm/Ansible dispatch for service stages.

### 2026-04-22 — xyz-deploy-prd.yaml structure
- Stages list is **incomplete** — only `infrastructure` stage defined, service stages (traefik, swarm deploy) not yet added.
- `spec.workspace.file = "@xyz_configuration/stack/xyz-ws-platform.yaml"` — workspace is cross-repo ref.
- `spec.environments = ["@xyz_configuration/environments/xyz-env-prd.yaml"]` — environment is cross-repo ref.
- Both will be resolved via repo_map at `load_deploy_services()` time.

### 2026-04-22 — DeploymentService scope
- `DeploymentService` is a **loader/validator only** — it loads YAML, validates structure, merges environment overrides, and exposes infra services as read-only.
- It does NOT execute anything — no subprocess calls, no integration calls.
- Execution logic belongs in a yet-to-be-written `DeployController`.
