# Danny — History

## Core Context

Lead / Architect for strata. Python DevOps CLI tool built with Click + Pydantic.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.

## Learnings

### 2026-07-28 — Global good/meh/ugly review, pass 1: contributed Concept + Design items to `_lesson.md`

- Scanned ADR coherence and architecture layering for the global audit Vincent kicked off; contributed Concept (C1–C6) and Design (D1–D7) items to the new root-level `_lesson.md` tracker. Collection only — no verdicts assigned yet.

### 2026-07-28 — ADR 0058 published: cross-deployment dependency gating via `spec.requires`

- Formalized the earlier discussion (below) into `docs/decisions/0058-cross-deployment-dependency-gating.md` (Status: Proposed) and added it to the ADR index. Records the `spec.requires: Optional[List[str]]` field decision, backed by the gitops-manifest status signal with `env status` as a live-state fallback.

### 2026-07-28 — Cross-deployment dependency gating: no built-in mechanism; recommend `spec.requires` over ADR-0057 gates

- Assessed whether strata can gate a lower deployment layer (zone) on an upper layer (landscape) succeeding first, across separate `kind: deployment` files. No built-in mechanism exists — `stages[].depends_on` is intra-file only, `spec.inputs.from` is unimplemented. Initially proposed an ADR-0057 `type: dependency` gate, but revised after Linus showed gates are environment-scoped/human-decision-oriented — concurred a new `spec.requires` field is the better fit. Flagged open, not actioned.

### 2026-07-28 — Secret post_generate/derive feature request: recommend docs recipe now, `derive:` spec as fallback

- Assessed a request to derive a secret from a generated one via a transform. Verdict: narrow use case — recommend Option D (documented CLI recipe: `secret put --generate` → `secret get --unmask` → `secret put --value`) now, zero new code. Recommend Option C (`derive:` spec on the secret store model) only if the pattern recurs. Option A (`post_generate` hook) is discouraged — unsafe in unattended build/deploy paths.

### 2026-07-11 — Naming: "promotion" verdict + verb/noun asymmetry rule

**Requested by:** Vincent Huybrechts (ADR 0011 name gut-check).

- **"Promotion" is the right noun.** It is the dominant industry term for ring/environment progression (Argo, Spinnaker, Octopus, GitLab environments all use "promote"). Ops readers get it instantly. `strata promote` reads well as a command. Keep it.
- **Naming rule learned — reverse operations do not have to be the linguistic inverse of the forward operation.** `unpromotion` is a documentation-grade noun but a poor CLI verb: `strata unpromote` is clumsy and not an industry term. For the CLI verb, pick the term ops people already say for the reverse action (`rollback` — it's already used elsewhere in strata's deploy vocabulary and in ADR 0011's own `strata promote rollback` example). It is fine, even preferable, for the ADR to keep "unpromotion" as the conceptual noun while the CLI exposes `strata promote rollback`. Consistency of *user-facing verbs* with industry vocabulary beats internal linguistic symmetry.
- **No noun collision.** `promote`/`promotion` does not clash with existing strata nouns (build, deploy, release, ref, lock). It sits cleanly beside them as its own command group, same shape as `deploy`.
- **Avoid `advance`/`propagate`/`rollout` for this concept** — each is either weaker (generic) or overloaded (rollout = intra-env rolling update in k8s land), which would create a *new* collision.

### 2026-04-22 — Full architecture review

**CLI:** `cli.py` is an empty shell — all 7 command groups commented out, zero active subcommands.
No command files exist in `commands/` except `base_command.py` and `cli_common.py`.
The "session" terminology in cli.py comments diverges from the "project" terminology now intended.

**Models:** Complete and solid. 16 model files covering every YAML kind. `ConfigurationModel` is the
richest — providers, topologies, layering, security, repositories all Pydantic-validated.
`ProjectModel` handles the `.strata/solution.json` workspace state file.

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

- Confirmed `.strata/` is the workspace state directory — `SOLUTION_DIR = ".strata"` in `utils/config.py`.
- `xyz init` (not `xyz project init`) — flat CLI structure per 2026-05-05 decisions.md decision.
- `strata config set|unset|list` (not `xyz set`) — confirmed in `commands/cli_config.py`.
- Workspace state file is `solution.json` (`SolutionModel`) — not `project.json`.
- `resolve_work_path()` in `utils/system.py` walks up from CWD for `.platform/` — implemented, falls back to CWD (error path not yet raised).
- CI uses composite actions: `.github/actions/install-python` (uv sync --frozen) and `.github/actions/test-python` (lint + types + pytest).
- Testing pattern: plain pytest classes (`class TestConfigSet:`) — no `unittest.TestCase`.
- copilot-instructions.md updated 2026-05-06.

### 2026-06-23 — ADR 0011 Promotion Strategies rubber-duck review

**Requested by:** Vincent Huybrechts.

**Reviewed:** `docs/decisions/0011-promotion-strategies-for-version-progression.md` — full stress-test.

**Key findings:**
1. "Percentage waves" table in Key Observations (`[10, 50, 100]`) is misleading — resolved design says wave membership is always explicit, not percentage-based. Table should use wording like "3 waves" not "10%, 50%, 100%".
2. The promotion override file mechanic (`environments/customers/{name}-promotion-override.yaml`) is sound BUT the ADR doesn't explain how this file gets into the deployment's `spec.environments` list. Deployment merge chain requires explicit file references in the deployment YAML — a new override file isn't auto-discovered.
3. No `continue` stale references found — clean.
4. `strata promote` as a new command group fits the existing pattern (same shape as `strata deploy` group with subcommands).
5. The `scope: customer` field semantics need tightening — implies only customer-layer deployments participate in waves, but never defines what "customer-layer" means mechanically (is it `spec.customer != null`? A label? A layer value?).
6. `PromotionStrategyModel` on `ConfigurationSpecModel` is a clean addition — no field conflicts.
7. `spec.promotion.wave` on deployment model is clean — new optional field, no conflicts with existing `DeploymentSpecModel` fields.
8. The `kind: promotion-record` artifact model follows existing conventions perfectly.

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

### 2026-06-09 — DNS kind architecture review

**Requested by:** Vincent Huybrechts.

**Reviewed:** 4 open architecture questions for `PlatformKind.DNS`. All resolved.

- `spec.provider`: INCLUDE as `Optional[str]`. Multi-provider DNS workspaces (INWX + Cloudflare + Route53 simultaneously) need per-zone routing without loading configuration.yaml. Validate against provider enum via `field_validator`.
- Workspace field name: `dns_zones` (`workspace.spec.dns_zones: Optional[List[WorkspaceDnsModel]]`). `dns` alone is too ambiguous; `_zones` qualifier makes the collection unit explicit.
- Merge strategy: Zone merge by name (last-wins); record merge by (name, type) RRset replacement — not per-value dedup. Matches Terraform DNS provider semantics (complete RRset replaced atomically).
- tfvars shape: APPROVED. Nested `dns_zones → attachment_name → {provider, zones: {domain → {ttl, records}}}`. Records serialized with null fields included (`exclude_none=False`) for uniform Terraform schema.

**Decision written:** `.squad/decisions/inbox/danny-dns-architecture.md`

### 2026-06-10 — Network kind architecture design

**Requested by:** Vincent Huybrechts.

**Deliverable:** Full design spec for `PlatformKind.NETWORK` written to `.archive/network-design.md`.

**Key architectural decisions:**

- `CidrSourceModel` as reusable value/var/secret union type for CIDRs (AD-NET-1). Appears in two structural positions (address_space list, subnet single), extracted to avoid duplication.
- Subnets required per network (min_length=1) (AD-NET-2). A network without subnets is unreferenceable — strata's value is the subnet registry.
- Peering as lightweight `(name, target)` reference only (AD-NET-3). Configuration is provider-specific → Terraform's job. Strata captures intent for overlap validation.
- Qualified subnet references `<network>/<subnet>` on `WorkspaceResourceModel.subnet` (AD-NET-4). Avoids ambiguity in multi-network setups. Dot notation rejected (PlatformName regex conflict).
- CIDR overlap: warning for non-peered networks, hard error for peered networks (AD-NET-5). Non-peered may legitimately overlap (isolated envs); peered overlap fails at provider level.
- CIDR validation deferred for var/secret sources (AD-NET-6). Models load without environment context; service re-validates after variable injection at build time.
- Merge strategy mirrors DNS: network merge by name (last-wins), subnet merge by `(network_name, subnet_name)` replacement, post-merge CIDR re-validation (AD-NET-7).
- No `spec.provider` field (AD-NET-9). Unlike DNS, networks are bound to a single provider via workspace topology — adding provider here would create contradictory source of truth.
- 17 touchpoints identified (comparable to DNS's 15). Two extras: `WorkspaceResourceModel.subnet` field and cross-kind reference validation.

**Design document:** `.archive/network-design.md`
**Decision written:** `.squad/decisions/inbox/danny-network-kind-design.md`

### 2026-06-10 — `strata guide` command design

**Requested by:** Vincent Huybrechts.

**Deliverable:** Full design spec for the `strata guide` command written to `.archive/guide-command-design.md`.

**Key architectural decisions:**

- Top-level `strata guide` command — NOT under `sln`. First-time users must reach it with zero prior knowledge. Buried under a lifecycle group defeats the purpose.
- `INIT_REQUIRED = False` — mirrors `StatusCommand`. Guide teaches you how to init; it cannot require init to run.
- 7 checklist phases: workspace initialized → repos registered → repos on disk → profile created → profile activated → refs registered → build artifact exists. Phases 2 (tools check) and 9 (deploy history) deferred to v2.
- Status markers: ✅ (ok), ⚠️ (partial/attention), ⬜ (pending). No ❌ in v1 — advisory only.
- "Next step" = first non-✅ phase from top. ⚠️ counts as non-done (repos 2/3 cloned still triggers a next-step hint). Phase 3 hint emits one `git clone` line per missing repo with the registered URL.
- Uses `SolutionService.load_from_json()` — never raw `json.load()`. Parse failures rendered as ⚠️ phase 1.
- Exit code always 0. Guide is advisory, never a pipeline gate.
- No `--profile` flag — always reads the active profile. A phantom-profile view would misrepresent deploy-time state.
- `ChecklistItem` / `NextStepItem` are module-local dataclasses in `show_guide_command.py` — single consumer, no shared extraction.
- Console rendering is single-pass `click.echo()` — matches StatusCommand pattern, no template engine.
- 3 new files (cli_guide.py, guide/__init__.py, guide/show_guide_command.py), 1 modified file (cli.py import + registration + `_HELP_SECTIONS`). Zero new models, zero new services.

**Design document:** `.archive/guide-command-design.md`
**Decision written:** `.squad/decisions/inbox/danny-guide-command-design.md`
