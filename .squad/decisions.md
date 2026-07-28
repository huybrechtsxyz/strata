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

### 2026-06-09 — DNS record value/var/secret union
- **By:** Linus
- **Decision 1 — Union model on `DnsRecordModel`:** `value: str` (required) replaced with three optional fields: `value`, `var`, `secret`. Mutual exclusion enforced by `model_validator(mode="after")`. Mirrors `ModuleServiceEnvironmentModel.validate_exactly_one_source` — adapted for DNS (no `feature` field).
- **Decision 2 — `DnsReferencesModel` at spec level:** Holds `variables: VariableRefs` and `secrets: SecretRefs` (reusing types from `common_models`). Placed in `DnsSpecModel` as `references: Optional[DnsReferencesModel]`. Cross-reference validation (all used var/secret keys must be declared) lives at spec level via a second `model_validator(mode="after")`, because records don't have access to their parent spec.
- **Decision 3 — `var:` resolution emits `null` + warning for non-literal stores:** `_build_dns_vars()` resolves only `store: literal` variables at build time. All other stores (azure_key_vault etc.) emit `null` in the output with a warning message. Full resolution requires runtime secret injection and is out of scope for the build phase.
- **Decision 4 — Split tfvars output (`dns_secret_records`):** Records using `secret:` write `null` to `dns.auto.tfvars.json` and add their coordinates (zone, domain, record name+type) plus the secret key to a separate `dns_secret_records.auto.tfvars.json`. This allows Terraform to identify which null values require external secret injection without re-parsing the zone structure.
- **Implications:** `platform_artifact_model.py` gains `references` field on `PlatformDnsModel`. `terraform_builder.py` writes two DNS-related tfvars files. Downstream Terraform modules must handle `null` record values gracefully.

### 2026-06-09 — DNS union test design
- **By:** Livingston
- **Decision 1 — Mutual exclusion tested at `DnsRecordModel` level:** 4 tests (no source, two sources, var valid, secret valid) exercise the record-level validator directly using minimal inline payloads. No need for full file fixture for these cases.
- **Decision 2 — Cross-reference tests at `DnsModel` level:** 5 tests (undeclared var, undeclared secret, var without references block, secret without references block, var+references valid) use full `DnsModel` payloads because `validate_references_declared` runs at spec level and requires a complete model.
- **Decision 3 — `dns-standard.yaml` fixture extended (not replaced):** Added `spec.references` block and two new TXT records (`var: spf_include`, `secret: domain_verify_token`). Existing records unchanged — preserves coverage of all 9 record types and the pre-union test cases.

### 2026-06-09 — DNS union documentation approach
- **By:** Reuben
- **Decision 1 — `one of` in Required column:** For `value`, `var`, `secret` rows in the Record Fields table, the Required column reads `one of` instead of `Yes`/`No`. A blockquote note after the table reinforces mutual exclusivity. Avoids adding a separate Constraints column to a table where the constraint appears in only one row.
- **Decision 2 — `spec.references` section placed before Zone Fields:** `## spec.references Fields` is inserted immediately after `## Top-level Fields`. Readers see the `spec.references` row in the top-level table and find its expansion on the next scroll — following natural reading order. Zone → Record → References would require readers to skip past two sections.
- **Decision 3 — Preserve and extend the huybrechts.xyz example:** The existing comprehensive example (A, CNAME, dual MX, DMARC, CAA) is kept; two TXT records are modified to show `var:` and `secret:` in context. Replacing with a minimal example would sacrifice the worked real-world zone structure that operators copy-paste.

### 2026-06-12 — SBOM collector design: APPROVED WITH CONDITIONS
- **By:** Danny (architecture review)
- **Decision:** The collector pattern (sub-components of `SbomBuilder`, CycloneDX isolated to the builder, composition over inheritance) is approved subject to 10 implementation constraints. Key constraints: (1) `sbom.json` goes to `{build_path}/{deployment_name}/sbom.json`, NOT `.strata/` (upholds 2026-05-21 build-folder decision); (2) SBOM models extend `PlatformBaseModel` (extra="forbid"), not bare `BaseModel`; (3) `SbomBuilder.build()` accepts optional `platform_model` for dry-run parity with `TerraformBuilder`; (4) drain `collector.get_warnings()` into `self._messages` after each `collect()`; (5) `strata build sbom` is a `SbomBuildCommand(BaseBuildCommand)` class, not inline in `cli_builders.py`; (10) CycloneDX serialization failure is a build failure (append to `self._errors`, return `False`), not a soft warning.
- **Rationale:** Design fits the existing `BaseBuilder` surface without new layers. The `.strata/` placement was the one item that would have been a rejection if not caught.
- **Implications:** See constraints 6–9 for de-duplication of `AnsibleDeployer._get_requirements_file()`, `cyclonedx-python-lib` version pinning, `sbom_utils.py` purity (zero `builders/`/`services/` imports), and `deployment_manifest_model.py` `sbom` field typing.

### 2026-06-12 — SBOM integration validation: ISSUES FOUND (blockers before implementation)
- **By:** Basher (DevOps integrations)
- **Decision:** Two blockers must be resolved before SBOM implementation proceeds: (1) `cyclonedx-python-lib` and `packageurl-python` are NOT installed — add to `pyproject.toml` `[project.dependencies]` (`cyclonedx-python-lib>=7.0,<9`, `packageurl-python>=0.11,<2`); (2) wrong `Property` import — use `from cyclonedx.model import Property`, NOT `from cyclonedx.model.property import Property` (does not exist in v7/v8).
- **Verified APIs:** `Bom`/`Component`/`ComponentType` imports OK; `JsonV1Dot6(bom).output_as_string()` confirmed; `hcl2.load()` confirmed with `python-hcl2==8.1.2`.
- **Non-blocking data-handling issue:** All `hcl2.load()` string values carry embedded surrounding quotes — callers must `.strip('"')` (e.g. `source`, `version` under `required_providers`).
- **Open question (low priority):** Ansible role PURL scheme has no canonical standard. Recommend dot-notation `pkg:ansible/{author}.{role}@{version}` (Galaxy install-name style) over the proposed slash form.

### 2026-06-15 — Policy Engine Phase 1 implementation decisions
- **By:** Linus
- **ADR reference:** `docs/decisions/0006-policy-engine-for-deployment-guardrails.md`
- **Decision:** (1) `PolicyResult.violations` uses `field(default_factory=list)` — avoids forcing graceful-skip callers to pass an empty list; (2) use `show_plan()` (returns `(bool, Dict, List[str])`, runs `terraform show -json`), NOT the non-existent `load_plan_json()`; policy runs only when `hasattr(deployer, "show_plan")`; (3) `_evaluate_plan_policies` stays inline on `RunDeployCommand` — a policy engine is a validator, not a controller; (4) `ConfigurationSpecModel.policies` is `Optional[List[PolicyModel]] = Field(default_factory=list)` — never `None` when omitted, `Optional` kept for forward-compat explicit `null`; (5) region normalization uses `.lower().replace(" ", "")` on both sides (plan JSON `"West Europe"` vs zone config `"westeurope"`).
- **Implications:** Policy evaluation wired at the `deploy_plan_after` gate; only runs for Terraform stages.

### 2026-06-15 — Policy docs observations & follow-ups
- **By:** Reuben (docs)
- **Decision / notes:** (1) Phase 2 features (`required_tags`, `naming_pattern`, `script`) documented ahead of implementation — pin YAML examples or add `<!-- TODO: update when Phase 2 lands -->` markers to flag drift risk; (2) `customer_zone` policy references `configuration.spec.zones`, which is undocumented — follow-up task to document `spec.zones` and `spec.customers` in `docs/config/configuration.md`; (3) `audit` enforcement records to `spec.policy_results` (Phase 3, not yet implemented) — `docs/config/manifest.md` needs a new section when Phase 3 ships; (4) index placement: `platform/policies` sits after `platform/lifecycles` (readers compare policies vs lifecycle hooks) and after `validators` (lower-level infra policies build on).

### 2026-06-23 — ADR-0011 review: sound, 3 clarifications before Phase 3
- **By:** Danny (architecture review)
- **Decision:** ADR-0011 (promotion strategies) is architecturally sound and ready for implementation. Phase 1 (read-only visibility) and Phase 2 (strategy model + validation) may proceed. Phase 3 automation is BLOCKED on two clarifications: (1) **promotion override file discovery** — the deployment model requires explicit `spec.environments` paths (no auto-discovery); the ADR must specify whether `strata promote start` PATCHes the deployment YAML to append the override file or whether a glob/auto-include mechanism is planned (biggest mechanical gap); (2) **`scope: customer` definition** — must be machine-resolvable (a filter predicate); candidates: `spec.customer != null`, a `spec.layers` value, or a label match.
- **Also required:** (3) the "Percentage waves" `[10, 50, 100]` row in Key Observations is misleading — the resolved design has NO auto-selection; rename to "Multi-wave (3 iterations)" or annotate as wave COUNT.
- **Advisory (non-blocking):** `strata promote log` reads gitignored local-only `.strata/promotions/` (won't work in CI — doc note); `--id prom-20260623-001` ID generation algorithm is undefined (resolve during implementation).

### 2026-07-11 — ADR-0011 naming: keep "promotion", drop `unpromote` as a CLI verb
- **By:** Danny (architecture review / naming gut-check)
- **Requested by:** Vincent Huybrechts
- **Decision:** Keep **"promotion" / `strata promote`** as the name for the ADR-0011 concept (advancing a version-lock through ordered rings dev→test→qas→prd). It is the dominant industry term (Argo, Spinnaker, Octopus, GitLab all "promote"), reads cleanly as a command (`strata promote start`, `strata promote status`), aligns with the ring/version-lock mental model, and collides with no existing strata noun. **The one change:** the reverse-direction CLI verb must be `strata promote rollback`, NOT `strata promote unpromote` — `unpromote` is not an industry term and reads awkwardly; `rollback` is the reverse vocabulary strata already uses in the deploy surface. "Unpromotion" is allowed only as descriptive ADR prose, never as a command.
- **Rule:** Reverse operations follow user-facing vocabulary, not linguistic symmetry with the forward verb.
- **Rejected alternatives:** advance/advancement (generic), rollout (collides with k8s rolling-update), propagate (unfamiliar), release-progression (verbose; "release" already means the ADR-0017 tagging lifecycle).
- **Action for ADR owner:** No structural change — ensure every CLI example uses `promote` (forward) and `promote rollback` (reverse).

### 2026-07-16 — ADR-0018 status is partial, not accepted
- **By:** Reuben
- **Requested by:** Vincent Huybrechts
- **Decision:** Document ADR-0018 as `partial`, not `accepted`.
- **Rationale:** Repository verification confirmed the normal deploy flow writes the deploy-log but does not automatically invoke `AuditController.enrich_with_pr_data()`, `AuditController.push_to_remote()`, or `AuditController.forward_to_siem()` end-to-end. The ADR also describes a future `strata audit diff` capability that is not present in this repository, and Layer 1 PR-template process evidence appears to live outside this repo, so it cannot be verified here.
- **Implications:** Keep the ADR narrative intact, but preserve the explicit status downgrade and the short "What Still Needs To Be Done" checklist until the deploy-path wiring, CLI surface, and any external process boundaries are either implemented or the ADR scope is narrowed.

### 2026-07-28 — [OPEN FINDING, not yet a decision] Audit log records unredacted secret values via full argv logging
- **By:** Basher (DevOps Integrations)
- **Status:** Open finding — not yet actioned. Not a finalized decision; flagged for future triage.
- **Finding:** `base_command.py` logs full argv (including `--value` secret content) to the audit log via `target=" ".join(sys.argv[1:])`. Needs redaction of sensitive option values (e.g. `--value`, `--password`) before persisting to `.strata/deploy-log/` or forwarding via `strata audit resend`.
- **Context:** Discovered during secret post_generate/derive feature discussion, 2026-07-28. Option-independent — affects the existing `--value` flag today, regardless of which (if any) secret-transform option is eventually built.
- **Not yet actioned.**

## Governance

- All meaningful architectural changes require a decision entry here
- Danny triages and records decisions — other agents propose via decisions/inbox/
- Keep decisions focused on direction, not implementation detail
