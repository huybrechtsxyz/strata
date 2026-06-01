# Danny — History

## Core Context

Lead / Architect for xyz-platform. Python DevOps CLI tool built with Click + Pydantic.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.

## Learnings

### 2026-04-22 — Full architecture review

**CLI:** `cli.py` is an empty shell — all 7 command groups commented out, zero active subcommands.
No command files exist in `commands/` except `base_command.py` and `cli_common.py`.
The "session" terminology in cli.py comments diverges from the "project" terminology now intended.

**Models:** Complete and solid. 16 model files covering every YAML kind. `ConfigurationModel` is the
richest — providers, topologies, layering, security, repositories all Pydantic-validated.
`ProjectModel` handles the `.xyz_platform/project.json` workspace state file.

**Services:** Very solid. `BaseService` has 2-phase validate (Pydantic + `_validate_dynamic`),
load-with-cache via `service_cache.py`, lifecycle hooks. All domain services implemented:
`ConfigurationService` (singleton, deep-merge glob-pattern loading), `WorkspaceService`,
`DeploymentService`, `EnvironmentService`, `ProjectService`, `PlatformService`.

**Controllers:** 4 implemented — `IntegrationController`, `LifecycleController`,
`RepositoryController`, `ValueController` (with `inject_tf_vars` context manager).
Missing: `BuildController` and `DeployController`.

**`@repo/path` resolution:** Solid. `resolve_path()` in `utils/system.py` handles `@repo_name/rest`
via `repo_map`. `get_repo_map()` on both `ConfigurationModel` and `ConfigurationService`.
All services that resolve cross-repo refs build `repo_map` before resolution.
**Gap:** No "repos fetched?" guard — missing repos give silent `FileNotFoundError`, not a
user-facing "run xyz sync first" message.

**`work_path` resolution:** Decided in `decisions.md` (flag → env → CWD walk → error) but
**not implemented**. No `_find_work_path()` function exists anywhere. CLI startup does no
initialization orchestration (`ConfigurationService.add_configurations()` is never called).

**Top 5 priorities:** (1) work_path resolution + CLI init, (2) xyz project commands,
(3) xyz validate command, (4) BuildController + xyz build, (5) xyz deploy orchestration.

### 2026-05-06 — copilot-instructions.md accuracy review

- Confirmed `.platform/` (not `.xyz_platform/`) is the workspace state directory — `SOLUTION_DIR = ".platform"` in `utils/config.py`.
- `xyz init` (not `xyz project init`) — flat CLI structure per 2026-05-05 decisions.md decision.
- `xyz config set|unset|list` (not `xyz set`) — confirmed in `commands/cli_config.py`.
- Workspace state file is `solution.json` (`SolutionModel`) — not `project.json`.
- `resolve_work_path()` in `utils/system.py` walks up from CWD for `.platform/` — implemented, falls back to CWD (error path not yet raised).
- CI uses composite actions: `.github/actions/install-python` (uv sync --frozen) and `.github/actions/test-python` (lint + types + pytest).
- Testing pattern: plain pytest classes (`class TestConfigSet:`) — no `unittest.TestCase`.
- copilot-instructions.md updated 2026-05-06.

### 2026-06-01 — Helm integration completeness review

**Requested by:** Vincent Huybrechts.

**Finding — HelmIntegration (helm.py):** Structurally correct. Follows AnsibleIntegration pattern exactly. `ensure_available()` and `validate_version()` (via BaseIntegration) both present. Three abstract methods implemented. No named domain methods (repo_add, upgrade_install, etc.) — deployer calls `_run_integration` directly. Not a blocker but violates convention.

**Finding — HelmDeployer (helm_deployer.py):** All 8 steps implemented and structurally correct. `validate_workspace` correctly iterates namespace services and loads modules. `destroy` force-guard present. `setup` gracefully handles no-registry case. **Bug:** `check()` runs `helm lint -f values.yaml {repo_name}/{chart_name}` for registry charts — `helm lint` requires a local path; this step would fail at runtime for any registry-sourced chart.

**Finding — validate_environment factory bypass:** HelmDeployer instantiates `HelmIntegration(config=IntegrationModel(name="helm", type="helm"))` directly, bypassing `IntegrationFactory.create()`. Currently necessary because "helm" is not registered. Once factory registration is fixed, this should be updated to use the factory.

**Finding — _create_deployer (4 command files):** All four commands correctly detect `ProvisionerType.HELM` and instantiate `HelmDeployer` with the correct constructor args for each command variant. Minor stale error message in `run_deploy_command.py` and `destroy_deploy_command.py` still says `"Supported: terraform, ansible."` — should include compose and helm.

**Finding — factory.py not updated:** `"helm"` is absent from `factory._BUILTIN_CLASS_MAP`. `HelmIntegration` is absent from `integrations/__init__.py`. This means `strata tools`, `IntegrationController`, and any `IntegrationFactory.create()` call for type "helm" will raise ValueError. The deploy commands work only because they bypass the factory.

**Priority fix order:** (1) factory.py + __init__.py registration — 4 lines; (2) stale error messages in run/destroy _create_deployer — 2 lines; (3) helm lint bug for registry charts in check() — requires logic change; (4) named methods on HelmIntegration — low priority.

### 2026-05-28 — Helm architecture analysis

**Requested by:** Vincent Huybrechts.

**Finding — No new `kind`:** Helm fits inside the existing `DeploymentModel` with `stage.type = "helm"`. A `kind: helm-deployment` would be over-engineering.

**Finding — `ProvisionerType` enum:** Add `HELM = "helm"` in `src/strata/models/common_models.py`. Mirrors how `TERRAFORM` and `ANSIBLE` are declared.

**Finding — `WorkspaceHelmModel`:** Helm cluster config (chart, repo_url, namespace, release_name, values_files, kubeconfig, kube_context, wait, atomic, timeout) belongs in the workspace spec as `helm: Optional[List[WorkspaceHelmModel]]`, adjacent to `provisioners`. Stage references by name via the existing `stage.provisioner: Optional[str]` field.

**Finding — `_create_deployer` duplication:** This method is copy-pasted in 4 deploy command files (`run_deploy_command.py`, `destroy_deploy_command.py`, `health_deploy_command.py`, `status_deploy_command.py`). Adding Helm without fixing this would require 4 edits. Recommend Basher extracts a `DeployerFactory` or moves `_create_deployer` to `BaseDeployCommand` as part of the Helm PR.

**Delegated to Basher:** `HelmIntegration` in `integrations/helm.py` — methods: `repo_add`, `repo_update`, `pull`, `upgrade_install`, `uninstall`, `status`, `list_releases`. Step sequence: `setup → check → plan → apply` (same contract as `TerraformDeployer`).

**Decision written:** `.squad/decisions/inbox/danny-helm-architecture.md`
