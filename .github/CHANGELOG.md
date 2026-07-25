# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **AI agent integration — ADR-0025 (Phases 1–4 implemented)**
  - `AiAgentIntegration(BaseIntegration)` in `strata.integrations.ai` — advisory LLM analysis at build/deploy lifecycle points; purely read-only, opt-in, no infrastructure mutations
  - Providers: `OllamaProvider` (local, no auth), `OpenAiProvider` (OpenAI + Azure OpenAI), `AzureCliProvider` (bearer token via existing `AzureCLIIntegration.get_access_token()`, no stored key), `AnthropicProvider`
  - Auth methods on existing `AuthenticationModel`: `api_key` (env var resolution), `cli` (Azure CLI), `managed_identity`; `provider: azure_cli` acquires short-lived bearer token via `az account get-access-token --resource https://cognitiveservices.azure.com/`
  - Six analysis methods: `analyse_plan()`, `diagnose_failure()`, `analyse_sbom()`, `explain_drift()`, `summarise_deployment()`, `review_policy_violations()`
  - `PromptLoader` with `.strata/prompts/<name>.md` workspace override support — resolved via `get_ai_prompts_dir(work_path)` from `utils/config.py`; built-in Python templates for all six hooks
  - `AiResponseCache` — SHA-256-keyed JSON file cache under `get_ai_cache_dir(work_path)` (`.strata/cache/ai/`); configurable TTL (default 24 h); failure-diagnosis never cached
  - `strata build plan --ai` — runs `analyse_plan()` after terraform plan; renders risk level, summary, concerns, and recommendations to console; adds `ai_analysis` key to JSON output
  - `strata build sbom --ai` — runs `analyse_sbom()` after SBOM generation; renders supply-chain risk summary to console
  - `strata deploy run --ai` — runs `diagnose_failure()` on any provisioner step failure (root cause + remediation); runs `summarise_deployment()` on successful completion
  - `type: ai_review` policy — gates deployment based on LLM risk score; configurable `risk_threshold` (`low`/`medium`/`high`/`critical`); registered in `PolicyEngine`
  - `--strict-ai-review [THRESHOLD]` on `strata build plan` and `strata deploy run` — fails non-interactively when AI risk ≥ threshold (default `high`); no policy declaration required; suitable for CI/CD
  - Interactive confirmation on `strata deploy run --ai` — prompts operator before apply when risk is high/critical and `--force` is not set; auto-blocks in non-TTY (CI) mode
  - Registered in `IntegrationFactory._BUILTIN_CLASS_MAP` as `"ai_agent"`
  - Path constants `SOLUTION_AI_CACHE_DIR = "cache/ai"` and `SOLUTION_PROMPTS_DIR = "prompts"` + builder functions `get_ai_cache_dir()` / `get_ai_prompts_dir()` added to `utils/config.py`
  - Every invocation logged to audit trail with provider, model, token counts, duration, and cache status
  - Help topic: `strata help --topic ai_agent`
  - Solution scaffold (`strata sln init`) includes commented-out `ai_agent` integration example in `configuration.yaml`
  - Phase 5: VS Code Chat Participant AI commands
    - New `@strata /review` — runs terraform plan and analyses risks using `request.model` (VS Code LM API / GitHub Copilot); streams risk level, concerns, and recommendations into chat
    - New `@strata /diagnose` — loads last failed deployment from audit history and generates root-cause + remediation analysis
    - New `@strata /sbom` — loads SBOM component inventory and analyses supply-chain risks
    - `AiPromptBuilder` (`src/vscode/src/providers/aiPromptBuilder.ts`) — resolves system prompts from `.strata/prompts/<name>.md` workspace overrides, falling back to built-in TypeScript constants
    - Auto-routing in `_handleFreeform` — detects AI-related keywords ("review plan", "why did it fail", "sbom", etc.) and delegates to the appropriate handler
    - Follow-up suggestions for all three new commands
    - `package.json` updated with `review`, `diagnose`, `sbom` slash command declarations
    - No Python AI provider configuration required in IDE — uses the model already active in VS Code (GitHub Copilot)

## [1.4.0] - 2026-07-24

### Added

- **`--timeout` for deploy run and deploy destroy — ADR 0027 (implemented)**
  - `--timeout SECONDS` option on `strata deploy run` and `strata deploy destroy` (default: `0` = no timeout)
  - `timeout=0`: stage loop runs on the main thread with no overhead
  - `timeout>0`: stage loop runs in a `ThreadPoolExecutor` worker; main thread calls `future.result(timeout=N)` and triggers `coordinator.shutdown("timeout after Ns")` on expiry
  - `ShutdownCoordinator.update_lock(backend, handle)` / `clear_lock()` — thread-safe handshake so the main thread's signal handlers can release a lock acquired in the worker thread
  - Exit code 1 on timeout — same as SIGTERM; deployment is in unknown state, operator inspection required
  - 7 new tests for timeout path and lock handshake

- **SIGTERM graceful shutdown — ADR 0028 (implemented)**
  - `ShutdownCoordinator` in `strata.utils.shutdown_coordinator` — ordered shutdown: terminate subprocesses → release deployment lock → exit 1
  - Process registry: `run_command()` auto-registers/deregisters every `Popen` instance with the active coordinator — zero boilerplate in deployers
  - Signal handlers installed per-invocation (scoped to lock-holding window): SIGTERM (Unix), SIGINT (all platforms), `atexit` safety net
  - Subprocess termination: SIGTERM to all active processes → 30s grace period → SIGKILL stragglers
  - Re-entrant guard (`threading.Event`) prevents double-shutdown on rapid signals
  - `children: List[ShutdownCoordinator]` hook for future rollout/parallel-deploy fan-out
  - Wired into `RunDeployCommand._execute_provisioning()` and `DestroyDeployCommand._execute_provisioning()`
  - 20 tests (1 skipped on Windows where SIGTERM is unavailable)

- **Google Cloud CLI integration + lifecycle scripts — ADR 0055 Phase 1**
  - `GCloudCLIIntegration(BaseIntegration)` — `COMMAND = "gcloud"`; `ensure_available()` checks binary + `gcloud config get-value account` + active project (three-step check, unlike Azure/AWS which stop at auth); `get_project()`, `get_account()`, `get_access_token()` (cached), `run_gcloud()` passthrough
  - `IGCloudTool` capability protocol + `"gcloud"` in `CAPABILITY_MAP`; registered in `IntegrationFactory`
  - `strata.utils.gcloud_script_base.GCloudScript` — base class mirroring `AzureScript`/`AWSScript`; `project()` resolves via `GOOGLE_CLOUD_PROJECT` → `CLOUDSDK_CORE_PROJECT` → `gcloud config`; `account()` and `get_access_token()` helpers
  - Built-in script: `gcloud_gke_credentials.py` — `gcloud container clusters get-credentials`; `GKE_CLUSTER` + `GKE_ZONE`/`GKE_REGION`; optional `GKE_ROLE_ARN` → `GKE_INTERNAL_IP`
  - Built-in script: `gcloud_artifact_registry_login.py` — `gcloud auth configure-docker`; `GAR_LOCATION` for Artifact Registry or `GCR_ENABLE=true` for legacy GCR
  - Built-in script: `gcloud_gcs_bucket_ensure.py` — idempotent bucket create with `--no-fail-on-existing-bucket`; optional versioning, storage class, location, labels
  - Solution scaffold: `.strata/scripts/gcloud_lifecycle_example.py`
  - Help: `strata help --topic gcloud_cli`, `strata help --topic gcloud_scripts`; guide: `docs/guides/gcloud-lifecycle-scripts.md`
  - ADR 0055 updated: status → phase 1 implemented; corrected "no existing GCP integrations" (gcp_secretmanager.py and gcp_runtimeconfig.py were fictitious)
  - 35 tests, zero regressions against 4833-test suite

- **AWS CLI integration + lifecycle scripts**
  - `AWSCLIIntegration(BaseIntegration)` — `COMMAND = "aws"`; `ensure_available()` checks binary AND `aws sts get-caller-identity`; `get_identity()`, `get_region()`, `run_aws()` passthrough
  - `IAWSTool` capability protocol + `"aws"` in `CAPABILITY_MAP`; registered in `IntegrationFactory`
  - `strata.utils.aws_script_base.AWSScript` — base class mirroring `AzureScript` for AWS; adds `region()` (3-tier resolution: `AWS_DEFAULT_REGION` → `AWS_REGION` → `aws configure`) and `account_id()`
  - Built-in script: `aws_eks_credentials.py` — `aws eks update-kubeconfig` before Helm/ArgoCD/Flux; configured via `EKS_CLUSTER`, `AWS_DEFAULT_REGION`; optional `EKS_ROLE_ARN`, `EKS_CONTEXT_ALIAS`
  - Built-in script: `aws_ecr_login.py` — two-step `get-login-password | docker login`; accepts `ECR_REGISTRY` or auto-constructs from `ECR_ACCOUNT_ID` + region
  - Built-in script: `aws_s3_bucket_ensure.py` — idempotent `aws s3api create-bucket`; optional versioning, AES-256 encryption, public access block, and tags in one script
  - Solution scaffold: `.strata/scripts/aws_lifecycle_example.py` — ready-to-use starter
  - Help: `strata help --topic aws_cli`, `strata help --topic aws_scripts`; guide: `docs/guides/aws-lifecycle-scripts.md`
  - 33 tests, zero regressions against 4798-test suite

- **Azure lifecycle scripts — `AzureScript` base class and built-in scripts**
  - `strata.utils.azure_script_base.AzureScript` — base class for `.strata/scripts/*.py` lifecycle scripts; wraps Azure CLI with `run_az()`, `exit_on_failure()`, `require_env()`, `get_token()`, `log()` and strata context helpers
  - Built-in script: `azure_aks_credentials.py` — `az aks get-credentials` before Helm/ArgoCD stages; configurable via `AKS_CLUSTER`, `AKS_RESOURCE_GROUP`, optional `AKS_ADMIN_CREDENTIALS`, `AKS_CONTEXT_NAME`
  - Built-in script: `azure_acr_login.py` — `az acr login` before container push; configured via `ACR_NAME`
  - Built-in script: `azure_resource_group_ensure.py` — idempotent `az group create` for Bicep subscription-scope deployments; configured via `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, optional `AZURE_RG_TAGS`
  - Solution scaffold includes `.strata/scripts/azure_lifecycle_example.py` — ready-to-use starter with built-in script references and custom script pattern
  - Help file: `strata help --topic azure_scripts`; guide: `docs/guides/azure-lifecycle-scripts.md`
  - 26 tests

- **Scoped multi-scheme layering — ADR 0042 Phase 1 (completed)**
  - `spec.layerings[]` field — declare multiple layering schemes, each with a glob scope that matches deployment files by path
  - `ScopedLayeringModel` — each scheme has a `name`, `scope` (glob pattern), and ordered `layers[]` list
  - First-match scope resolution — deployment file is matched against schemes in order; first match wins, no match means no layering validation
  - Shared `strata.utils.layering` module — `resolve_layering_scheme()` resolves a deployment file path to its matching scheme; `compute_artifact_path()` builds the artifact path from a scheme
  - `DeploymentService._validate_deployment_layers()` updated — uses scope resolution to pick the active scheme per deployment
  - `DeploymentService.get_artifact_path()` updated — resolves scheme, then builds path from resolved layers
  - `OverlapController._compute_artifact_path()` updated — same resolution logic for cross-manifest collision detection
  - Mutual exclusion validation — `spec.layering` and `spec.layerings` cannot both be set; validator enforces this
  - Full test coverage — overlap controller tests adapted to multi-scheme; integration tests validate both flat and scoped schemes

- **Path convention validation — ADR 0052 (completed)**
  - `spec.paths[]` field on the configuration model — declare directory structure conventions for fleet-wide path enforcement
  - `PathConventionModel` — each convention has a `name`, `scope` (glob), `pattern` with `{segment}` captures, and optional `validate` rules per segment
  - Two validation rule types: `spec.field[*].attr` for model membership lookup (e.g., declared zones); or a path template for file existence check (e.g., `customers/{tenant}/tenant.yaml`)
  - `path_convention` policy type — enforces conventions at validate phase with deny/warn/audit levels; supports per-convention filtering via `configuration.conventions`
  - Deploy-repo mode — inline convention on policy for repos without a configuration model (`configuration.scope` + `configuration.pattern`)
  - Scope matching: `fnmatch` glob; pattern matching: positional literal + capture; no-match = skip (never a violation)
  - `spec.*` rules require configuration service (deep validation); file existence rules work in surface mode
  - `file_path: Optional[Path]` added to `PolicyContext` — populated by policy engine before `evaluate()`
  - `strata.utils.path_convention` module — `match_pattern`, `resolve_spec_rule`, `evaluate_file_rule`, `evaluate_conventions`
  - 45 tests, zero regressions against 4607-test suite

- **Checkov IaC security scanning — ADR 0051 Phase 1 (completed)**
  - `checkov` policy type — runs Checkov CLI against Terraform build artifacts during the `build` phase
  - `CheckovIntegration(BaseIntegration)` — invokes `checkov --directory ... --output json --compact`; parses single and multi-framework JSON output; graceful degradation when Checkov not installed
  - `CheckovPolicy(BasePolicy)` — resolves Terraform artifact dir from `context.build_path` (deployment-scoped → `terraform/` subdir → root); applies `severity_gate` and `skip_checks` filters
  - `CheckovFinding` / `CheckovScanResult` dataclasses — structured scan result with per-finding severity, resource, file path, and guideline
  - `CheckovScanResult.findings_at_or_above(severity)` — filters findings by severity level for gate evaluation
  - `iac_security` capability added to `CAPABILITY_MAP` / `CAPABILITY_REGISTRY` with `IIacSecurityScanner` protocol
  - Registered in `IntegrationFactory._BUILTIN_CLASS_MAP` and `PolicyEngine._create()`
  - Graceful degradation: Checkov not found → skip; no `.tf` files in build path → skip; subprocess failure → skip (non-fatal, never blocks build)
  - 30 tests, zero regressions against 4637-test suite

### Changed

- **Removed hardcoded "environment" layer name constraint** — last layer no longer required to be named `"environment"`. Layer names are now arbitrary (e.g., `ring`, `stage`, `landscape` as last layer). Collision prevention is entirely owned by `OverlapController` artifact path uniqueness check, not by layer naming.
- **`spec.layering` marked as deprecated** — single-scheme flat layering still supported for backward compatibility, but `spec.layerings` is preferred for new configs. Existing deployments continue to work unchanged.

### Breaking Changes

- **Layer name constraint removal** — configurations or deployment code that relied on the final layer being named `"environment"` should be updated. The constraint was overly restrictive and served no functional purpose in artifact path generation.

- **Bicep provisioner — ADR 0046 (completed)**
  - `ProvisionerType.BICEP = "bicep"` added to the enum — Bicep is now a first-class provisioner type
  - `BicepDeployer(BaseDeployer)` — Azure-native IaC deployer using ARM deployments (no state file, no backend)
  - Steps: `setup` → `az bicep build`, `plan` → `az deployment {scope} what-if`, `apply` → `az deployment {scope} create`, `destroy` → `az deployment {scope} delete`, `output` → ARM deployment outputs
  - Four ARM deployment scopes: `resourceGroup` (default), `subscription`, `managementGroup`, `tenant`
  - `BicepDeployer` uses `AzureCLIIntegration` for all `az` calls — inherits auth check and token caching
  - `_deployment_cmd()` routes to the correct `az deployment group/sub/mg/tenant` subcommand based on scope
  - What-if result cached by `plan()` and returned by `show_plan()`; output parsed from ARM `properties.outputs`
  - Registered in `DeployerFactory._BUILTIN_MAP`
  - 27 tests, zero regressions against 4739-test suite

- **Azure CLI integration — ADR 0053 Phase 1 (completed)**
  - `AzureCLIIntegration(BaseIntegration)` — `COMMAND = "az"`; shared foundation for all Azure CLI-based operations

- **Bicep provisioner — ADR 0046 (completed)**
  - `ProvisionerType.BICEP = "bicep"` — Bicep is now a first-class provisioner type
  - `BicepDeployer(BaseDeployer)` — Azure-native IaC deployer; no state file or backend required (ARM manages state server-side)
  - Steps: `setup` → `az bicep build`, `plan` → `az deployment {scope} what-if`, `apply` → `az deployment {scope} create`, `destroy` → `az deployment {scope} delete`, `output` → ARM deployment outputs
  - Four ARM scopes: `resourceGroup` (default), `subscription`, `managementGroup`, `tenant`
  - Uses `AzureCLIIntegration` for all `az` calls — inherits auth check and token caching from ADR-0053
  - Registered in `DeployerFactory._BUILTIN_MAP`; `provisioner: bicep` valid in workspace YAML
  - `docs/config/workspace.md` updated — `provisioner: bicep` added to provisioner list with `configuration` fields documented
  - Help file: `strata help --topic bicep`
  - 27 tests, zero regressions against 4739-test suite
  - `ensure_available()` checks binary presence **and** active login (`az account show`) — surfaces "not authenticated" in Tools view immediately
  - `get_subscription()` — returns active subscription `id`, `name`, `tenantId`
  - `get_access_token(resource)` — cached bearer tokens per resource scope; avoids repeated `az account get-access-token` spawns
  - `bicep_version()` — reports Bicep extension version (`az bicep version`)
  - `run_az(args)` — passthrough for arbitrary `az` subcommands (used by upcoming Bicep deployer)
  - `IAzureTool` capability protocol + `"azure"` in `CAPABILITY_MAP`
  - Registered in `IntegrationFactory`; Tools view shows subscription name and auth status
  - 20 tests, zero regressions against 4712-test suite

- **OPA (Open Policy Agent) integration — ADR 0050 (completed)**
  - `opa` policy type — evaluates Rego rules against strata deployment context
  - Two modes: HTTP REST (`POST /v1/data/{rule}` to running OPA server) and `opa eval` CLI fallback (stateless, no server required)
  - Auto-fallback: if HTTP endpoint unreachable, falls back to CLI mode transparently
  - `OPAIntegration(BaseIntegration)` — `evaluate_http()`, `evaluate_cli()`, unified `evaluate()` entry point
  - `OPAPolicy(BasePolicy)` — serializes `PolicyContext` (platform artifact, configuration, deployment, plan data) to OPA input document; parses violations from result
  - `OPAResult` dataclass — `passed: bool`, `violations: List[str]`, `raw: Any`
  - strata does **not** manage OPA server lifecycle — binary install and server start/stop are the operator's responsibility
  - Registered in `IntegrationFactory` and `PolicyEngine`; `iac_security` capability; help file `strata help --topic opa`
  - 34 tests, zero regressions against 4671-test suite

- **Date/time format standard — ADR 0045 (implemented)**
  - `src/strata/utils/datetime_utils.py` — shared UTC datetime utilities: `now_utc()`, `to_wire_timestamp()`, `format_display_timestamp()`, `parse_iso_timestamp()`, `coerce_to_utc()`
  - All `datetime.now()` (naive) calls replaced with `datetime.now(timezone.utc)` across `base_command.py`, `sbom_build_command.py`, `schema_base_command.py`, `solution_controller.py`
  - `base_command._start_time` / `_end_time` initialised as UTC-aware in `__init__` — prevents `can't subtract offset-naive and offset-aware datetimes` errors
  - Console header timestamp now shows `UTC` suffix
  - `solution_controller` cutoff time (minutes filter) now UTC-aware — fixes silent comparison bug with UTC log entries
  - 21 unit tests for `datetime_utils`

### Changed

- **`datetime.now()` → `datetime.now(timezone.utc)` everywhere** — all internal timing and audit timestamps are now timezone-aware UTC. Wire format (`+00:00` suffix) unchanged for existing consumers.

---

## [1.3.1] — 2026-07-22

### Added

- **Cost Estimation and Visibility — ADR 0031 Phase 1 (completed)**
  - `strata cost show` command — display monthly cost estimate for a deployment using Infracost
  - `strata cost diff` command — show cost impact of terraform plan changes (before/after delta)
  - `strata cost history` command — display historical cost snapshots (up to 50 entries per deployment)
  - `ICostEstimator` capability protocol — integrations can implement cost estimation; Infracost registered as first implementation
  - `InfracostIntegration` class — invokes `infracost breakdown` and `infracost diff`; supports Azure, AWS, GCP; non-fatal if binary not installed (graceful degradation)
  - `CostController` — orchestrates cost estimation; handles multi-provisioner deployments; caches results locally (7-day TTL with content hash key)
  - `CostHistoryStore` — appends cost snapshots to `.strata/cost/{deployment}.cost-history.json`; auto-computes delta from previous snapshot; capped at 50 entries (most-recent kept)
  - `cost.json` artifact — written alongside `platform.json` in build directory with monthly/resource breakdown after `strata cost show`
  - `deploy --dry-run` auto cost diff — after terraform plan, auto-runs infracost diff to show cost impact (non-fatal, never blocks deploy)
  - `cost_threshold` policy type — blocks or warns on deployments exceeding monthly cost limit; environment pattern scoping; reads `cost.json` from build artifacts
  - Configuration YAML integration entries (azure-aks, aws-eks, gcp-gke) — Infracost declared as optional integration with cost capability
  - Full test coverage — 25 unit tests for `CostHistoryStore`, 88+ integration tests for cost commands/controllers/policies

- **VS Code Extension — deployment-centric rework (v1.3.1)**
  - **Deployment context** — active deployment is now a persistent, workspace-scoped selection (stored across sessions); all commands default to it instead of "whatever file is open"
  - **Deployments view** (new) — replaces the flat Files view; shows the selected deployment's full hierarchy: workspace → providers, provisioners, topology, namespaces → environments → configurations → policies (lazy loaded); one-click switch between deployments; `$(target) Set Active` code lens on every `kind: deployment` file
  - **Operations view** (new) — shows runtime status for the active deployment: build cache status, health, drift, lock state, cost snapshot with delta, lazy-loaded outputs per stage, and deploy history
  - **Workspace view merged** — health, readiness phases, profiles, repositories, and tools unified into one collapsible panel (was 3 separate panels: Workspace, Repositories, Tools)
  - **Status bar** updated — shows active deployment name alongside health and profile: `◎ HEALTHY  —  dev  | Phase 5/8  $(cloud)  deploy-prd`
  - **New commands**: `strata.selectDeployment` (Quick Pick from all deployment files), `strata.setActiveDeployment` (set from code lens or tree click), `strata.newFile` (guided scaffolding via `strata new`), `strata.activateProfile` (inline from Workspace view), `strata.showCostHistory` (open cost history terminal)
  - **Inline YAML manifest parsing** — deployment explorer reads workspace/environment/configuration references from YAML directly (no CLI roundtrip) to resolve file links
  - **`CostSnapshot` / `CostHistoryData` interfaces** added to `StrataClient` — wired to `strata cost history` via `getCostHistory()`
  - **Sidebar reduced** from 8 views to 6: removed `strataFiles`, `strataRepositories`, `strataTools`, `strataEnvironment`; added `strataDeployments`, `strataOperations`
  - **CI pipeline fix** — `cp LICENSE src/vscode/LICENSE` step added before `vsce package`; `--skip-license` flag removed so the AGPL-3.0 license is now bundled inside the `.vsix`

### Changed

- **Provider model `engine` field removed** — unused field that was never applied during cost estimation; cost behavior is now determined entirely by integration type and Infracost availability
- **Resource model `unit_cost` field removed** — planned for manual per-resource pricing but not implemented; Infracost integration provides automatic cost calculation instead

---

## [1.2.1] — 2026-07-20

### Changed

- **`strata new --output-file` replaces `--path`** — `--path` / `-p` renamed to `--output-file` for naming consistency with other commands
- **`strata validate run --pattern` replaces `--path`** — option renamed from `--path` to `--pattern` (`-p`) for clarity; describes glob patterns used for cross-manifest overlap validation
- **Exit code 4 for lock conflicts** — `handle_command_exit` now prioritises lock conflict detection before other failure types; `deploy run` and `deploy destroy` exit with code `4` when another process holds the deployment lock
- **`LockConflictError` / `LockTimeoutError` hierarchy** — `LockTimeoutError` is now a subclass of `LockConflictError`, enabling callers to catch either level of locking failure; exit code 4 is emitted for both

### Fixed

- `strata secret mask` — passwords or tokens starting with `-` are now handled correctly when passed as a positional argument in automated scripts (flaky test fixed; use `--` separator before the value when the secret may start with a dash)
- Sphinx docs — `decisions/` directory excluded from GitHub Pages build; removed stale toctree references that caused "not in doctree" warnings

---

## [1.2.0] — 2026-07-16

### Added

- **GitOps Controller Integration — ADR 0041 (completed)**
  - `argocd` and `flux` provisioner types — integrate GitOps controllers as first-class deployment stages with no new CLI commands
  - `SyncBackendModel` — `backend.integration` (names the integration instance) and `backend.remote` (names the git remote for rendered output) on deployment stages
  - `namespace` field on `DeploymentStageModel` — scopes sync provisioner output to a declared workspace namespace
  - `ProvisionerType.ARGOCD` / `ProvisionerType.FLUX` added to the enum; `_SYNC_PROVISIONER_TYPES` frozenset used throughout for sync-aware branching
  - `ReconciliationResult` dataclass — shared health result type: `sync_status`, `health_status`, `last_synced_at`, `revision`, `intended_revision`, `drift`, `message`
  - `SyncBuilder` — reads platform artifact, finds sync stages, renders user-editable Jinja2 templates (`StrictUndefined`), writes output files; wired into `strata build run`
  - `BaseSyncDeployer`, `ArgocdDeployer`, `FluxDeployer` — step-based deployers; apply step commits rendered output to git remote; health step queries controller API (`GET /api/v1/applications/{name}` for ArgoCD, `kubectl get kustomizations` for Flux)
  - `strata deploy health` auto-detects sync stages and queries reconciliation status alongside infrastructure health — no flags required
  - Cross-reference validation in `DeploymentService._validate_sync_stages()`: `backend.integration` must exist in configuration with `sync` capability; `backend.remote` must be in the merged repo map; `namespace` must match a declared workspace namespace
  - `_validate_sync_stages()` tests — 12 test cases in `TestValidateSyncStages`
  - Sync Jinja2 adapter templates scaffolded by `strata sln init` / `sln update`: `.strata/templates/sync/argocd-appset-entry.json.j2`, `flux-kustomization.yaml.j2`, `README.md`
  - `.j2` files skipped by `TemplateProcessor.render()` in scaffold methods — raw Jinja2 templates are copied verbatim so end-users can use template syntax freely

- **Platform artifact convenience fields (ADR 0041 — Decision 8)**
  - 8 new computed fields on `PlatformSpecModel` populated by the platform builder: `name`, `labels`, `annotations`, `layers`, `chart_versions`, `image_versions`, `resolved_variables`, `revision`
  - `revision` — `git rev-parse HEAD` at build time (best-effort, `None` outside git repos)
  - `resolved_variables` — non-secret variables only; secrets never appear in template context
  - `chart_versions` / `image_versions` — flat `name → value` dicts for ergonomic Jinja2 access

### Changed

- **`WorkspaceIacModel.source` is now optional** — required for IaC provisioner types (`terraform`, `ansible`, `helm`, `compose`, `script`) via `validate_provisioner_fields()` model validator; optional for sync types (`argocd`, `flux`) which generate output from the platform artifact
- **`DeploymentService._merged_repo_map()`** — new helper merges configuration-level remote map with solution-level repo map (solution names take precedence); replaces three inline dict merges in `_validate_dynamic()`
- `ansible_deployer.py` / `terraform_deployer.py` — removed redundant `iac.source and` null guards; added `assert iac.source is not None` (model validator guarantees this for non-sync provisioners, satisfies mypy)
- ADR 0041 status → `completed`; ADR 0011 status typo fixed (`Implementated` → `completed`)
- ADR status taxonomy extended: 7 ADRs updated from `proposed` to `partial` (0031, 0034, 0035, 0037, 0039, 0040, 0042)
- `docs/guides/features.md` — added `argocd`/`flux` to provisioner table with GitOps explanation; added Version management and Promotions sections

### Fixed

- ADR 0041 code fences changed from ` ```jinja2 ` to ` ```jinja ` — `jinja2` is not a valid Pygments lexer name; fixes Sphinx docs build warnings

---

## [1.1.1] — 2026-07-15

### Added

- **`strata new --validate` flag**
  - New `--validate` / `-v` flag on `strata new` validates each generated file immediately after creation using `PlatformValidator`
  - Runs `before_validate → validate → after_validate` lifecycle on every produced file (single-file and bundle modes)
  - Validation errors are appended to command output; generated files are preserved for manual correction
  - Exit code reflects combined result of generation + validation

### Changed

- **BaseCommand lifecycle — ADR 0030 (completed)**
  - All command `_run()` overrides migrated to `_execute()` across the entire command layer (~80 files)
  - `execute()` is now a concrete sealed method on `BaseCommand`; subclasses must not override it
  - `INIT_REQUIRED` ClassVar removed; workspace-optional commands call `_initialize_session()` instead of `super()._initialize()`
  - `_initialize_session()` added to `BaseCommand` — mirrors `_initialize()` but does not error when `solution.json` is absent
  - Three regression guards added to `scripts/Check.ps1`: no `INIT_REQUIRED`, no `execute()` overrides, no `_run()` definitions
  - `CONTRIBUTING.md` updated with new command authoring pattern

### Fixed

- `cli_ref.py` — `super()._run()` call inside `_execute()` updated to `super()._execute()` (leftover from ADR 0030 migration)
- `sbom_build_command.py`, `drift_deploy_command.py` — removed unnecessary `f` prefix from string literals with no placeholders (ruff F541)
- `run_new_command.py` — mypy `Optional` reassignment on `context` variable resolved via separate `prompted` variable

### Design & ADR Progress

- **ADR-0030** (Command lifecycle explicitness and thin overrides) — status updated to `completed`
- **ADR-0043** (Tenant offboarding — `strata remove tenant`) — new ADR, status `proposed`

---

## [1.1.0] — 2026-07-14

### Added

- **Environment Provider Overrides (ADR 0036)**
  - `EnvironmentProviderOverrideModel` — environments can now override provider file bindings per provider name, supporting both file swaps and configuration-level property overrides
  - `spec.overrides.providers[].file` — replace entire provider YAML file per environment (enables region/cloud-account variants without workspace duplication)
  - `spec.overrides.providers[].configuration` — overlay specific provider properties without maintaining separate provider files
  - Provider file validation — when an override loads a new provider file, `meta.name` is validated to match the workspace provider name (hard error if mismatched)
  - `EnvironmentService.get_overridden_provider_names()` — returns set of provider names with overrides
  - `EnvironmentService.get_provider_override()` — accessor for override model by provider name
  - `DeploymentService.apply_environment_overrides()` — applies file swaps + configuration overlays during deployment build
  - Full test coverage: model validation tests (file-only, configuration-only, combined), service merge tests, provider file resolution tests
  - Documentation: `docs/config/environment.md#Provider-Overrides` with multi-region examples; `docs/config/provider.md#Environment-Specific-Provider-Overrides` with cross-reference

- **Environment Promotion Strategies** (preparation for ADR 0011)
  - `PromotionStrategyModel` — framework for multi-environment promotion workflows (dev → staging → prod)
  - `spec.promotion` field on environments to define advancement criteria and gates
  - Promotion validator — ensures environment sequences are valid and acyclic
  - CLI ready for future `strata promote` subcommand

- **Version Management & Release Tooling**
  - `scripts/Release.ps1` now supports version parameter: `-Version X.Y.Z` for automated version bumping
  - `VERSION.txt` updated to `1.1.0` with corresponding git tags
  - Changelog versioning aligned with semantic versioning for clarity on minor vs patch releases

- **Tenant Scaffolding Bundle Template**
  - New workspace-local bundle template `.strata/templates/tenant/` enables rapid tenant provisioning for multi-customer deployments
  - `strata new tenant <name>` generates complete tenant structure: tenant config file + dev/qa/prd environments with provider overrides
  - Supports `{{ name }}` variable substitution in generated tenant codes, file paths, and descriptions
  - Eliminates copy-paste for 200+ customer onboarding; teams maintain template in their workspace, not shipped with strata
  - Includes auto-generated README and CHECKLIST for onboarding workflow
  - Fixed `strata new` to exclude `template.yaml` metadata from bundle output
  - Fixed `strata new --list` to properly display workspace bundle templates alongside single-file templates

### Changed

- **`strata new --list` workspace template priority** — bundle directories in `.strata/templates/` now take precedence over same-named single-file templates in display (matching resolution precedence)

- **Provider Override Handling** — provider resolution now includes fallback chain: workspace default → environment override file → environment override configuration
- **Deployment Build Output** — plan summaries now show provider resolution details (file loaded, overrides applied per stage)

### Design & ADR Progress

- **ADR-0036** (Workspace, Provider, and Environment-level Provider Overrides) — status updated to `completed`
- **ADR-0011** (Promotion strategies) — status updated to `in-progress` (model framework added, CLI TBD)

---

## [1.0.1] — 2026-07-09

### Added

- **Tenant — `spec.environments` now applied at build time**
  - `DeploymentService.load_deploy_services` prepends `tenant.spec.environments` to the deployment's own environment list before merging, so tenant tier files (e.g. `environments/tiers/enterprise.yaml`) are applied as a base layer that deployments can override
  - `PlatformTenantModel` now carries an `environments` field so the build artifact records which base files were applied

- **Tenant — `spec.properties` and `spec.custom`**
  - New optional fields on `TenantSpecModel`; merged as base layers into every deployment's `spec.properties` / `spec.custom` — deployment values take precedence on any overlapping key
  - `PlatformTenantModel` carries both fields for artifact traceability
  - `TenantService` exposes `get_properties()` and `get_custom()` accessors

- **`sln deployment` subcommand group**
  - `sln deployment add <path>` — register a deployment YAML file in the solution
  - `sln deployment remove <name>` — remove a registered deployment by name
  - `sln deployment list [--name]` — list registered deployments (JSON output supported)
  - `sln deployment scan [path]` — recursively discover and register `kind: deployment` files

- **`docs/config/tenant.md`** — new reference doc covering schema, field descriptions, `properties`/`custom` vs `configuration` distinction, environment layering order, phase validation rules, and zone policy behaviour

- **Environment provider overrides** (`docs/config/environment.md`)
  - `spec.overrides.providers[].file` — swap the entire provider binding per environment; recommended for targeting different regions or cloud accounts
  - `spec.overrides.providers[].configuration` — override individual provider properties on top of the resolved provider file, without maintaining separate provider files per environment
  - Build plan output now includes provider resolution details (which file was loaded and which overrides were applied per stage)

- **ADR 0026 — Resolved-model cache** (proposed) — SQLite-backed cache for fleet-wide command performance; documents cache key computation, invalidation strategies, per-kind TTL, and VS Code extension integration for background cache warming

### Fixed

- **Tenant `spec.environments` was declared and validated but never applied** — the field existed in the schema since the initial tenant model, paths were checked on disk during Phase 2, but the files were never actually merged into the build pipeline (`get_environments()` was defined but never called)

### Changed

- **Tenant `spec.environments` field description** — clarified to explicitly state these are *base environment files merged before the deployment's own environments* (not a list of environments the tenant belongs to); both the model docstring and the scaffolding templates updated
- **`sln` subcommand set** — `deployment` group added; `test_sln_subcommands_registered` updated accordingly
- **Strata Workspace Agent** — rule added: all temporary files must be written to `.strata/temp/`, not the workspace root; `.strata/temp/` documented in the workspace layout

### Design & ADR Progress

- **ADR-0013** (Auto-generated secrets) — status updated to `completed`
- **ADR-0014** (Onboarding experience) — status updated to `completed`
- **ADR-0026** (Resolved-model cache) — added as `proposed`

---

## [1.0.0] — 2026-07-08

### Added

- **S3 Lock Backend (ADR 0007)**
  - `S3LockBackend` — distributed deployment lock using AWS S3 object conditional writes; supports TTL, lock metadata (holder, acquired_at), force-release
  - Full test suite covering acquire, release, status, force-release, TTL expiry, and concurrent contention scenarios

- **VS Code Extension — Complete Feature Parity with CLI**
  - **Values Inspector** (`strataValues` tree view) — new panel showing all resolved deployment values with secret masking, source tracking, resolved/unresolved indicators, and copy-to-clipboard
  - **Lock Status & Release** — `strata.lockStatus` shows live lock holder and TTL; `strata.releaseLock` force-releases with confirmation dialog; lock badge (🔒) shown on deployment items in Environments panel
  - **Drift Detection** — `strata.envDrift` runs `deploy drift run`; ⚠ drift badge shown on deployment items after detection
  - **SBOM Generation** — `strata.buildSbom` runs `build sbom` with progress notification; offers to open the generated `sbom.json`
  - **Stage-targeted Deploy** — `strata.deployStage` prompts for stage name, supports dry-run; right-click on stage items in Environments panel
  - **Repository Write Operations** — sync (with spinner), remove (with confirmation), add (input boxes for name + path) from the Repositories panel
  - **Audit Filter & Limit** — `strata.auditFilter` cycles all/success/failures; `strata.auditSetLimit` sets entry count (5–200)
  - **Workspace Panel** — `strataWorkspace` tree view fully implemented: active profile, repositories, document paths, tool availability (was entirely stubbed)
  - **Chat Participant** — `/build` and `/deploy` now execute via action buttons (▶ Dry Run / ⚡ Full Build / 🚀 Full Deploy); new `/stage`, `/values`, `/drift` slash commands
  - **Task Provider** — SBOM task added to auto-discovered VS Code tasks per deployment manifest
  - **Editor context menus** — Show Values, Generate SBOM, Lock Status available on `.yaml` files
  - **13 new commands** registered: `deployStage`, `lockStatus`, `releaseLock`, `showValues`, `copyValueKey`, `buildSbom`, `syncRepo`, `addRepo`, `removeRepo`, `auditFilter`, `auditSetLimit`

- **Umbrella JSON Schema (`strata.json`)**
  - `_generate_schemas()` now produces `.strata/schemas/strata.json` alongside per-kind schemas
  - Single `if/then/else` discriminated-union schema routes to the correct per-kind schema based on the `kind:` field value
  - `yaml.schemas` in workspace settings and solution template reduced from 12 separate entries to one `strata.json` entry — kind-based validation regardless of file location
  - Generated automatically on `strata sln init` and `strata sln update`

- **MCP Server** — `_run_command` envelope now derives `success` from `not cmd.has_errors()` instead of `cmd.execute()` return value — fixes `None` success on commands that don't explicitly return a bool

### Changed

- **Exception handling & logging refactor (#187)** — unified exception capture, structured logging, and command execution methods across all BaseCommand subclasses
- `.gitignore` replaced 561-line Visual Studio template with a lean Python/infra-focused file; adds `*.egg-info/`, `src/vscode/out/`, `docs/_build/`, `.coverage*`, `htmlcov/`, `**/.strata/cli.yaml`, `**/.strata/audit.log`, `**/.strata/solution.json`
- `config/azure-aks/.strata/cli.yaml` removed from git tracking (runtime-written file)
- Workspace `yaml.schemas` uses the new umbrella `strata.json` — one entry replaces twelve

### Design & ADR Progress

#### ADR-0013: Auto-generated Secrets — Model Acceptance Criteria Updates
- [x] **Model field**: `SecretStoreModel.rotate: Optional[SecretRotateSpec]` (sibling of `generate:`)
- [x] **Validator 1**: `policy: rotate` without `generate:` → Pydantic validation error
- [x] **Validator 2**: `max_age` is `int` (days, >= 1) with field_validator enforcing range
- [ ] **YAML examples**: All docs examples using integer `max_age` (audit of docs still needed)

**Status**: Model layer 100% complete. Remaining: documentation consistency sweep across ADR-0013 and companion docs.

#### ADR-0011: Promotion Strategies — Phase 3 Design Gaps Identified
Five blocking issues identified for Phase 3 (automation: `start`, `rollback`, `history`):

- **#11** — Phase 1 `status`/`matrix` wrongly describe reading `spec.version`; should read `spec.overrides.remotes[].reference` (correct field)
- **#12** — No deployment discovery mechanism for `promote start --to production`; three options proposed (explicit flags, directory scan, solution registry)
- **#13** — Wave-to-file mapping ambiguous; conflation of `kind: tenant` vs `kind: environment`; three resolution options
- **#14** — Rollback depends on gitignored `.strata/promotions/` activity log; three recovery options proposed
- **#15** — Single-layer configs (`scope: tenant` matches 0 deployments); needs explicit graceful degradation behavior

**Status**: Phase 1 (read-only) and Phase 2 (model + validation) unblocked. Phase 3 deferred pending resolution of #11–#15.

#### ADR-0018: SIEM Integrations — Layer 4 Completed
- [x] **ELK Syslog Integration** (`ElkSiemIntegration`) — dual-protocol: TCP (Logstash) + HTTP (Elasticsearch bulk)
- [x] **OpenTelemetry Integration** (`OtelSiemIntegration`) — OTLP/HTTP JSON; no SDK dependency needed
- [x] **Factory registration** — both types registered as `"elk"` and `"otel"`
- [x] **Tests** — full coverage in `test_elk_siem_integration.py` and `test_otel_siem_integration.py`

**Design note**: Uses integration-reference model (`integration: <name>` in `AuditSinkModel`) rather than built-in sink types. Both can forward to same ELK stack independently of `LogstashHandler` (operational logs via TCP vs. compliance audit events via HTTP).

#### ADR-0022: CEF Syslog Format — Implementation Complete
- [x] **Model field**: `AuditSinkModel.format: Optional[str]` — syslog sink accepts `"json"` (default) or `"cef"`
- [x] **CEF encoder** — `AuditController._format_cef(data)` → CEF:0 header + 6-field extension (rt/src/dst/act/externalId/msg)
  - Severity: `3` (Low) on success, `7` (High) on failure
  - Proper escaping of pipes/backslashes per CEF spec
- [x] **Syslog routing** — `_send_syslog(data, address, fmt)` routes to formatter based on `fmt` parameter
- [x] **CLI flag** — `--siem <name>` on `strata audit export` for on-demand SIEM forwarding by integration name
- [x] **Tests** — `test_syslog_sink_passes_cef_format`, `TestFormatCef` class (header, severity, escaping, extension fields)

**Status**: Ready for v1.0. CEF output validated against CEF 0 specification.

---

## [0.16.1] — 2026-07-06

### Fixed

- Moved `doc` from `[project.optional-dependencies]` to `[dependency-groups]` in `pyproject.toml` — resolves "group doc is not defined" error in `uv sync --group doc` introduced by uv's stricter group/extra distinction
- Updated `Dockerfile.docs` and CI docs job to use `--group doc` instead of `--extra doc`

---

## [0.16.0] — 2026-07-05

### Added

- **Environment Composition: Flat Merge Fix (ADR 0024)**
  - `EnvironmentService.merge_envfiles()` now merges **all 8 spec sections** (previously only 3): variables, secrets, features, properties, custom, lifecycle, audit, and all override subsections
  - Per-section merge strategy documented and implemented: last-wins by key for variables/secrets/features, shallow dict-merge for properties/custom, wholesale last-wins for lifecycle/audit, resource/provider/remote last-wins by name, includes/output_files are additive with deduplication
  - `MergeProvenance` dataclass tracks which environment file contributed each key — populated during merge, carried through `ResolvedValues` to CLI output
  - Multiple environment files enable composition pattern: base + region + environment layers for DRY configuration
  - `strata values list --trace` flag shows provenance: which file each variable/secret/feature originates from in console table and JSON output
  - `merge_order` exposed in `values list` JSON output — list of files in merge sequence
  - 21 comprehensive tests covering all merge strategies, override merging, features by-key semantics, and provenance tracking
- **Environment Composition Guide** (`docs/guides/environment-composition.md`)
  - When and why to compose; merge semantics per section; base + region + prd example with effective-result table
  - `strata values list --trace` usage with console and JSON examples; merge order visualization
  - Common patterns: base + region + environment, shared security policy, tenant overlays
  - Troubleshooting and validation notes
- **Updated merge order documentation**
  - `docs/config/deployment.md` Configuration Merge Order section now includes precise per-section table and `--trace` usage link
  - `docs/config/environment.md` new Multi-file Composition section with strategy table
- **Index coverage** — environment-composition guide added to `docs/index.rst`

### Changed

- `EnvironmentService.merge_envfiles()` now returns `Tuple[EnvironmentModel, MergeProvenance]` instead of just `EnvironmentModel`
- `deployment_service.py` unpacks and stores provenance from merge; exposed via `get_merge_provenance()` getter
- `ResolvedValues` dataclass now includes `variable_sources`, `secret_sources`, `feature_sources`, and `merge_order` dicts
- `ValueController.resolve_values()` populates provenance sources from deployment's merge provenance

### Fixed

- `merge_envfiles()` previously dropped properties, custom, lifecycle, audit, and all override sections from every file after the first
- Features now merge by key (last-wins per flag) instead of whole-file replacement

### Documentation

- Added ADR 0024: Environment Composition — Flat Merge Fix
- New comprehensive composition guide with patterns and examples
- Updated config reference docs with merge semantics and `--trace` usage

### Testing

- 3771 tests passing (3750 existing + 21 new merge/provenance tests), 15 skipped, 1 warning

---

## [0.15.0] — 2026-07-03

### Added

- **Configurable Terraform build output profiles (ADR 0019)**
  - New `output:` block on Terraform provisioners in `workspace.yaml` — controls what `.auto.tfvars.json` files `strata build run` produces
  - `format` modes: `strata` (default, backward compatible), `custom` (emit only what `emits[]` and `files[]` specify), `script` (one user script owns all output), `none` (suppress tfvars output entirely)
  - `emits[]` gate — selectively enable/disable each built-in emit category (`features`, `variables`, `properties`, `workspace`, `providers`, `topologies`, `modules`, `namespaces`, `firewalls`, `dns`, `networks`, `resources`, `tenant`)
  - `files[]` custom file definitions — source mode (pass-through a key from merged properties/custom dict) and script mode (per-file Python/shell script with `STRATA_PLATFORM_PATH`, `STRATA_BUILD_PATH`, `STRATA_OUTPUT_PATH`, `STRATA_OUTPUT_FILE`, `STRATA_WORKSPACE_PATH`, `STRATA_DRY_RUN` env vars)
  - `format: script` top-level — single script receives `STRATA_PROVISIONER` additionally
  - `output_files` override on `EnvironmentOverridesModel` — additive extra file definitions per environment; cannot remove or replace workspace-level definitions; collision warnings logged
  - Backend configuration expression resolution — `${var:KEY}` and `${secret:KEY}` placeholders now resolved from `resolved_values` at deploy time
  - Two-phase emission: `features` and `variables` written at build time (constant/env-store only); integration-backed entries re-written by deployer immediately before `terraform init`
  - Security invariant enforced: secrets are never written to any `.auto.tfvars.json` file regardless of configuration; always injected as `TF_VAR_*` environment variables only
  - 43 new tests covering profile models, `_planned_files`, feature/variable extraction, property merging, deploy-time vars, and backend expression resolution
- **`strata env status`** — renamed from `strata env state` for naming consistency; added `--all` and `--path DIR` flags to scan all deployment manifests in the workspace without requiring a single `-f FILE`
- **`strata audit changes/resend --output json`** — now emits the standard CLI JSON envelope (`{ success, command, execution_id, timestamp, data, messages, errors }`) instead of a raw array, consistent with every other command
- **Audit Trail documentation** — new `## audit` section in `docs/platform/commands.md` covering `audit changes`, `audit resend`, `audit export`, audit path template configuration, and SIEM sink YAML examples for Sentinel, ELK, and OTel
- **VS Code extension — Environments panel** (`strataEnvironment` tree view): shows all deployment manifests with cached build-output status; per-stage drill-down with last-cached timestamp and output count; click to open manifest file
- **VS Code extension — Audit Trail panel** (`strataAudit` tree view): shows the last 20 deploy-log entries with success/failure icons, timestamps, duration, and environment; expands to per-stage results (provisioner, duration, step-level detail), PR enrichment (clickable link), and commit SHA
- **VS Code extension — env commands**: `strata.envStatus` (cached, offline), `strata.envDrift` (terraform plan), `strata.envDoctor` (inline notification with pass/warn/fail counts)
- **VS Code extension — audit commands**: `strata.auditChanges` (terminal), `strata.auditResend` (terminal), `strata.auditExport` (save dialog → JSON or NDJSON file)
- **VS Code extension — code lens** on deployment files: `$(pulse) Status`, `$(diff) Drift`, `$(history) Audit` lenses added after Deploy (Dry Run)
- **26 new tests** for `env status` CLI wiring, multi-deployment scanning, and cache detection (`tests/strata/commands/test_commands_env.py`)

### Changed

- `strata env state` → `strata env status` (command renamed; old name removed)
- `strata audit changes --output json` data shape changed from raw array to `data: { entries: [...], count: N }`
- `strata audit resend --output json` data shape changed from `{"sent": N, "failed": N}` to standard envelope with same payload in `data`

### Fixed

- `_save_terraform_vars` previously wrote identical tfvars to every Terraform provisioner path; now iterates per-provisioner and applies per-provisioner output profile

### Documentation

- Added ADR 0019: Configurable Terraform Build Output
- Updated `docs/config/workspace.md` with full Build Output Profile reference: format modes table, `emits[]` categories, source-mode and script-mode file examples, two new worked examples
- Updated `docs/config/environment.md` with `spec.overrides.output_files[]` documentation

### VS Code Extension

- Workspace snippet updated: Terraform provisioner block now includes `output: / format:` stub with comment showing all valid modes

### Testing

- Full test suite passing: 1060+ passed, 0 failed

---

## [0.14.0] — 2026-06-26

### Added

- **Deployment Audit and Traceability (ADR 0018)**
  - Audit policy/sink configuration model for environment and configuration YAML (`AuditConfigModel`)
  - SIEM capability protocol (`ISiemSink`) and audit capability wiring in integration capabilities map
  - SIEM integrations:
    - `SentinelIntegration` (Azure Logs Ingestion API)
    - `ElkSiemIntegration` (TCP JSON and HTTP bulk)
    - `OtelSiemIntegration` (OTLP/HTTP logs)
  - Shared output directory resolver utility (`OutputWriter`) for structured and versioned outputs
  - `ManifestController` to handle deployment manifest push-to-remote workflow
  - Comprehensive SIEM integration test suite for base behavior and sink-specific behavior

### Changed

- `RunDeployCommand` now resolves and injects integration-backed SIEM sinks for deploy-log forwarding
- `AuditController.forward_to_siem()` now supports both built-in sinks and integration-backed sinks
- Deployment manifest push path now uses `ManifestController` from deploy command flow
- Output path resolution was consolidated through `OutputWriter` across audit/deploy-manifest flows
- Integration model now supports sink-specific `properties` payloads for extensible SIEM configuration

### Fixed

- Fixed deploy-manifest push test expectations and mocking path for manifest remote push
- Restored missing imports/constants that caused broad command/service test failures
- Resolved lint and type issues found during full validation (including variable naming and union-attr checks)
- Corrected docs content that caused Sphinx lexer/parsing failures in ADR documentation

### Documentation

- Added ADR 0018: Deployment Audit and Traceability
- Updated related docs content to keep Sphinx build clean

### Testing

- Full test suite passing: 3382 passed, 3 skipped, 0 failed
- All checks passing via `scripts/Check.ps1`:
  - ruff lint
  - ruff format
  - mypy
  - smoke checks
  - docs index coverage
  - sphinx build

## [0.13.0] — 2026-06-24

### Added

- **Guided Onboarding Experience (ADR 0014)**
  - `strata console` — interactive REPL session with prompt_toolkit (status, check, next, do, new, validate, graph, tools, open, reload, templates, help)
  - `GuideController` extracted from `GuideCommand` for stateful workspace analysis
  - `strata validate graph` — Mermaid dependency graph visualization with live validation status
  - `strata validate --explain` — plain-English summary of what a validated file does
  - Validation error fix suggestions with "did you mean?" for misspelled fields
  - `strata validate --path "**"` — batch validation of all workspace YAML
  - `strata sln init --list` — discover available init templates
  - `strata new --list` — shows bundles with descriptions
  - Rich rendering (panels, tables, progress indicators) in guide REPL
  - Standalone LLM skill file (`docs/skills/strata-onboarding.md`) bundled into init scaffold at `.github/skills/`
  - CI template validation test — all built-in templates validated on every run
  - `config/` formalized as reference example workspace with README annotations and CI validation
  - Contributing guide section for adding community example workspaces

### Fixed

- Template bundles: replaced invalid `type:` fields on stages with `provisioner:` (Pydantic `extra="forbid"` compliance)
- Validation error messages: `extra_forbidden` now names the offending field
- Template scaffold: `deploy.yml` wrapped in `{% raw %}` to prevent Jinja2 conflicts with GitHub Actions `${{ }}` expressions
- Template scaffold: `_substitute()` now handles both `${key}` and `{{ key }}` placeholder syntax
- Model tests: updated references from deleted `config/xyz-configuration/` to new cloud-provider examples

### Documentation

- Added ADR 0014 (Guided Onboarding and Cold-Start Experience)
- Updated CONTRIBUTING.md with example workspace contribution guidelines

---

## [0.12.0] — 2026-06-23

### Added

- **Secret generation utilities (`strata/utils/secret_generator.py`)**
  - `generate_secret(fmt, length)` — cryptographically secure generation for formats: `urlsafe`, `hex`, `alphanumeric`, `password`, `numeric`, `base64`, `uuid4`, `uuid7`
  - `mask_secret(value, show, char)` — safe masking for log/output display; moved from `commands/` into shared utils so controllers can import without violating layer rules
  - `generate_secret_command.py` and `mask_secret_command.py` converted to re-export shims

- **Auto-generated secrets (ADR 0013)**
  - `SecretGenerateSpec` on `SecretStoreModel` — declare a generator spec alongside the secret reference
  - `ValueController._resolve_secret` — generate-on-missing: if a secret is absent and `generate:` is declared, strata generates a value, writes it to the backing store, and returns it
  - Race-safe: if `set_secret` fails but a concurrent write is detected via re-read, the existing value is used without error

- **Seed-on-missing for variables and feature flags**
  - `VariableStoreModel.default` — if a variable key is absent from an integration-backed store, strata writes the declared default and returns it
  - `FeatureStoreModel.default` — same pattern for feature flags; default parsed as `"true"`/`"false"` string to boolean
  - Race-safe re-read fallback on write failure for both types

### Changed

- `ValueController._resolve_variable`, `_resolve_secret`, `_resolve_feature` — return signature extended from `(value, error)` to `(value, error, note)` to carry seed/generate annotations without polluting error lists
- `ResolvedValues.for_stage` — filters `secret_notes` alongside secrets when building a stage-scoped copy

### Fixed

- Error message for `password` format minimum length corrected from `"--length >= 4"` to `"length >= 4"` in both CLI output and test assertions

### Documentation

- `docs/guides/faq.md` — restructured as high-level explainer (what is strata, how it works with Terraform/Ansible/Helm)
- `docs/guides/config-faq.md` — new; configuration-specific questions (SSH key setup, existing Terraform state adoption, multi-stage deployment YAML, rollback procedure)
- `docs/guides/features.md` — new; practical capability overview aimed at DevOps engineers evaluating strata
- `docs/decisions/0013-auto-generated-secrets.md` — ADR for auto-generated secret design

### Testing

- `tests/strata/utils/test_utils_secret_generator.py` — new; covers all `generate_secret` formats and `mask_secret` edge cases
- `tests/strata/commands/secret/` — removed; unit tests relocated to `utils/`, CLI tests consolidated into `test_commands_secret.py`
- `tests/strata/commands/test_commands_secret.py` — renamed from `test_cli_secret.py` to match project convention
- `tests/strata/controllers/test_controllers_value.py` — updated all `_resolve_*` call sites to unpack 3-tuple `(val, err, _)`

---

## [0.11.0] — 2026-06-23

### Added

- **Promotion Strategies System (ADR 0011)**
  - Named progressions: ordered lists of environments for version promotion
  - Named strategies: policies that govern promotion waves and guardrails
  - Wave assignment on deployments via `spec.promotion.wave` (iteration, match_labels, or default)
  - Scope predicates: layer-based filtering for promotion targets
  - CLI command group: `strata promote` (start, rollback, status, matrix, history, log)
  - Activity log: `.strata/promotions/` for audit trail (gitignored)
  - Promotion-record in artifact store for state tracking

### Changed

- **Tenant Naming (ADR 0012) — BREAKING CHANGE**
  - Renamed concept: `customer` → `tenant`
  - Kind: `customer` → `tenant`; Model: `CustomerModel` → `TenantModel`; Service: `CustomerService` → `TenantService`
  - Policy: `customer_zone` → `tenant_zone`; Directory: `customers/` → `tenants/`
  - Field: `spec.customer` → `spec.tenant`; Properties: `properties.customer` → `properties.tenant`
  - Terraform variables: `customer.auto.tfvars.json` → `tenant.auto.tfvars.json`
  - Ansible hostvars: `strata_customer` → `strata_tenant`

### Migration Guide (v0.10.0 → v0.11.0)

```bash
mv customers/ tenants/
# Update kind: customer → kind: tenant and spec.customer → spec.tenant in all YAML files
```

### Documentation

- Updated all platform docs to reflect tenant terminology
- Added ADR 0011 (Promotion Strategies), ADR 0012 (Rename Customer → Tenant)
- Updated `at-scale.md` with multi-tenant design patterns

---

## [0.10.0] — 2026-06-22

### Added

- **`strata deploy show`** — new command that loads and displays the fully resolved deployment configuration (workspace, environments, variables, secrets, features) without executing. Useful for auditing what a deployment will use before running it.
- **`strata deploy list`** — new command that recursively scans a directory for `kind: deployment` YAML files and emits a flat table of all deployments with their layer fields promoted to top-level columns. Designed for CI matrix generation — pipe the JSON output directly into a GitHub Actions matrix strategy via `jq -c '.data.deployments'`.
- **`strata new` bundle templates** — `strata new <template>` now resolves bundle templates (directories) in addition to single-file templates. A bundle directory mirrors the desired output tree; `${var}` substitution is applied to both file contents and path segments. Workspace bundles override package bundles by the same name.
- **Overlap validation (`strata validate --path`)** — new `--path GLOB` option validates multiple deployment manifests for cross-manifest conflicts: duplicate artifact paths, Terraform backend collisions, and namespace overlaps across deployment layers. The non-overlap guarantee is now machine-checkable.
- **Remote reference overrides** — environment files now support `spec.overrides.remotes[]` to pin a specific remote to a version, tag, or branch for that environment only. The base reference is defined once in the configuration remote; deviations are explicit per-environment overrides. `BaseBuildCommand` checks out remotes to their effective reference at build time.
- **`OverlapController`** — new controller that orchestrates cross-manifest overlap checks (artifact paths, Terraform backends, namespaces). Used by `strata validate --path`.
- **`RepositoryController.ensure_remote_refs`** — new method that checks out all remotes in a deployment to their effective reference before a build begins.
- **`GitIntegration`** — new methods: `fetch`, `checkout`, `resolve_commit_sha` for fine-grained remote management.
- **`NamespaceType` enum** — `dedicated` vs `shared` namespace types added to the namespace model, affecting overlap validation behavior (shared namespaces are excluded from uniqueness checks).
- **Docs:** New guide section "Variable Flow: Customer Metadata → Terraform" in `docs/guides/at-scale.md` — documents the full chain from `customer.yaml spec.configuration` through environment variable stores to `TF_VAR_*` injection with three concrete patterns (tier-wide constants, per-customer overrides, CI-injected values).
- **Docs:** New section "Deploying ArgoCD ApplicationSets" in `docs/config/deployment.md` — covers the recommended pattern for managing ArgoCD via `server.additionalApplications` / `extraObjects` helm values, with full workspace + environment + override YAML examples.
- **Docs:** `strata deploy list` documented in `docs/platform/commands.md` with usage, option table, JSON output shape, and a complete GitHub Actions matrix workflow snippet.
- **Docs:** Workspace-per-layer pattern documented in `docs/guides/at-scale.md` (three workspaces: bootstrap, infrastructure, application; why one workspace per layer; 400-deployment example).

### Changed

- **`strata deploy output`** — unified output handling; stored artifact support enhanced.
- **Environment model** — `spec.overrides.remotes[]` field added with `RemoteOverrideModel` (name, reference, description). Duplicate remote names within one environment rejected at parse time.
- **`DeploymentService`** — applies remote reference overrides when resolving environments; effective reference for each remote is the environment override if present, otherwise the configuration default.

### Dependencies (GitHub Actions)

- `astral-sh/setup-uv` bumped from v2 to v7
- `actions/setup-python` bumped from v4 to v6
- `peaceiris/actions-gh-pages` bumped from v3 to v4
- `actions/checkout` bumped from v4 to v7

---

## [0.9.3] — 2026-06-20

### Added

- **HelmBuilder:** `meta.yaml` now includes chart coordinates (`chartName`, `chartVersion`, `chartRepository`) for registry-based modules. Build artifacts are fully self-contained — deployers and external tools can drive `helm upgrade` without re-reading the module spec.
- **HelmBuilder:** `spec.configuration` (module-level) is now merged into `values.yaml`. For service-less modules this creates the values file; for modules with services it merges on top as overrides.
- **HelmBuilder:** Service-less helm modules (registry chart + values file pattern) now produce `meta.yaml` correctly. Previously these modules were silently skipped.
- **HelmDeployer:** `--wait`, `--atomic`, and `--timeout 5m` flags added to `helm upgrade --install`. Deploys now block until pods are healthy and auto-rollback on failure.
- **Docs:** New guide `docs/guides/helm-modules.md` covering the full helm lifecycle (define, build, deploy, GitOps integration).

### Changed

- **HelmDeployer:** Chart coordinates are now read from `meta.yaml` instead of `module.spec.source` at deploy time. The deployer no longer depends on the module YAML for chart resolution — only the build artifact.
- **TerraformIntegration:** All integration methods (`init`, `validate`, `plan`, `apply`, `destroy`) now forward `**kwargs` to `_run_integration`, enabling `line_callback` for streaming output.
- **TerraformIntegration:** `plan()` passes `ok_returncodes={2}` when using `-detailed-exitcode`, suppressing the spurious "Integration command failed" warning for exit code 2 (success with changes).
- **BaseIntegration:** `_run_integration` accepts `ok_returncodes: Optional[set]` parameter to suppress warnings for expected non-zero exit codes.
- **Deploy verbose output:** Streaming output now prefixed with deployer tool name and `│` gutter (e.g. `terraform │ ...`), with cyan for stdout and yellow for stderr.

---

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder now skips writing variable files for empty sections (providers, topologies, resources, modules, namespaces, firewalls, DNS, networks). Previously all 9 files were written unconditionally, producing empty `strata_*.yml` files for features not configured in a deployment.
- `test_version.py` fixture replaced fragile `../../VERSION.txt` relative path with `Path(__file__)`-anchored navigation; added `.strip()` to handle trailing newline in `VERSION.txt`. Fixes version test failures in CI.
- Removed invalid `sync` input from `astral-sh/setup-uv@v8.1.0` in `install-python` action, eliminating a warning on every CI job.

---

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings (floating tags, parse errors) are now silent by default during `strata build run`. The SBOM is still generated with full fidelity — use `--verbose` or run `strata build sbom` explicitly to see advisories. Configured policies continue to evaluate component properties regardless of warning visibility.

---

## [0.9.0] — 2026-06-18

### Breaking Changes

- **Configuration YAML schema:** `spec.repositories` renamed to `spec.remotes` in configuration files (ADR 0010). Existing configuration YAML files must update the field name. Solution repos (`solution.json → spec.repositories`) are unchanged.

### Added

- Self-SBOM generation in CI (`ci-build.yml` Job 3): `strata build sbom --scan .` runs against its own source tree, producing a CycloneDX 1.6 `sbom.json` artifact (dogfooding)
- `sbom.json` artifact attached to every GitHub Release via `ci-release.yml`
- `workflow_dispatch` trigger on `ci-docs.yml` for manual doc builds and testing
- Edge image jobs now gate on SBOM job success (`needs: [build, docs, sbom]`)
- ADR 0010: Decision record for renaming configuration repositories to remotes

### Changed

- `RepositoryModel` renamed to `RemoteModel` (backwards-compatible alias retained)
- `RepositoryType` enum renamed to `RemoteType` (backwards-compatible alias retained)
- `ConfigurationService.get_repositories()` → `get_remotes()`
- `ConfigurationService.get_repo_map()` → `get_remote_map()`
- `ConfigurationModel.get_repo_map()` → `get_remote_map()`
- `SourceModel.repository` field description corrected to reference solution repos

### Fixed

- **Bug:** Workspace deep validation checked `configuration_model.spec.repositories` (manifest backends) instead of solution repos for provisioner `source.repository` references — repos registered via `strata repo add` now validate correctly

---

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow: Added `--group doc` to `uv sync` to include Sphinx dependencies
- CI docs workflow: Removed impossible `if` condition that prevented deployment on tag triggers

---

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for automated documentation deployment on release tags
- Enhanced workspace volume model with optional fields: `size`, `mount_path`, `driver`, `mode`, `configuration`

### Fixed

- Issue #109: Allow any string for topology volume type (removed restrictive enum)
- Clarified `access_mode` (container-level concurrency) vs `mode` (filesystem permissions) in volume model

### Changed

- Documentation now deployed to GitHub Pages (`https://huybrechtsxyz.github.io/strata`)
- Support link updated to GitHub Issues (`https://github.com/huybrechtsxyz/strata/issues`)
- Updated `DOCS_URL` and `SUPPORT_URL` constants in `src/strata/utils/config.py`

---

## [0.8.0] — 2026-06-17

### Added

- **CVE audit (`strata build sbom --audit`)** — vulnerability scanning step that runs after SBOM generation using a locally-installed scanner (Trivy preferred, Grype as fallback). No external API calls — the scanner runs offline against the generated `sbom.json`.
  - `--audit` flag enables scanning; no-op with a warning when no scanner is in PATH.
  - `--severity LEVEL` (default `MEDIUM`) sets the minimum severity to report (`CRITICAL` | `HIGH` | `MEDIUM` | `LOW` | `UNKNOWN`).
  - `--fail-on LEVEL` exits with code 3 when findings at or above the given severity exist. Without `--fail-on`, the audit is advisory only.
  - Console output: severity summary table + top 10 findings.
  - NDJSON output: each finding emitted as a `data` event with an `audit_finding` payload.
  - JSON/text output: `audit` key added to the result envelope with severity counts.
- **`CveScannerIntegration`** — new integration (`integrations/cve_scanner.py`) wrapping Trivy and Grype. Auto-detects whichever backend is in PATH. Exposes `scan_sbom(path, severity_threshold, timeout) → CveAuditResultModel`.
- **`CveFindingModel` / `CveAuditResultModel`** — Pydantic models for structured CVE scan results added to `models/sbom_model.py`.
- **`sbom_license` policy** — new built-in policy type that enforces license allow/deny lists on SBOM components at the `build` phase. Reads each component's `strata:license` property (set by collectors or lockfile parsers). Supports fnmatch globs (`BSD-*`, `GPL-*`). Deny always wins over allow when both lists are configured. `unknown_action` setting controls behaviour for components without license metadata (`allow` | `warn` | `deny`; default `warn`).
- **Expanded lockfile parser support** — six new built-in parsers added to `DependencyFileCollector`:
  - `NugetPackagesLockParser` — `packages.lock.json` (.NET / NuGet)
  - `PackagesConfigParser` — `packages.config` (.NET / NuGet)
  - `MavenPomParser` — `pom.xml` (Java / Maven)
  - `GradleLockParser` — `gradle.lockfile` (Java / Maven)
  - `GemfileLockParser` — `Gemfile.lock` (Ruby / gem)
  - `CargoLockParser` — `Cargo.lock` (Rust / cargo)
  - `ComposerLockParser` — `composer.lock` (PHP / Packagist)
- **Lockfile parser package refactor** — `builders/sbom/lockfile_parsers/` restructured from a single file into a package with one module per ecosystem; all parsers re-exported from `__init__.py` for backward compatibility.
- **Auto-discovery from `.strata/lockfile_parsers/`** — any `.py` file dropped in `.strata/lockfile_parsers/` at the workspace root is imported automatically before the first SBOM build. No `collectors.yaml` entry required. Files prefixed with `_` are skipped.
- **NDJSON datalines for `strata build sbom --scan`** — with `--output ndjson`, one `data` event is emitted per discovered SBOM component in addition to the terminal `complete` event.

### Changed

- `SbomBuildCommand` constructor extended with `audit`, `audit_severity`, and `fail_on` parameters wired from the new CLI flags.

---

## [0.7.0] — 2026-06-16

### Added

- **`strata policy check`** — standalone command to evaluate all declared policies for a given deployment file without running a deploy. Accepts `--file` / `-f` (required). Reports each policy result with pass/fail, enforcement level, and any violations. Exits with code 3 when one or more `deny` policies fail; exits 0 when all policies pass or only `warn`/`audit` policies are violated.
- **`strata deploy outputs`** — reads stored deployment output artifact files written by a previous `strata deploy run`. Accepts `--stage`, `--key`, `--version`, and `--all-versions`. Resolves artifacts from `{work_path}/{outputs.path}/{deployment_name}/{version}/{stage}.json`; supports filtering to a single key for scripting.
- **Deploy phase policy hook** — `RunDeployCommand._evaluate_phase_policies("deploy", …)` is now evaluated after Terraform plan succeeds and before apply runs. This extends the existing plan-phase gate so policies annotated with `phase: deploy` can block the apply step independently.
- **`ManifestOutputsReferenceModel`** — new Pydantic model that records the workspace-relative path, stage name, version, and `written_at` ISO-8601 timestamp of a stored outputs artifact. Added as `ManifestStageModel.outputs_artifact` so each stage in the deployment manifest carries a typed reference to its output file when one was written.
- **`NamingPolicy` `targets` parameter** — the `naming_pattern` built-in policy now accepts an optional `targets` list in its `configuration` block, defaulting to `["config_name"]` for full backward compatibility. Available targets: `config_name`, `deployment_name`, `stage_names`, `workspace_name`, `topology_names`, `resource_names`, `namespace_names`, `provisioner_names`, `module_names`, `volume_names`. Targets whose required service is absent from the evaluation context are silently skipped. Unknown target names produce a policy violation so misconfiguration is caught early.

### Changed

- `RunDeployCommand._evaluate_plan_policies` renamed to `_evaluate_phase_policies(phase, stage, deployer)` — the method now accepts an explicit phase string so it can serve both `plan` and `deploy` gates without duplication.

---

## [0.6.0] — 2026-06-15

### Added

- **Policy engine** — declarative deployment guardrails evaluated at validate, build, and plan phases.
  - `PolicyModel` — Pydantic model for policy declarations in `configuration.spec.policies` (`name`, `type`, `phase`, `enforcement`, `enabled`, `description`, `configuration`).
  - `BasePolicy` / `PolicyContext` / `PolicyResult` — abstract base, evaluation context dataclass, and typed result dataclass.
  - `PolicyEngine` — coordinator that instantiates built-in and custom policy types, evaluates a list of enabled policies for a given phase, and accumulates results.
  - Four built-in policy types:
    - `customer_zone` — denies deploy when the target cluster is not in an allowed customer zone.
    - `required_tags` — verifies that every namespace in the built platform artifact carries the configured required labels.
    - `naming_pattern` — validates `meta.name` of the active configuration against a required regex pattern.
    - `script` — delegates to an external script or tool (OPA, Checkov, custom) via subprocess; passes JSON context on stdin and reads violations from stdout/stderr.
  - **Validate phase hook** — `ValidateCommand` evaluates all `phase: validate` policies after structural validation; violations with `enforcement: deny` promote the command to exit code 3.
  - **Build phase hook** — `RunBuildCommand` evaluates all `phase: build` policies after a successful SBOM build step.
  - **Plan phase hook** — `RunDeployCommand` evaluates all `phase: plan` policies after Terraform plan output is available.
  - **Policies in platform artifact** — `PlatformSpecModel.policies` field carries the full policy declaration list into `platform.json` so deploy-time inspection doesn't require the source configuration.
  - **Policy results in deployment manifest** — `ManifestPolicyResultModel` captures per-policy outcomes; `DeploymentManifestSpecModel.policy_results` accumulates them across all evaluated phases, making audit data available in the signed manifest.
- **`PolicyController`** — `get_declared_policies(configuration_service)` extracts the policy list; `get_deployment_phases(deployment_service)` infers which lifecycle phases a deployment's stages can trigger via keyword matching on provisioner and topology names.
- **`strata policy list`** — introspection command that lists every policy declared in the active configuration. Columns: Name, Type, Phase, Enforcement, Enabled. `deny` enforcement highlighted red, `warn` yellow. Summary line reports enabled/disabled counts. Accepts `--file` / `-f` to load a deployment YAML and annotate the output with which lifecycle phases that deployment can trigger. Supports `--output json` / `--output text` / `--output ndjson` for machine consumption.

---

## [0.5.0] — 2026-06-12

### Added

- **SBOM generation** — CycloneDX 1.6 JSON Software Bill of Materials written automatically after every `strata build run`, stored as `sbom.json` alongside `platform.json` in the deployment build directory.
- **`strata build sbom`** — standalone command to regenerate the SBOM from an existing `platform.json` without a full rebuild.
- **Extensible collector pattern** — `BaseSbomCollector` abstract base with four built-in collectors:
  - `ContainerImageCollector` — scans `platform.spec.modules[].services[].image` for container images (`pkg:docker/…` PURLs). Floating tags (`latest`, `main`, `dev`, etc.) are flagged with a `strata:tag-stability=floating` CycloneDX property and a `WARNING` log.
  - `HelmChartCollector` — collects Helm charts from provisioners with `type: helm` (`pkg:helm/…` PURLs, with `repository_url` qualifier when a repository is set).
  - `TerraformProviderCollector` — parses `required_providers {}` blocks from `*.tf` files in the build directory via `python-hcl2` (`pkg:terraform/…` PURLs).
  - `AnsibleCollectionCollector` — reads `requirements.yml` files in the build directory for collections and roles (`pkg:ansible/…` PURLs).
- **`SbomReferenceModel`** — Pydantic model recording the SBOM path, format, SHA-256 digest, and component count. Stored on `DeploymentManifestSpecModel.artifacts.sbom`.
- **`utils/sbom_utils.py`** — pure-function PURL helpers and floating-tag detection with no dependency on the SBOM library (fully unit-testable).
- **`utils/ansible_utils.py`** — shared `find_ansible_requirements_file()` utility used by both `AnsibleCollectionCollector` and `AnsibleDeployer` to eliminate duplicated discovery logic.
- `cyclonedx-python-lib >=7.0,<9` and `packageurl-python >=0.11,<2` added as runtime dependencies.

### Changed

- `DeploymentManifestSpecModel.artifacts.sbom` field typed as `Optional[SbomReferenceModel]` (was `Optional[Dict[str, Any]]`).
- `AnsibleDeployer._get_requirements_file()` now delegates to `find_ansible_requirements_file()` from `utils/ansible_utils`.
- `strata build run` full pipeline now includes SBOM generation as step 6 (after Helm builder).

### Documentation

- Added `SbomBuilder` to `docs/platform/builders.md` — output location, collector table, floating-tag behaviour, three-phase pipeline, and extension guide.
- Added `strata build sbom` to `docs/platform/commands.md`; updated `build run` description to list all pipeline steps.
- Added `sbom_utils.py` and `ansible_utils.py` sections to `docs/platform/utilities.md`.

---

## [0.2.0] — 2026-06-09

### Added

- **`strata env status`** — show live infrastructure status per deployment stage (resources, serial, outputs from `terraform show -json`). Supports `--offline` for cached-only mode. `--all` / `--path DIR` scan multiple deployment manifests and show a one-line summary per deployment (offline/cache-based).
- **`strata env drift`** — detect drift between desired config and live infrastructure using `terraform plan -detailed-exitcode`. Reports create/update/delete/replace counts per stage.
- **`strata values set`** — write a value to its configured store backend. Dispatches to constant (print location), environment (export instruction), github (`gh secret set`), or integration backends. Supports `--value`, `--from-file`, and `--stdin` for multiline input.
- **`strata values resolve`** — diagnose value resolution paths without revealing actual values. Walks the resolution chain with checkpoints per store type. `--probe` flag attempts actual backend resolution (pass/fail only).
- **Environment module overrides** — new `spec.overrides.modules` schema in environment YAML for pinning container images, Helm chart versions, and module enabled state per environment without modifying module definitions.
  - `services` list with `name` + `image` for per-service image pinning.
  - `chart_version` field for Helm chart version overrides.
  - Optional `resource`, `namespace`, `slot_type` qualifiers for scoping when a module appears in multiple places.
  - Specificity-based matching: most specific override wins.
  - Validates: mutual exclusivity of resource/namespace, unique service names.

### Changed

- Renamed internal `commands/env/` package to `commands/envs/` to avoid collision with the reserved word `env`.
- `EnvironmentModuleOverrideModel` redesigned: `module` is now the primary key (required), `resource` is optional (was required).
- `get_module_override()` service method uses specificity scoring instead of exact tuple match.
- `get_overridden_module_keys()` returns `(module, resource, namespace, slot_type)` tuples.
- Deployment service applies module overrides by scanning all matching workspace resources rather than requiring exact resource targeting.

### Documentation

- Added `Overrides` section to `docs/config/environment.md` with full module override schema and examples.
- Added cross-reference in `docs/config/module.md` pointing to environment overrides for image pinning.
- Added `values set` and `values resolve` to `docs/platform/commands.md` and `docs/platform/workflow.md`.
- Created `.archive/releases.md` — release & version management analysis for multi-repo deployment orchestration.

---

## [0.1.1] — 2026-06-07

### Added

- **Cross-module `depends_on`** — `@module/service` syntax for dependencies between modules in the same namespace. Validated at build time. Shorthand `@module` when module name equals service name.
- `strata output` — show Terraform outputs from cache or live backend.

### Changed

- `ComposeBuilder` uses two-pass build: first pass builds a namespace service registry, second pass resolves all `depends_on` refs (intra-module and cross-module).

---

## [0.1.0] — 2026-05-14

_First real release. Everything before this was iterative scaffolding toward a working platform._

### Core Platform

- Layered architecture: `commands/` → `controllers/` → `services/` → `integrations/` → `models/` → `utils/`.
- Pydantic v2 models for all YAML document kinds: workspace, environment, deployment, configuration, resource, namespace, module, provider, firewall.
- Click CLI with flat `strata <group> <command>` structure.
- Exit codes: `0` success, `1` system failure, `2` usage error, `3` validation failure.
- Structured logging via `structlog`. Audit logging subsystem.
- Project renamed from `xyz-platform` to `strata`.

### CLI Commands

- `strata validate` — validate any YAML config file with structured error output.
- `strata build run` / `plan` / `clean` — artifact generation pipeline. `plan` is dry-run.
- `strata deploy run` / `destroy` / `status` / `health` — deployment lifecycle.
- `strata diff` — preview environment changes before deploying.
- `strata sln init` / `clean` / `status` / `export` — workspace lifecycle.
- `strata repo` — register, clone, sync external repositories.
- `strata profile` — manage environment profiles.
- `strata config` — persist CLI preferences to `.strata/cli.yaml`.
- `strata vars` — variable inspection.
- `strata log` — audit log listing and configuration.
- `strata tools` — list and check external integration availability.
- `strata schema` — export JSON schema for all document kinds.
- `--file` / `-f` option across build, deploy, validate commands (`STRATA_FILE` env var).

### Compose Builder

- Multi-service modules — `spec.services` with per-service image, ports, mounts, healthcheck, environment, `depends_on`.
- One `docker-compose.yml` per namespace. All compose modules merged into a single file.
- Service name prefixing: `{module}-{service}` (omitted when equal).
- Pass-through mode — `spec.compose_file` copies an external compose file verbatim.
- Module file copy — `spec.files` with glob, `@repo/` refs, and template substitution.
- `.env` file generated at deploy time with resolved secrets/variables.

### Integrations

- Terraform: `init` / `plan` / `apply` / `destroy` / `output` via subprocess.
- Secret resolution: Bitwarden, Azure Key Vault, HashiCorp Vault, environment variables.

### DevOps

- GitHub Actions composite actions: `setup-strata`, `validate`, `build-run`, `build-plan`, `diff`, `deploy-run`, `deploy-destroy`, `run`.
- CI workflows: `install-python`, `test-python`.
- Docker images: `Dockerfile.cli` (production), `Dockerfile.docs` (documentation site).
- Scaffold templates for `strata sln init --template <name>`.

### Documentation

- Platform docs: architecture, builders, deployers, CLI reference, getting started.
- Operator guides: cookbook, cross-env patterns, troubleshooting, FAQ.
- Config reference: all YAML document kinds.
- Shell completion (Bash, Zsh, Fish).

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
-->

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes
